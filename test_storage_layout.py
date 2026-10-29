import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'app'))
from storage_layout import prepare_storage


class StorageLayoutTests(unittest.TestCase):
    def test_existing_database_and_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'assistant_data').mkdir()
            db = root / 'assistant_data' / 'conversation.sqlite3'
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('create table facts(value text)')
                con.execute('insert into facts values (?)', ('synthetic',))
            (root / 'user_memory.json').write_text('{"facts": []}', encoding='utf-8')
            (root / 'clawbot_service.log').write_text('synthetic log', encoding='utf-8')
            prepare_storage(root)
            target = root / 'assistant_data' / 'memory' / 'conversation.sqlite3'
            with closing(sqlite3.connect(target)) as con:
                self.assertEqual(con.execute('select value from facts').fetchone()[0], 'synthetic')
            self.assertTrue(db.exists())
            self.assertEqual((root / 'logs' / 'clawbot_service.log').read_text(), 'synthetic log')
            (root / 'logs' / 'clawbot_service.log').write_text('new')
            prepare_storage(root)
            self.assertEqual((root / 'logs' / 'clawbot_service.log').read_text(), 'new')


if __name__ == '__main__':
    unittest.main()
