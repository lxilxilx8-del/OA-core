import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"app"))
from conversation_store import ConversationStore
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from core_runtime import DebouncedInbox, ModelCallError, atomic_write_json, atomic_write_text


class AtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temp.name)/'history.json'
        atomic_write_json(self.path, [{'content':'旧记录'}])
    def tearDown(self): self.temp.cleanup()

    def test_failed_replacement_keeps_old_history_and_cleans_temporary(self):
        with patch('core_runtime.os.replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                atomic_write_json(self.path, [{'content':'新记录'}])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), [{'content':'旧记录'}])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_unserializable_value_does_not_truncate_old_file(self):
        with self.assertRaises(TypeError): atomic_write_json(self.path, {'bad':object()})
        self.assertIn('旧记录', self.path.read_text(encoding='utf-8'))

    def test_unicode_roundtrip_and_text(self):
        atomic_write_json(self.path, {'正文':'你好\n世界'})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), {'正文':'你好\n世界'})
        atomic_write_text(self.path, '小A')
        self.assertEqual(self.path.read_text(encoding='utf-8'), '小A')


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.Lock()
        self.errors = []
        self.received = []
        self.event = threading.Event()
        def handler(state):
            self.assertTrue(self.lock.locked())
            self.received.append(state)
            self.event.set()
        self.queue = DebouncedInbox(handler, self.lock, self.errors.append, debounce=.035, max_wait=.14)
    def tearDown(self):
        self.queue.close()
        if self.queue.worker:
            self.queue.worker.join(2)
            self.assertFalse(self.queue.worker.is_alive())

    def send(self,text,owner='alice',context='ctx',image='',files=None):
        self.queue.enqueue('url','token',owner,context,text,image,files)

    def wait_count(self,count):
        until=time.monotonic()+2
        while len(self.received)<count and time.monotonic()<until:
            self.event.wait(.02); self.event.clear()
        self.assertEqual(len(self.received),count)

    def test_waiting_for_foreground_keeps_fragments_mergeable_and_latest_context(self):
        with self.lock:
            self.send('第一句',files=[{'name':'a.txt'}])
            time.sleep(.06)  # old timer would already have removed this batch
            self.send('第二句',context='new',image='image.png')
            time.sleep(.06)
            self.assertEqual(len(self.queue.states),1)
        self.wait_count(1)
        state=self.received[0]
        self.assertEqual(state['texts'],['第一句','第二句'])
        self.assertEqual(state['args'][3],'new')
        self.assertEqual(state['img'],'image.png')
        self.assertEqual(state['files'],[{'name':'a.txt'}])

    def test_other_users_keep_fifo_order_and_contents_separate(self):
        with self.lock:
            self.send('A1');self.send('B1','bob');self.send('A2')
            time.sleep(.06)
        self.wait_count(2)
        self.assertEqual([s['args'][2] for s in self.received],['alice','bob'])
        self.assertEqual([s['texts'] for s in self.received],[['A1','A2'],['B1']])

    def test_fragments_during_running_reply_form_only_one_next_turn(self):
        started=threading.Event(); release=threading.Event()
        handler=self.queue.handler
        def slow(state):
            if state['texts']==['first']:
                started.set();release.wait(2)
            handler(state)
        self.queue.handler=slow
        try:
            self.send('first'); self.assertTrue(started.wait(1))
            self.send('next');time.sleep(.06);self.send('more')
        finally: release.set()
        self.wait_count(2)
        self.assertEqual(self.received[1]['texts'],['next','more'])

    def test_worker_survives_failed_reply_without_automatic_retry(self):
        handler=self.queue.handler
        failed=threading.Event()
        def sometimes(state):
            if state['texts']==['bad']:
                failed.set();raise RuntimeError('failed')
            handler(state)
        self.queue.handler=sometimes
        self.send('bad');self.assertTrue(failed.wait(1))
        self.send('good');self.wait_count(1)
        self.assertEqual(self.received[0]['texts'],['good'])
        self.assertEqual(len(self.errors),1)

    def test_large_burst_uses_one_worker_and_does_not_lose_text(self):
        with self.lock:
            self.send('0');worker=self.queue.worker
            for i in range(1,100): self.send(str(i))
            self.assertIs(worker,self.queue.worker)
        self.wait_count(1)
        self.assertEqual(self.received[0]['texts'],list(map(str,range(100))))

    def test_continuous_input_has_bounded_debounce(self):
        start=time.monotonic()
        while time.monotonic()-start<.35 and not self.event.is_set():
            self.send('fragment');time.sleep(.015)
        self.assertTrue(self.event.is_set())
        self.assertLess(time.monotonic()-start,.35)


class ModelTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).parent/'app'/'clawbot_service.py'
        node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                  if isinstance(n,ast.FunctionDef) and n.name=='invoke_agy_llm')
        self.lock=threading.Lock()
        self.env=dict(os=os,time=time,re=re,subprocess=subprocess,AGY_CALL_LOCK=self.lock,
                      AGY_BIN_PATH=str(source),BASE_DIR=str(source.parent),MODEL_NAME='gemini-3.8-flash-medium',ModelCallError=ModelCallError,log=Mock())
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),self.env)
        self.invoke=self.env['invoke_agy_llm']

    def test_error_output_never_becomes_reply_and_lock_is_released(self):
        for code,out in [(2,b'private diagnostics'),(0,b'  ')]:
            with patch.object(subprocess,'run',return_value=Mock(returncode=code,stdout=out,stderr=b'private')) as run:
                with self.assertRaises(ModelCallError): self.invoke('user text')
                run.assert_called_once()
                self.assertFalse(self.lock.locked())
                self.env['log'].assert_not_called()

    def test_lock_wait_is_bounded_and_does_not_start_model(self):
        with self.lock, patch.object(subprocess,'run') as run:
            start=time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired): self.invoke('user text',timeout=.04)
            self.assertLess(time.monotonic()-start,.5)
            run.assert_not_called()

    def test_waiting_time_is_subtracted_from_process_timeout(self):
        fake_lock=Mock();fake_lock.acquire.return_value=True
        self.env['AGY_CALL_LOCK']=fake_lock
        with patch.object(time,'monotonic',side_effect=[100,112]), patch.object(subprocess,'run',return_value=Mock(returncode=0,stdout=b'OK',stderr=b'')) as run:
            self.assertEqual(self.invoke('hello',timeout=50),'OK')
            self.assertEqual(run.call_args.kwargs['timeout'],38)
        fake_lock.release.assert_called_once()

    def test_timeout_releases_lock_and_does_not_retry(self):
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('agy',1)) as run:
            with self.assertRaises(subprocess.TimeoutExpired): self.invoke('hello',timeout=1)
            run.assert_called_once()
        self.assertFalse(self.lock.locked())

    def test_success_keeps_hidden_launch_and_updater_suppression(self):
        with patch.dict(os.environ,{'AGY_CLI_DISABLE_AUTO_UPDATE':'1'}), patch.object(subprocess,'run',return_value=Mock(returncode=0,stdout=b'\x1b[32mOK\x1b[0m',stderr=b'')) as run:
            self.assertEqual(self.invoke('hello'),'OK')
            self.assertEqual(run.call_args.kwargs['env']['AGY_CLI_DISABLE_AUTO_UPDATE'],'true')
            self.assertEqual(os.environ['AGY_CLI_DISABLE_AUTO_UPDATE'],'1')
            self.assertEqual(run.call_args.kwargs['creationflags'],0)




if __name__=='__main__': unittest.main()
