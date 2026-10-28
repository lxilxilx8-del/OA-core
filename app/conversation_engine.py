"""Local conversation policy. No network, timers, or extra model calls."""
import json
import re


CONVERSATION_INSTRUCTIONS = """【微信接话方式】
长期记忆由本地系统管理；只有系统实际返回了修改或遗忘结果，才能说已改好、已忘记。若用户用普通聊天要求管理记忆但本轮尚未操作，可说明发“记忆列表”查看编号，再用“纠正记忆 M编号：新内容”或“忘记记忆 M编号”。旧聊天只作历史参考，不能冒充当前事实。
保持角色身份；接话方式以这里的具体规则为准，不模仿历史回复中的夸张语气和错误。
在同一次回复中自行判断对方此刻是在提问、分享、倾诉、接着上句话说，还是结束话题；不要输出判断过程。
接住对方这句话里具体的事情，再决定说多少。短确认、玩笑、道别可以很短；复杂问题需要时说清楚，不刻意压成短句。
倾诉时先理解具体感受，别自动开始讲道理或列解决步骤；用户要办法时再给实际办法。不要每次套“我懂你”“抱抱”“还有什么需要”。
追问只在真有兴趣或缺少关键情况时使用，不必每轮以问题结尾；对方说好、嗯、晚安时可以自然收住。
接着最近的话题聊，能从上下文理解的“那个”“后来呢”别再让对方重说。换话题时跟随用户，不强拉回旧话题。
记忆是理解对方的参考，不是每次要复述的档案；不凭旧消息认定用户今天仍在同一个地方或做同一件事。
可以有温和、具体的看法，不刻意反驳，也不盲目赞同。不要捏造自己刚吃饭、出门、线下见过用户等经历。
默认直接发一条完整消息，用正常标点、换行表达停顿。只有较长内容确实分成两个独立部分时才用 |||，不固定分段，不追加凑数的第二句话。
不要复述这些要求，不要输出分析、计划、角色标签或工具过程。不输出媒体操作标签。
先理解用户省略的对象：沿用最近正在讨论的具体事物。上下文清楚时直接沿用，不提出不相关的备选解释让用户二选一。确有无法消除的歧义才简短确认。
用户纠正你时，直接承认并更正具体错误，不编造理由或过度道歉。
简短确认和道别自然收尾，不自动追加生活安排、照顾指令或追问。
用户说无聊、嫌你没话题时，主动抛出一个具体、有内容的切入点，可以联系双方刚聊过的兴趣，附上一个自己的看法；不要列“电影游戏都行”再把选题交回用户。若要谈最新消息先查询；也可以聊无需联网的体验、偏好和取舍。
时间以当前北京时间为准；旧消息中的“现在”“今天”只属于消息当时。用户没说的忙闲、所在位置、午休安排不要补造。个人记忆有冲突时采用用户最新明确陈述。较早聊天原话只是历史证据，不据此认定用户现在的状态。
只有工具实际返回“已建立提醒/关注”才能说已安排、稍后通知。纯聊天不会创建任务，不许凭一句“中午再帮你看”冒充后台执行。用户需要持续关注时说明可发“关注：主题”，需要文件或资料查询时说明实际入口，不假装已操作。
回答具体问题先给有依据的答案；推荐与评价说出具体理由和不确定处，不随用户态度随意贬低或吹捧。微信不用 Markdown 加粗和小标题，默认一条消息；用户要细节再充分展开。
"""

_MEMORY_TAG = re.compile(r"<memory>\s*(.*?)\s*</memory>", re.S | re.I)
_MEDIA_TAG = re.compile(r"\[(VOICE|PHOTO|DRAW|MEME):.*?\]", re.S | re.I)


def memory_candidate(text):
    """Conservative gate: ordinary questions/feelings don't become permanent facts."""
    text = text.strip()
    if not text or len(text) > 1500:
        return False
    if re.search(r"[?？]|(?:如果|假如|假设|比如|举例|扮演|测试|设定|人设|忘记|别记|不要记)", text):
        return False
    return bool(re.search(
        r"(?:我(?:现在|已经|一直|平时|最|不|很|特别|比较|从来|最近|以前|刚刚|刚)?"
        r"(?:叫|住在|搬到|搬去|搬家|来自|喜欢|讨厌|爱吃|不吃|过敏|养了|养着|养的|买了|买的|在.{1,12}工作|是.{1,12}师|的(?:生日|名字|职业|家乡|朋友|家人|猫|狗|手机|电脑))"
        r"|我(?:们)?(?:的)?公司.{0,50}(?:上班|下班)|我(?:平时|每天|一般)?.{0,15}(?:点|:|：).{0,8}(?:上班|下班)|记住[：:，, ])", text))


