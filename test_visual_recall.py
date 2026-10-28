import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"app"))
import tempfile
from pathlib import Path
import unittest
from conversation_store import ConversationStore

class VisualRecallTests(unittest.TestCase):
    def test_visual_notes_are_recalled_without_promoting_ordinary_assistant_claims(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store=ConversationStore(directory)
            store.save_snapshot('alice',[
                {'role':'user','content':'看看图片'},
                {'role':'assistant','content':'回答。\n\n【本轮图片文字摘要（模型生成，仅作历史参考，非用户亲口陈述）】\n第1张：蓝色火山示意图。'},
                {'role':'user','content':'另一件事'},
                {'role':'assistant','content':'蓝色火山是用户住址。'}])
            result=store.recall('alice','蓝色火山')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['role'],'assistant')
            self.assertIn('模型生成',result[0]['content'])
            self.assertEqual(store.recall('bob','蓝色火山'),[])

if __name__=='__main__':unittest.main()
