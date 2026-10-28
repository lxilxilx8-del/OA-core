"""Offline checks for a clean public build. All model/WeChat calls are mocked."""
from pathlib import Path
import ast
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).parent
if (ROOT/'app').is_dir():
    PACKAGE=ROOT
else:
    PACKAGE=ROOT/'XiaoA-Core-1.1.0'
sys.path.insert(0,str(PACKAGE/'app'))
ISOLATION=tempfile.TemporaryDirectory(dir=ROOT)
os.environ['XIAOA_DATA_DIR']=ISOLATION.name
import clawbot_service as service
from core_runtime import ModelCallError
from assistant_hub import AssistantHub, Result
import clawbot_plugins as plugins
from document_library import DocumentLibrary, create_artifact, extract_document


class CleanReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT)
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def chat(self,replies):
        names={'invoke_agy_llm':Mock(side_effect=replies),'load_history':Mock(return_value=[]),
               'load_user_memory':Mock(return_value=[]),'load_persona':Mock(return_value=service.DEFAULT_PERSONA),
               'build_plugin_context':Mock(return_value=''),'send_typing':Mock(),
               'save_history':Mock(),'add_user_memory':Mock(),'log':Mock(),
               'search_web_context':Mock(return_value='【联网核实资料】 https://example.org/source')}
        for name,value in names.items():
            patcher=patch.object(service,name,value);patcher.start();self.addCleanup(patcher.stop)
        return names

    def test_import_does_not_load_or_create_an_account(self):
        self.assertFalse(Path(service.ACCOUNT_PATH).exists())
        self.assertFalse(Path(service.HISTORY_PATH).exists())
        self.assertEqual(service.BASE_DIR,ISOLATION.name)

    def test_first_run_check_creates_only_generic_persona(self):
        env={**os.environ,'XIAOA_DATA_DIR':str(self.root/'fresh-data'),'PYTHONDONTWRITEBYTECODE':'1'}
        result=subprocess.run([sys.executable,str(PACKAGE/'app/clawbot_service.py'),'--check'],
                              env=env,capture_output=True,text=True,encoding='utf-8',timeout=15,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('no login, model call or message sent',result.stdout)
        created=list((self.root/'fresh-data').iterdir())
        self.assertEqual([p.name for p in created],['persona.txt'])
        self.assertEqual(created[0].read_text(encoding='utf-8'),service.DEFAULT_PERSONA)

    def test_plain_chat_is_one_call_and_history_contains_only_reply(self):
        calls=self.chat(['在呢。'])
        self.assertEqual(service.ask_ai_chat('url','token','alice','ctx','在吗'),'在呢。')
        calls['invoke_agy_llm'].assert_called_once()
        self.assertEqual(calls['save_history'].call_args.args[0][-1]['content'],'在呢。')

    def test_image_skips_prefetch_and_has_image_timeout(self):
        calls=self.chat(['图里是一块红色区域。'])
        image=self.root/'sample.png';image.write_bytes(b'\x89PNG\r\n\x1a\n')
        self.assertIn('红色',service.ask_ai_chat('','','','','看这个图',str(image)))
        calls['build_plugin_context'].assert_not_called()
        self.assertEqual(calls['invoke_agy_llm'].call_args.kwargs['timeout'],90)
        self.assertIn('@'+str(image),calls['invoke_agy_llm'].call_args.args[0])

    def test_search_is_bounded_to_one_lookup_and_two_model_calls(self):
        calls=self.chat(['<web_search>{"query":"公开展会信息","time_sensitive":true}</web_search>',
                         '查到一个来源：https://example.org/source'])
        reply=service.ask_ai_chat('','','','','最近有哪些公开展会？')
        self.assertIn('example.org/source',reply)
        calls['search_web_context'].assert_called_once()
        self.assertEqual(calls['invoke_agy_llm'].call_count,2)

    def test_memory_uses_same_model_call(self):
        calls=self.chat(['知道啦。<memory>{"fact":"用户喜欢阅读科幻作品"}</memory>'])
        reply=service.ask_ai_chat('','','','','我喜欢阅读科幻作品')
        self.assertNotIn('memory',reply)
        calls['invoke_agy_llm'].assert_called_once()
        calls['add_user_memory'].assert_called_once_with('用户喜欢阅读科幻作品',source_text='我喜欢阅读科幻作品')

    def test_model_failure_does_not_send_private_diagnostics_or_save_history(self):
        calls=self.chat([ModelCallError('synthetic private diagnostics')])
        reply=service.ask_ai_chat('','','','','在吗')
        self.assertIn('没有成功',reply)
        self.assertNotIn('diagnostics',reply)
        calls['save_history'].assert_not_called()
        calls['invoke_agy_llm'].assert_called_once()

    def test_handler_drops_other_users_before_model_or_features(self):
        hub=Mock(owner='bound-owner')
        with patch.object(service,'get_assistant_hub',return_value=hub),patch.object(service,'ask_ai_chat') as ask:
            service.handle_inbound_message('','','another-user','','hello')
            hub.handle.assert_not_called();ask.assert_not_called()

    def test_file_handler_does_not_spend_an_extra_chat_call(self):
        hub=Mock(owner='alice');hub.handle.return_value=Result('文件收到。')
        with patch.object(service,'get_assistant_hub',return_value=hub),patch.object(service,'ask_ai_chat') as ask,patch.object(service,'deliver_assistant_result') as deliver:
            service.handle_inbound_message('','','alice','ctx','总结文件',inbound_files=[{'name':'sample.txt'}])
            ask.assert_not_called();deliver.assert_called_once()

    def test_fresh_hub_is_disabled_without_topics_or_creator_schedule(self):
        model=Mock()
        hub=AssistantHub(self.root,model,Mock(return_value=[]),owner='new-user')
        settings=hub.proactive.settings('new-user')
        self.assertFalse(settings['enabled'])
        self.assertEqual(settings['daily_limit'],2)
        self.assertFalse(settings['send_window'])
        self.assertEqual(hub.watcher.list('new-user'),[])
        self.assertFalse(hub.proactive.has_pending('new-user'))
        model.assert_not_called()

    def test_generated_docx_can_be_parsed_in_clean_environment(self):
        path=create_artifact(self.root,{'format':'docx','name':'sample','title':'示例',
                                        'paragraphs':['这是公开测试文字。']})
        result=extract_document(path)
        self.assertIn('公开测试文字',str(result))

    def test_plain_file_ingest_and_search_is_owner_scoped(self):
        path=self.root/'sample.txt';path.write_text('蓝色行星是一份测试资料。',encoding='utf-8')
        library=DocumentLibrary(self.root/'library')
        library.ingest(path,'alice',scope='knowledge')
        self.assertIn('蓝色行星',library.search('蓝色行星','alice'))
        self.assertFalse(library.search('蓝色行星','bob'))

    def test_login_qr_is_generated_without_network(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('must be local')):
            png=plugins.fetch_qrcode_bytes('https://example.org/synthetic-login')
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_foreign_wechat_sender_is_ignored_before_attachment_download(self):
        account=self.root/'account.json';account.write_text(json.dumps({'userId':'alice','botToken':'synthetic'}),encoding='utf-8')
        response={'get_updates_buf':'test','msgs':[{'message_type':1,'from_user_id':'bob','item_list':[]}]}
        with patch.object(service,'ACCOUNT_PATH',str(account)),patch.object(service,'STATE_PATH',str(self.root/'state.json')),patch.object(service,'ensure_weixin_login'),patch.object(service,'pause_remaining',return_value=0),patch.object(service,'log'),patch.object(service,'ilink_post',side_effect=[{},response,KeyboardInterrupt]),patch.object(service,'download_and_decrypt_wechat_image') as image,patch.object(service,'receive_files') as files:
            with self.assertRaises(KeyboardInterrupt):service.worker_loop()
            image.assert_not_called();files.assert_not_called()

    def test_public_tree_has_only_source_documents_and_generated_manifest(self):
        forbidden={'clawbot_account.json','clawbot_state.json','chat_history.json','user_memory.json',
                   'persona.txt','model_cascade.json','search_cache.json','holdings.json','deployment.json'}
        for path in PACKAGE.rglob('*'):
            if path.is_file():
                self.assertNotIn(path.name,forbidden)
                self.assertNotIn(path.suffix.lower(),{'.sqlite3','.db','.log','.png','.jpg','.ico','.exe','.lnk'})

    def test_service_has_no_old_personal_or_remote_dependencies(self):
        source=(PACKAGE/'app/clawbot_service.py').read_text(encoding='utf-8')
        for forbidden in ('dangerously-skip-permissions','fund-monitor','remote_controller','letai_emoji_engine','REMINDERS_PATH','HOLDINGS_PATH'):
            self.assertNotIn(forbidden,source)


if __name__=='__main__':
    try:unittest.main()
    finally:ISOLATION.cleanup()