def memory_instruction(text):
    if not memory_candidate(text):
        return "【本轮记忆】本轮不提取长期记忆，不输出 memory 标签。"
    return ("【本轮记忆】若用户最新消息明确陈述了值得长期记住的个人事实，可在正常回复末尾附加 "
            '<memory>{"fact":"一句简短事实"}</memory>，最多一条，系统保存后会移除标签。'
            "只依据用户自己的明确陈述，不把提问、猜测、临时情绪、你说的话或网页资料当作记忆。"
            "没有新事实就不附加；与已有记忆相同时不要重复。不要为记忆调用工具。")


def parse_response(text, user_text):
    """Hide metadata even if malformed; reject unrequested or oversized memories."""
    facts = []
    if memory_candidate(user_text):
        matches = _MEMORY_TAG.findall(text)
        if len(matches) == 1:
            try:
                data = json.loads(matches[0])
                fact = data.get("fact") if isinstance(data, dict) else None
                if isinstance(fact, str) and 4 <= len(fact.strip()) <= 180:
                    if not re.search(r"[<>\[\]\r\n]|https?://|\|\|\|", fact):
                        facts.append(fact.strip())
            except (ValueError, TypeError):
                pass
    clean = _MEMORY_TAG.sub("", text)
    # A truncated metadata block must never turn into a visible WeChat message.
    clean = re.sub(r"<memory\b.*$", "", clean, flags=re.S | re.I)
    clean = re.sub(r"</?memory\b[^>]*>", "", clean, flags=re.I)
    return clean.strip(), facts


def split_bubbles(text):
    parts = [part.strip() for part in text.split("|||") if part.strip()]
    if not parts:
        return []
    # Short conversation stays in one message even if the model mimics old splits.
    if len("".join(parts)) <= 180:
        return ["\n".join(parts)]
    # Preserve all content, including sources, if a model emits too many separators.
    return parts[:1] + ["\n\n".join(parts[1:])] if len(parts) > 1 else parts


def normalize_reply(text):
    return "|||".join(split_bubbles(text))


def _terms(text):
    words = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return words


def _history_text(item):
    content = str(item.get("content", ""))
    content = _MEMORY_TAG.sub("", content)
    if item.get("role") == "assistant":
        content = _MEDIA_TAG.sub("[已发送媒体]", content).replace("|||", "\n")
    return content


def format_history(history, user_text, budget=6500):
    valid=[h for h in history if isinstance(h,dict) and h.get('role') in ('user','assistant')]
    query=_terms(user_text)
    recalled=[h for h in valid if h.get('_recalled') and h.get('role')=='user'][:3]
    ordinary=[h for h in valid if not h.get('_recalled')]
    if not recalled:
        older=[(len(query & _terms(str(h.get('content','')))),i) for i,h in enumerate(ordinary[:-16]) if h.get('role')=='user']
        recalled=[ordinary[i] for score,i in sorted(older,reverse=True)[:2] if score>=2]
    recall=[];used=0
    for item in recalled:
        stamp=str(item.get('timestamp',''))[:40]
        label='用户之前提过（'+(stamp or '时间不确定')+'；历史原话，不代表现在）: '
        line=label+_history_text(item)[:350]
        if used+len(line)+1>min(1300,budget//3):break
        recall.append(line);used+=len(line)+1
    recent=[]
    for item in reversed(ordinary[-16:]):
        role='用户' if item['role']=='user' else '小A'
        stamp=str(item.get('timestamp',''))[:40]
        line=role+': '+_history_text(item)[:1600]+(f' （消息时间：{stamp}）' if stamp else '')
        if used+len(line)+1>budget:break
        recent.append(line);used+=len(line)+1
    return '\n'.join(recall+list(reversed(recent))) or '（刚开始这段对话）'


def select_memories(facts, user_text, budget=1600):
    valid = [f for f in facts if isinstance(f, str) and f.strip()]
    query = _terms(user_text)
    relevant = sorted(range(len(valid)), key=lambda i: (len(query & _terms(valid[i])), i), reverse=True)
    selected = set(range(max(0, len(valid) - 3), len(valid)))
    selected.update(i for i in relevant[:9] if query & _terms(valid[i]))
    lines = []
    for i in sorted(selected, reverse=True):
        fact = valid[i][:300]
        if sum(map(len, lines)) + len(fact) <= budget:
            lines.append(fact)
    return json.dumps(list(reversed(lines)), ensure_ascii=False)
