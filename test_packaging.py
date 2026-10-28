import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

HERE=Path(__file__).parent
PACKAGE=HERE if (HERE/'tools/build_release.py').exists() else HERE/'XiaoA-Core-1.1.0'
spec=importlib.util.spec_from_file_location('release_builder_test',PACKAGE/'tools/build_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE)
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def allow(self,names):
        (self.root/'release-files.json').write_text(json.dumps(names),encoding='utf-8')
    def test_archive_contains_only_explicit_files_and_hashes(self):
        (self.root/'README.md').write_text('synthetic public source',encoding='utf-8')
        (self.root/'private.txt').write_text('not allowlisted',encoding='utf-8')
        self.allow(['README.md','release-files.json'])
        result=builder.build(self.root)
        self.assertEqual(result['file_count'],3)
        with zipfile.ZipFile(self.root/'dist'/result['archives'][0]['name']) as z:
            self.assertFalse(any('private.txt' in n for n in z.namelist()))
            self.assertTrue(any(n.endswith('/manifest.json') for n in z.namelist()))
    def test_private_data_is_rejected_even_if_added_to_allowlist(self):
        (self.root/'data').mkdir()
        (self.root/'data'/'clawbot_account.json').write_text('{}',encoding='utf-8')
        self.allow(['data/clawbot_account.json'])
        with self.assertRaises(ValueError):builder.build(self.root)
    def test_directory_escape_is_rejected(self):
        self.allow(['../outside.txt'])
        with self.assertRaises(ValueError):builder.build(self.root)
    def test_possible_embedded_credential_is_rejected(self):
        (self.root/'sample.txt').write_text('Bearer '+'x'*30,encoding='utf-8')
        self.allow(['sample.txt'])
        with self.assertRaises(ValueError):builder.build(self.root)


if __name__=='__main__':unittest.main()
