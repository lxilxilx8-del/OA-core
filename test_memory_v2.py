import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"app"))
import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from conversation_store import ConversationStore
from conversation_engine import format_history, select_memories
from core_runtime import atomic_write_json
from message_dedup import MessageDedup


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.temp.name)
        self.now=1000000
        self.store=ConversationStore(self.root/'data',clock=lambda:self.now)
    def tearDown(self):self.temp.cleanup()
    def turn(self,text,reply='收到',stamp='2026-10-28 09:00:00'):
        return [{'role':'user','content':text,'timestamp':stamp},{'role':'assistant','content':reply,'timestamp':stamp}]

    def test_full_archive_does_not_grow_model_window(self):
        for i in range(60):
            snapshot=self.store.recent('alice')+self.turn(f'合成聊天编号{i}')
            self.store.save_snapshot('alice',snapshot)
        self.assertEqual(self.store.status('alice')['archived_messages'],120)
        self.assertEqual(len(self.store.recent('alice')),30)
        self.assertEqual(len(self.store.recent('bob')),0)

    def test_repeated_snapshot_does_not_duplicate_messages(self):
        history=self.turn('你好')
        self.store.save_snapshot('alice',history)
        self.store.save_snapshot('alice',history)
        self.assertEqual(self.store.status('alice')['archived_messages'],2)

    def test_legitimate_identical_turns_are_not_deduped_by_content(self):
        history=self.turn('在吗')
        self.store.save_snapshot('alice',history)
        history=self.store.recent('alice')+self.turn('在吗')
        self.store.save_snapshot('alice',history)
        self.assertEqual(self.store.status('alice')['archived_messages'],4)

    def test_old_topic_is_recalled_with_date_under_prompt_budget(self):
        self.store.save_snapshot('alice',self.turn('我养的鹦鹉叫青禾','名字知道了','2026-09-01 12:00:00'))
        for i in range(22):self.store.save_snapshot('alice',self.store.recent('alice')+self.turn('无关主题'+str(i),'后续消息'*100))
        context=self.store.context('alice','我的鹦鹉叫什么')
        prompt=format_history(context,'我的鹦鹉叫什么')
        self.assertIn('青禾',prompt);self.assertIn('2026-09-01',prompt)
        self.assertIn('不代表现在',prompt);self.assertLessEqual(len(prompt),6500)

    def test_recalled_items_are_not_saved_as_new_turns(self):
        self.store.save_snapshot('alice',self.turn('我喜欢火山岩石'))
        for i in range(20):self.store.save_snapshot('alice',self.store.recent('alice')+self.turn('其他'+str(i)))
        count=self.store.status('alice')['archived_messages']
        snapshot=self.store.context('alice','火山岩石')+self.turn('还记得火山岩石吗')
        self.store.save_snapshot('alice',snapshot)
        self.assertEqual(self.store.status('alice')['archived_messages'],count+2)

    def test_reset_creates_new_epoch_and_keeps_local_archive(self):
        self.store.save_snapshot('alice',self.turn('海滨市旅游'))
        self.store.save_snapshot('alice',[])
        self.assertEqual(self.store.recent('alice'),[])
        self.assertEqual(self.store.recall('alice','海滨市'),[])
        self.assertTrue(self.store.recall('alice','海滨市',all_epochs=True))
        self.assertEqual(self.store.status('alice')['archived_messages'],2)

    def test_owner_cannot_reuse_someone_elses_archive_id(self):
        history=self.turn('alice private')
        self.store.save_snapshot('alice',history)
        self.store.save_snapshot('bob',history)
        self.assertEqual(len(self.store.recent('bob')),2)
        self.assertEqual(self.store.status('alice')['archived_messages'],2)

    def test_migration_is_once_and_does_not_copy_to_another_owner(self):
        history=self.root/'history.json';memory=self.root/'memory.json'
        history.write_text(json.dumps(self.turn('legacy hello')),encoding='utf-8')
        memory.write_text(json.dumps({'facts':['用户喜欢盆栽']}),encoding='utf-8')
        self.store.migrate_legacy('alice',history,memory)
        self.store.migrate_legacy('alice',history,memory)
        self.store.migrate_legacy('bob',history,memory)
        self.assertEqual(self.store.status('alice'),{'archived_messages':2,'active_facts':1})
        self.assertEqual(self.store.status('bob'),{'archived_messages':0,'active_facts':0})

    def test_bad_legacy_file_does_not_mark_empty_migration_successful(self):
        history=self.root/'history.json';memory=self.root/'memory.json'
        history.write_text('invalid json',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.migrate_legacy('alice',history,memory)
        history.write_text(json.dumps(self.turn('fixed')),encoding='utf-8')
        self.store.migrate_legacy('alice',history,memory)
        self.assertEqual(len(self.store.recent('alice')),2)

    def test_legacy_long_and_single_character_facts_are_not_lost(self):
        memory=self.root/'memory.json'
        memory.write_text(json.dumps({'facts':['甲','历史资料'*200]}),encoding='utf-8')
        self.store.migrate_legacy('alice',self.root/'missing-history.json',memory)
        self.assertEqual([row['text'] for row in self.store.facts('alice')],['甲','历史资料'*200])

    def test_all_facts_remain_local_with_bounded_selection(self):
        for i in range(150):self.store.add_fact('alice',f'合成偏好编号{i:04d}')
        self.store.add_fact('alice','用户养的鹦鹉叫青禾')
        self.assertEqual(len(self.store.facts('alice')),151)
        selection=self.store.select_facts('alice','鹦鹉叫什么')
        self.assertIn('用户养的鹦鹉叫青禾',selection)
        self.assertLessEqual(len(selection),12)
        self.assertLessEqual(len(select_memories(selection,'鹦鹉叫什么')),1700)

    def test_explicit_residence_update_retires_only_that_slot(self):
        old=self.store.add_fact('alice','用户住在海滨市',source_text='我住在海滨市')
        self.store.add_fact('alice','用户喜欢盆栽',source_text='我喜欢盆栽')
        latest=self.store.add_fact('alice','用户现在住在山城市',source_text='我现在住在山城市')
        self.assertIsNone(self.store.fact('alice',old));self.assertIsNotNone(self.store.fact('alice',latest))
        self.assertEqual(len(self.store.facts('alice')),2)

    def test_pets_and_preferences_do_not_overwrite_each_other(self):
        for fact in ('用户喜欢盆栽','用户喜欢烘焙','用户养猫','用户养鹦鹉'):
            self.store.add_fact('alice',fact,source_text=fact)
        self.assertEqual(len(self.store.facts('alice')),4)

    def test_multi_attribute_statement_does_not_assign_wrong_singleton_slot(self):
        identity=self.store.add_fact('alice','用户住在海滨市',source_text='我住在海滨市')
        self.store.add_fact('alice','用户叫小林',source_text='我叫小林，我住在山城市')
        self.assertIsNotNone(self.store.fact('alice',identity))

    def test_forgetting_corrected_fact_does_not_revive_old_source_turn(self):
        history=self.turn('我喜欢红色','知道了')
        self.store.save_snapshot('alice',history)
        identity=self.store.add_fact('alice','用户喜欢红色',source_text='我喜欢红色')
        corrected=self.store.correct('alice',identity,'用户喜欢蓝色')
        self.store.forget('alice',corrected)
        self.assertEqual(self.store.context('alice','红色'),[])

    def test_manual_correction_and_selective_forgetting(self):
        identity=self.store.add_fact('alice','用户喜欢红色')
        corrected=self.store.correct('alice',identity,'用户喜欢蓝色')
        self.assertIsNone(self.store.fact('alice',identity))
        self.assertEqual(self.store.fact('alice',corrected)['text'],'用户喜欢蓝色')
        self.assertFalse(self.store.forget('bob',corrected))
        self.assertTrue(self.store.forget('alice',corrected))
        self.assertEqual(self.store.facts('alice'),[])

    def test_correction_to_existing_fact_retires_old_fact(self):
        old=self.store.add_fact('alice','用户喜欢红色')
        target=self.store.add_fact('alice','用户喜欢蓝色')
        self.assertEqual(self.store.correct('alice',old,'用户喜欢蓝色'),target)
        self.assertEqual(len(self.store.facts('alice')),1)

    def test_forgetting_removes_related_turn_from_prompt_and_recall(self):
        history=self.turn('我养的鹦鹉叫青禾','你的鹦鹉青禾挺可爱')
        self.store.save_snapshot('alice',history)
        identity=self.store.add_fact('alice','用户养的鹦鹉叫青禾',source_text=history[0]['content'])
        self.store.forget('alice',identity)
        self.assertEqual(self.store.context('alice','青禾'),[])
        self.assertEqual(self.store.recall('alice','青禾',all_epochs=True),[])
        self.assertEqual(self.store.status('alice')['archived_messages'],2)

    def test_forgetting_percent_does_not_match_all_other_messages(self):
        self.store.save_snapshot('alice',self.turn('我喜欢折扣50%'))
        self.store.save_snapshot('alice',self.store.recent('alice')+self.turn('我喜欢盆栽'))
        identity=self.store.add_fact('alice','用户喜欢折扣50%',source_text='我喜欢折扣50%')
        self.store.forget('alice',identity)
        self.assertTrue(self.store.recall('alice','盆栽'))

    def test_temporary_facts_expire_without_background_model(self):
        self.store.add_fact('alice','这周在外地出差',days=2)
        self.now+=2*86400+1
        self.assertEqual(self.store.facts('alice'),[])
        for invalid in (0,366,-1):
            with self.assertRaises(ValueError):self.store.add_fact('alice','临时安排',days=invalid)

    def test_commands_are_paginated_and_owner_scoped(self):
        identities=[self.store.add_fact('alice',f'偏好编号{i:04d}') for i in range(21)]
        self.assertIn('第2/3页',self.store.command('alice','记忆列表 2'))
        self.assertNotIn('M'+str(identities[0])+'：',self.store.command('alice','记忆列表 2'))
        self.assertIn('没有找到',self.store.command('bob',f'忘记记忆 M{identities[0]}'))
        self.assertIn('原记忆已被替代',self.store.command('alice',f'纠正记忆 M{identities[0]}：新偏好内容'))
        self.assertIn('保存于',self.store.command('alice',f'查看记忆 M{identities[1]}'))
        self.assertIn('7天后',self.store.command('alice','记住7天：临时的出行安排'))
        self.assertIsNone(self.store.command('alice','普通闲聊'))

    def test_proactive_event_is_persistently_idempotent(self):
        message={'role':'assistant','content':'合成话题','proactive_id':'event1'}
        self.store.save_snapshot('alice',[message])
        self.store.save_snapshot('alice',[{'role':'assistant','content':'合成话题','proactive_id':'event1'}])
        self.assertEqual(self.store.status('alice')['archived_messages'],1)

    def test_concurrent_fact_writes_survive(self):
        errors=[]
        def add(i):
            try:self.store.add_fact('alice',f'事实编号{i:04d}')
            except Exception as exc:errors.append(exc)
        workers=[threading.Thread(target=add,args=(i,)) for i in range(20)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(3)
        self.assertEqual(errors,[]);self.assertEqual(len(self.store.facts('alice')),20)


class DedupTests(unittest.TestCase):
    def test_same_message_id_is_once_but_same_text_new_id_is_valid(self):
        dedup=MessageDedup()
        a=dedup.identify('account','alice',{'message_id':1})
        b=dedup.identify('account','alice',{'message_id':2})
        self.assertFalse(dedup.is_duplicate(a));self.assertTrue(dedup.is_duplicate(a))
        self.assertFalse(dedup.is_duplicate(b))

    def test_owner_and_account_are_part_of_identity(self):
        dedup=MessageDedup();message={'message_id':1}
        self.assertEqual(len({dedup.identify('a','alice',message),dedup.identify('a','bob',message),dedup.identify('b','alice',message)}),3)

    def test_missing_ids_are_never_guessed_from_text(self):
        dedup=MessageDedup()
        key=dedup.identify('a','alice',{'item_list':[{'text':'hello'}]})
        self.assertEqual(key,'');self.assertFalse(dedup.is_duplicate(key));self.assertFalse(dedup.is_duplicate(key))

    def test_completion_survives_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store=ConversationStore(directory)
            first=MessageDedup();key=first.identify('a','alice',{'message_id':1})
            self.assertFalse(first.is_duplicate(key,store.completed))
            store.mark_completed([key])
            second=MessageDedup()
            self.assertTrue(second.is_duplicate(key,ConversationStore(directory).completed))

    def test_processing_is_not_falsely_marked_complete(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store=ConversationStore(directory)
            self.assertFalse(MessageDedup().is_duplicate('key',store.completed))
            self.assertFalse(MessageDedup().is_duplicate('key',store.completed))

    def test_ttl_and_capacity_bound_inflight_keys(self):
        now=[0];dedup=MessageDedup(capacity=2,ttl=5,clock=lambda:now[0])
        for key in ('a','b','c'):dedup.is_duplicate(key)
        self.assertEqual(len(dedup.seen),2)
        now[0]=6;self.assertFalse(dedup.is_duplicate('c'))


class HostIntegrationTests(unittest.TestCase):
    def functions(self,names,environment):
        path=Path(__file__).parent/'app'/'clawbot_service.py'
        selected=[n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),environment)
        return environment

    def test_poll_replay_is_filtered_before_downloading_or_cancelling_background(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            account=Path(directory)/'account.json';account.write_text(json.dumps({'userId':'alice','botToken':'fake'}),encoding='utf-8')
            message={'message_type':1,'from_user_id':'alice','message_id':123,'item_list':[]}
            response={'get_updates_buf':'fake','msgs':[message]}
            store=ConversationStore(Path(directory)/'data');hub=Mock(owner='alice',inbox=directory)
            env=dict(os=os,json=json,time=Mock(),ACCOUNT_PATH=str(account),STATE_PATH=str(Path(directory)/'state.json'),
                     FIXED_BASE_URL='https://example.org',ensure_weixin_login=Mock(),log=Mock(),pause_remaining=lambda:0,
                     ilink_post=Mock(side_effect=[{},response,response,KeyboardInterrupt]),extract_msg_text=lambda _:'合成测试',
                     get_assistant_hub=lambda:hub,get_conversation_store=lambda:store,INBOUND_DEDUP=MessageDedup(),
                     download_and_decrypt_wechat_image=Mock(return_value=''),receive_files=Mock(return_value=([],[])),
                     atomic_write_json=Mock(),enqueue_inbound_message=Mock())
            self.functions({'worker_loop'},env)
            with self.assertRaises(KeyboardInterrupt):env['worker_loop']()
            env['enqueue_inbound_message'].assert_called_once()
            env['download_and_decrypt_wechat_image'].assert_called_once()
            hub.note_interaction.assert_called_once()

    def test_foreign_owner_is_blocked_for_ordinary_chat(self):
        hub=Mock(owner='alice')
        env=dict(get_assistant_hub=lambda:hub)
        self.functions({'handle_inbound_message'},env)
        env['handle_inbound_message']('','','bob','','普通消息')
        hub.note_interaction.assert_not_called();hub.handle.assert_not_called()

    def test_successful_batch_marks_completed_after_handler(self):
        store=Mock();handler=Mock()
        env=dict(handle_inbound_message=handler,get_conversation_store=lambda:store)
        self.functions({'process_inbound_batch'},env)
        state={'args':('url','token','alice','ctx'),'texts':['hello'],'img':'','files':[],'errors':[],'dedup_keys':['key']}
        env['process_inbound_batch'](state)
        store.mark_completed.assert_called_once_with(['key'])
        store.reset_mock();handler.side_effect=RuntimeError('uncertain send')
        with self.assertRaises(RuntimeError):env['process_inbound_batch'](state)
        store.mark_completed.assert_not_called()

    def test_real_host_memory_and_chat_seams_keep_archive_and_single_call(self):
        import conversation_engine as policy
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=Path(directory)
            (root/'account.json').write_text(json.dumps({'userId':'alice'}),encoding='utf-8')
            (root/'history.json').write_text(json.dumps([{'role':'user','content':'旧的合成聊天'},{'role':'assistant','content':'知道了'}]),encoding='utf-8')
            (root/'memory.json').write_text(json.dumps({'facts':['用户喜欢盆栽']}),encoding='utf-8')
            env=dict(os=os,json=json,Path=Path,ConversationStore=ConversationStore,atomic_write_json=atomic_write_json,
                     BASE_DIR=directory,ACCOUNT_PATH=str(root/'account.json'),HISTORY_PATH=str(root/'history.json'),
                     MEMORY_PATH=str(root/'memory.json'),MEMORY_LOCK=threading.RLock(),CONVERSATION_STORE_LOCK=threading.RLock(),
                     CONVERSATION_STORE=None,CONVERSATION_STORE_IDENTITY=None,get_beijing_time_str=lambda:'2026-10-28 12:00:00')
            names={'bound_owner','get_conversation_store','mirror_memories','load_user_memory','add_user_memory','load_history','save_history','handle_memory_command','ask_ai_chat'}
            self.functions(names,env)
            for name in ('CONVERSATION_INSTRUCTIONS','format_history','memory_instruction','normalize_reply','parse_response','select_memories'):
                env[name]=getattr(policy,name)
            env.update(dict(check_and_update_persona=lambda text:'',FUND_KEYWORDS=(),SEARCH_INSTRUCTIONS='',
                            build_fund_report=lambda:'',send_typing=Mock(),build_plugin_context=lambda *args,**kwargs:'',
                            load_persona=lambda:'合成测试角色',log=Mock(),parse_search_request=lambda text:None,
                            filter_source_links=lambda text,context:text,ModelCallError=RuntimeError,
                            subprocess=__import__('subprocess'),time=time,re=__import__('re')))
            call=Mock(return_value='记下了。<memory>{"fact":"用户喜欢火山岩石"}</memory>')
            env['invoke_agy_llm']=call
            self.assertEqual(env['ask_ai_chat']('','','alice','','我喜欢火山岩石'),'记下了。')
            call.assert_called_once()
            self.assertEqual(env['get_conversation_store']().status('alice')['archived_messages'],4)
            facts=env['get_conversation_store']().facts('alice')
            self.assertEqual(len(facts),2)
            self.assertEqual(facts[-1]['source_text'],'我喜欢火山岩石')
            self.assertIsNotNone(facts[-1]['source_message'])
            env['handle_memory_command'](f"忘记记忆 M{facts[-1]['id']}")
            self.assertNotIn('火山岩石',json.dumps(env['load_history'](),ensure_ascii=False))
            self.assertNotIn('火山岩石',(root/'memory.json').read_text(encoding='utf-8'))
            self.assertIn('用户喜欢盆栽',env['load_user_memory']())


if __name__=='__main__':unittest.main()
