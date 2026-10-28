"""Local conversation archive and owner-scoped, manageable personal facts.

Storage is independent of the bounded model prompt. No network/model calls.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

BEIJING = timezone(timedelta(hours=8))
_STOP = {'用户','现在','最近','什么','怎么','那个','这个','一下','之前','知道','可以','今天','明天','还有','记得','我们','你们','这样','我说','你说','小a'}


def terms(text):
    result = set(re.findall(r'[a-z0-9]{2,}', str(text).lower()))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', str(text)):
        result.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return result - _STOP


def fact_slot(text):
    """Only unambiguous singleton attributes; preferences/pets stay separate."""
    text = re.sub(r'^(?:帮我)?记住[：:]\s*','',str(text).strip())
    if re.search(r'(?:我|用户)(?:现在|目前|已经|最近)?(?:住在|居住在|搬到|搬去|搬家到)', text):
        return 'residence'
    if re.search(r'^(?:我|用户)(?:的名字是|叫|名字叫)', text):
        return 'name'
    if re.search(r'(?:我|用户)(?:的)?生日(?:是|在|为)', text):
        return 'birthday'
    return ''


class ConversationStore:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory/'conversation.sqlite3'
        self.clock = clock
        self.lock = threading.RLock()
        with self.connect() as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS conversations(owner TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages(
                  id INTEGER PRIMARY KEY,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
                  role TEXT NOT NULL,content TEXT NOT NULL,stamp TEXT NOT NULL DEFAULT '',
                  created REAL NOT NULL,turn_id TEXT NOT NULL,proactive_id TEXT,
                  recall_allowed INTEGER NOT NULL DEFAULT 1);
                CREATE UNIQUE INDEX IF NOT EXISTS proactive_event ON messages(owner,proactive_id) WHERE proactive_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS messages_owner ON messages(owner,epoch,id);
                CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(tokens);
                CREATE TABLE IF NOT EXISTS facts(
                  id INTEGER PRIMARY KEY,owner TEXT NOT NULL,text TEXT NOT NULL,slot TEXT NOT NULL DEFAULT '',
                  source_text TEXT NOT NULL DEFAULT '',source_message INTEGER,
                  status TEXT NOT NULL DEFAULT 'active',created REAL NOT NULL,updated REAL NOT NULL,
                  expires REAL,supersedes INTEGER);
                CREATE INDEX IF NOT EXISTS facts_owner ON facts(owner,status,id);
                CREATE TABLE IF NOT EXISTS completed_messages(key TEXT PRIMARY KEY,completed REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=10000')
        try:
            with con: yield con
        finally: con.close()

    @staticmethod
    def _epoch(con, owner):
        con.execute('INSERT OR IGNORE INTO conversations(owner) VALUES(?)', (owner,))
        return con.execute('SELECT epoch FROM conversations WHERE owner=?', (owner,)).fetchone()[0]

    def migrate_legacy(self, owner, history_path, memory_path):
        """Import once, atomically. Bad files never become a successful empty import."""
        if not owner: raise ValueError('尚未绑定微信用户。')
        with self.lock, self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM metadata WHERE key='legacy_owner'").fetchone(): return
            history = json.loads(Path(history_path).read_text(encoding='utf-8-sig')) if Path(history_path).exists() else []
            memory = json.loads(Path(memory_path).read_text(encoding='utf-8-sig')) if Path(memory_path).exists() else {'facts':[]}
            if not isinstance(history,list) or not isinstance(memory,dict) or not isinstance(memory.get('facts',[]),list):
                raise ValueError('原有记忆文件格式异常，迁移未完成。')
            epoch = self._epoch(con,owner)
            turn = uuid.uuid4().hex
            for message in history:
                if not isinstance(message,dict) or message.get('role') not in ('user','assistant') or not isinstance(message.get('content'),str): continue
                if message['role']=='user': turn=uuid.uuid4().hex
                self._insert_message(con,owner,epoch,message,turn)
            for fact in memory.get('facts',[]):
                if isinstance(fact,str) and fact.strip():
                    self._put_fact(con,owner,fact.strip(),'',infer_legacy=True)
            con.execute("INSERT INTO metadata(key,value) VALUES('legacy_owner',?)", (owner,))

    def _insert_message(self, con, owner, epoch, item, turn):
        event = item.get('proactive_id') or None
        cursor = con.execute('INSERT OR IGNORE INTO messages(owner,epoch,role,content,stamp,created,turn_id,proactive_id) VALUES(?,?,?,?,?,?,?,?)',
                             (owner,epoch,item['role'],item['content'],str(item.get('timestamp','')),self.clock(),turn,event))
        if cursor.rowcount:
            identity=cursor.lastrowid
            con.execute('INSERT INTO message_search(rowid,tokens) VALUES(?,?)', (identity,' '.join(sorted(terms(item['content'])))))
            return identity
        return con.execute('SELECT id FROM messages WHERE owner=? AND proactive_id=?',(owner,event)).fetchone()[0]

    def save_snapshot(self, owner, messages):
        if not owner: raise ValueError('尚未绑定微信用户。')
        assigned=[]
        with self.lock, self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            epoch=self._epoch(con,owner)
            if not messages:
                con.execute('UPDATE conversations SET epoch=epoch+1 WHERE owner=?',(owner,))
                return
            turn=uuid.uuid4().hex
            for item in messages:
                if not isinstance(item,dict) or item.get('role') not in ('user','assistant') or not isinstance(item.get('content'),str):continue
                existing=None
                if isinstance(item.get('_archive_id'),int):
                    existing=con.execute('SELECT * FROM messages WHERE id=? AND owner=? AND epoch=?',
                                         (item['_archive_id'],owner,epoch)).fetchone()
                if existing and existing['role']==item['role'] and existing['content']==item['content']:
                    turn=existing['turn_id'];continue
                if item['role']=='user':turn=uuid.uuid4().hex
                identity=self._insert_message(con,owner,epoch,item,turn)
                assigned.append((item,identity))
        # Don't expose IDs until the transaction has committed.
        for item,identity in assigned:item['_archive_id']=identity

    @staticmethod
    def _message(row):
        item={'role':row['role'],'content':row['content'],'timestamp':row['stamp'],'_archive_id':row['id']}
        if row['proactive_id']:item['proactive_id']=row['proactive_id']
        return item

    def recent(self, owner, limit=30):
        with self.connect() as con:
            epoch=self._epoch(con,owner)
            rows=con.execute('SELECT * FROM messages WHERE owner=? AND epoch=? AND recall_allowed=1 ORDER BY id DESC LIMIT ?',
                             (owner,epoch,max(1,min(limit,100)))).fetchall()
            return [self._message(row) for row in reversed(rows)]

    def recall(self, owner, query, before_id=None, limit=4, all_epochs=False):
        tokens=sorted(terms(query))[:40]
        if not tokens:return []
        expression=' OR '.join('"'+token.replace('"','""')+'"' for token in tokens)
        with self.connect() as con:
            params=[expression,owner]
            where="message_search MATCH ? AND m.owner=? AND m.recall_allowed=1 AND (m.role='user' OR (m.role='assistant' AND instr(m.content, ?) > 0))"
            params.append('【本轮图片文字摘要（模型生成，仅作历史参考，非用户亲口陈述）】')
            if not all_epochs:
                where+=' AND m.epoch=?';params.append(self._epoch(con,owner))
            if before_id is not None:where+=' AND m.id<?';params.append(before_id)
            rows=con.execute('SELECT m.*,bm25(message_search) AS rank FROM message_search JOIN messages m ON m.id=message_search.rowid WHERE '+where+' ORDER BY rank,m.id DESC LIMIT ?',
                             params+[max(1,min(limit,6))]).fetchall()
            return [self._message(row) for row in sorted(rows,key=lambda r:r['id'])]

    def context(self, owner, query='', limit=30):
        recent=self.recent(owner,limit)
        recalled=self.recall(owner,query,before_id=recent[0]['_archive_id'],limit=3) if query and recent else []
        for item in recalled:item['_recalled']=True
        return recalled+recent

    def _put_fact(self, con, owner, text, source_text, expires=None, supersedes=None, infer_legacy=False):
        text=text.strip()
        if not infer_legacy and not 2<=len(text)<=500:raise ValueError('记忆内容需为2至500字。')
        existing=con.execute("SELECT id FROM facts WHERE owner=? AND text=? AND status='active' AND (expires IS NULL OR expires>?)",
                             (owner,text,self.clock())).fetchone()
        if existing:return existing['id']
        # Never use an arbitrary model key to overwrite a different personal attribute.
        slot=fact_slot(source_text or text) if source_text or infer_legacy else ''
        if slot and fact_slot(text)!=slot:slot=''
        now=self.clock()
        source_row=con.execute("SELECT id FROM messages WHERE owner=? AND role='user' AND content=? ORDER BY id DESC LIMIT 1",
                               (owner,source_text)).fetchone() if source_text else None
        if slot:
            previous=con.execute("SELECT id FROM facts WHERE owner=? AND slot=? AND status='active' ORDER BY id DESC LIMIT 1",(owner,slot)).fetchone()
            if previous and supersedes is None:supersedes=previous['id']
            con.execute("UPDATE facts SET status='superseded',updated=? WHERE owner=? AND slot=? AND status='active'",(now,owner,slot))
        if supersedes is not None:
            con.execute("UPDATE facts SET status='superseded',updated=? WHERE id=? AND owner=? AND status='active'",(now,supersedes,owner))
        cursor=con.execute('INSERT INTO facts(owner,text,slot,source_text,source_message,created,updated,expires,supersedes) VALUES(?,?,?,?,?,?,?,?,?)',
                           (owner,text,slot,source_text,source_row['id'] if source_row else None,now,now,expires,supersedes))
        return cursor.lastrowid

    def add_fact(self, owner, text, source_text='', days=None):
        if days is not None and not 1<=days<=365:raise ValueError('临时记忆有效期为1至365天。')
        with self.lock,self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            return self._put_fact(con,owner,text,source_text,self.clock()+days*86400 if days is not None else None)

    def facts(self, owner, limit=None):
        with self.connect() as con:
            sql="SELECT * FROM facts WHERE owner=? AND status='active' AND (expires IS NULL OR expires>?) ORDER BY id"
            params=[owner,self.clock()]
            if limit is not None:sql+=' DESC LIMIT ?';params.append(max(1,min(limit,1000)))
            rows=con.execute(sql,params).fetchall()
            return [dict(row) for row in (reversed(rows) if limit is not None else rows)]

    def fact(self, owner, identity):
        with self.connect() as con:
            row=con.execute("SELECT * FROM facts WHERE id=? AND owner=? AND status='active' AND (expires IS NULL OR expires>?)",
                            (identity,owner,self.clock())).fetchone()
            return dict(row) if row else None

    def select_facts(self, owner, query, limit=12):
        # Facts remain local; choose only a bounded set to pass to the old formatter.
        values=self.facts(owner)
        tokens=terms(query)
        ranked=sorted(values,key=lambda row:(len(tokens & terms(row['text'])),row['id']),reverse=True)
        selected={row['id']:row for row in values[-3:]}
        for row in ranked:
            if tokens & terms(row['text']):selected[row['id']]=row
            if len(selected)>=limit:break
        return [row['text'] for row in sorted(selected.values(),key=lambda r:r['id'])]

    def correct(self, owner, identity, text):
        with self.lock,self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old=con.execute("SELECT * FROM facts WHERE id=? AND owner=? AND status='active'",(identity,owner)).fetchone()
            if not old:return None
            if old['text']==text.strip():return identity
            duplicate=con.execute("SELECT id FROM facts WHERE owner=? AND text=? AND status='active' AND (expires IS NULL OR expires>?)",
                                  (owner,text.strip(),self.clock())).fetchone()
            if duplicate:
                con.execute("UPDATE facts SET status='superseded',updated=? WHERE owner=? AND id=?",(self.clock(),owner,identity))
                return duplicate['id']
            return self._put_fact(con,owner,text,'用户明确纠正：'+text,supersedes=identity)

    def forget(self, owner, identity):
        with self.lock,self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute("SELECT * FROM facts WHERE id=? AND owner=? AND status='active'",(identity,owner)).fetchone()
            if not row:return False
            related={row['id']:row}
            previous=row
            while previous['supersedes'] and previous['supersedes'] not in related:
                previous=con.execute('SELECT * FROM facts WHERE id=? AND owner=?',(previous['supersedes'],owner)).fetchone()
                if not previous:break
                related[previous['id']]=previous
            if row['slot']:
                related.update({r['id']:r for r in con.execute('SELECT * FROM facts WHERE owner=? AND slot=?',(owner,row['slot']))})
            sources=[]
            turn_ids=set()
            for fact in related.values():
                if fact['source_text'] and len(fact['source_text'])>=4:sources.append(fact['source_text'])
                core=re.sub(r'^(?:用户|我)(?:的)?','',fact['text'])
                if len(core)>=4:sources.append(core)
                if fact['source_message']:
                    linked=con.execute('SELECT turn_id FROM messages WHERE owner=? AND id=?',(owner,fact['source_message'])).fetchone()
                    if linked:turn_ids.add(linked['turn_id'])
            for text in sources:
                # SQL wildcard characters in user data must remain literal.
                escaped=text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                turn_ids.update(r['turn_id'] for r in con.execute("SELECT turn_id FROM messages WHERE owner=? AND content LIKE ? ESCAPE '\\'",(owner,'%'+escaped+'%')))
            for turn in turn_ids:
                con.execute('UPDATE messages SET recall_allowed=0 WHERE owner=? AND turn_id=?',(owner,turn))
            con.execute("UPDATE facts SET status='forgotten',updated=? WHERE owner=? AND id=?",(self.clock(),owner,identity))
            return True

    def completed(self, key):
        with self.connect() as con:
            return bool(con.execute('SELECT 1 FROM completed_messages WHERE key=? AND completed>?',(key,self.clock()-7*86400)).fetchone())

    def mark_completed(self, keys):
        with self.connect() as con:
            con.executemany('INSERT OR REPLACE INTO completed_messages(key,completed) VALUES(?,?)',[(key,self.clock()) for key in keys if key])
            con.execute('DELETE FROM completed_messages WHERE completed<?',(self.clock()-7*86400,))

    def status(self, owner):
        with self.connect() as con:
            archived=con.execute('SELECT count(*) FROM messages WHERE owner=?',(owner,)).fetchone()[0]
        return {'archived_messages':archived,'active_facts':len(self.facts(owner))}

    def command(self, owner, text):
        text=text.strip()
        detail=re.fullmatch(r'查看记忆\s*[Mm](\d+)',text)
        if detail:
            row=self.fact(owner,int(detail[1]))
            if not row:return '没有找到这条有效记忆。'
            created=datetime.fromtimestamp(row['created'],BEIJING).strftime('%Y-%m-%d %H:%M')
            expiry=datetime.fromtimestamp(row['expires'],BEIJING).strftime('%Y-%m-%d %H:%M') if row['expires'] else '未设置到期时间'
            return f"M{row['id']}：{row['text']}\n保存于：{created}\n有效期：{expiry}\n来源：{row['source_text'][:200] or '旧版记忆迁移，未记录原始陈述'}"
        page=re.fullmatch(r'(?:记忆列表|查看记忆|记忆|我的档案)(?:\s+(\d+))?',text)
        if page:
            values=self.facts(owner);number=max(1,int(page[1] or 1));pages=max(1,(len(values)+9)//10)
            if number>pages:return f'共有{pages}页，可发“记忆列表 1”查看。'
            lines=[f'长期记忆（第{number}/{pages}页，共{len(values)}条）：']
            for row in values[(number-1)*10:number*10]:
                expiry='（至'+datetime.fromtimestamp(row['expires'],BEIJING).strftime('%m-%d')+'）' if row['expires'] else ''
                lines.append(f"M{row['id']}：{row['text']}{expiry}")
            lines.append('可发“忘记记忆 M编号”或“纠正记忆 M编号：新内容”。')
            return '\n'.join(lines) if values else '还没有保存长期记忆。'
        if text=='清空记忆':
            return '如需移除长期记忆，请发“记忆列表”，再按编号发“忘记记忆 M编号”。“重置对话”只开始新对话。'
        if text=='记忆状态':
            status=self.status(owner)
            return f"本机已归档{status['archived_messages']}条消息，当前有效记忆{status['active_facts']}条。回答时只选取有限的相关内容。"
        forget=re.fullmatch(r'(?:忘记|删除)记忆\s*[Mm](\d+)',text)
        if forget:
            return '这条记忆已停用，相关原始对话也不再放入模型上下文。' if self.forget(owner,int(forget[1])) else '没有找到这条有效记忆。'
        correction=re.fullmatch(r'(?:纠正|修改)记忆\s*[Mm](\d+)\s*[：:]\s*(.+)',text,re.S)
        if correction:
            identity=self.correct(owner,int(correction[1]),correction[2])
            return f'已保存为 M{identity}，原记忆已被替代。' if identity else '没有找到这条有效记忆。'
        temporary=re.fullmatch(r'记住\s*(\d+)\s*天[：:]\s*(.+)',text,re.S)
        if temporary:
            identity=self.add_fact(owner,temporary[2],source_text=temporary[2],days=int(temporary[1]))
            return f'已保存为 M{identity}，{int(temporary[1])}天后不再用于回答。'
        recall=re.fullmatch(r'(?:回忆|历史检索)[：:]\s*(.+)',text,re.S)
        if recall:
            rows=self.recall(owner,recall[1],all_epochs=True,limit=5)
            return '\n'.join(['本地历史原话（只作历史参考）：']+[f"{row['timestamp'] or '时间未记录'}：{row['content'][:350]}" for row in rows]) if rows else '本地档案里暂未找到相关原话。'
        return None
