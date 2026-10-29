import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"app"))
import ast
import base64
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from Crypto.Cipher import AES
from PIL import Image
import clawbot_plugins as plugins
from core_runtime import DebouncedInbox
import test_web_search as chat_tests


class MultiImageTests(unittest.TestCase):
    def function(self, name, env):
        source = Path(__file__).parent/'app'/'clawbot_service.py'
        node = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
        return env[name]

    def test_three_encrypted_items_are_all_downloaded_in_order(self):
        key = b'0123456789abcdef'
        messages, responses, originals = [], [], []
        for index, color in enumerate(['red', 'green', 'blue']):
            buf = io.BytesIO()
            Image.new('RGB', (12, 12), color).save(buf, format='PNG')
            raw = buf.getvalue()
            originals.append(raw)
            padding = 16 - len(raw) % 16
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = AES.new(key, AES.MODE_ECB).encrypt(raw + bytes([padding]) * padding)
            responses.append(response)
            messages.append({'type': 2, 'image_item': {'media': {
                'encrypt_query_param': 'synthetic-' + str(index), 'aes_key': base64.b64encode(key).decode()}}})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, \
                patch.object(plugins, 'BASE_DIR', directory), \
                patch.object(plugins.urllib.request, 'urlopen', side_effect=responses) as network:
            errors = []
            paths = plugins.download_and_decrypt_wechat_images({'item_list': messages}, errors=errors)
            self.assertEqual([Path(p).read_bytes() for p in paths], originals)
            self.assertTrue(all(Path(p).parent == Path(directory) / 'inbound_images' for p in paths))
            self.assertEqual(len(set(paths)), 3)
            self.assertEqual(network.call_count, 3)
            self.assertEqual(errors, [])

    def test_failed_image_is_numbered_and_other_items_are_kept(self):
        items = [{'type': 2, 'image_item': {'synthetic': i}} for i in range(3)]
        with patch.object(plugins, 'download_and_decrypt_wechat_image', side_effect=['a.png', '', 'c.png']):
            errors = []
            paths = plugins.download_and_decrypt_wechat_images({'item_list': items}, errors=errors)
        self.assertEqual(paths, ['a.png', 'c.png'])
        self.assertEqual(len(errors), 1)
        self.assertIn('第2张', errors[0])

    def test_three_messages_and_caption_reach_one_model_call(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = [str(Path(directory) / f'image-{i}.png') for i in range(3)]
            for index, path in enumerate(paths):
                Image.new('RGB', (12, 12), (index * 100, 0, 0)).save(path)
            env = chat_tests.ChatIntegrationTests().chat(['三张图有不同颜色。'])
            lock = threading.Lock()
            completed = threading.Event()
            failures = []
            replies = []
            def handler(state):
                replies.append(env['ask_ai_chat']('', '', 'alice', '', '，'.join(state['texts']), state['images']))
                completed.set()
            inbox = DebouncedInbox(handler, lock, failures.append, debounce=.02, max_wait=.15)
            try:
                with lock:
                    for path in paths:
                        inbox.enqueue('url', 'token', 'alice', 'ctx', '', image=path)
                    inbox.enqueue('url', 'token', 'alice', 'latest', '比较这三张图')
                self.assertTrue(completed.wait(2))
                self.assertEqual(failures, [])
                env['invoke_agy_llm'].assert_called_once()
                prompt = env['invoke_agy_llm'].call_args.args[0]
                self.assertIn('本轮共提供3张图片', prompt)
                self.assertEqual([prompt.index('@' + p) for p in paths], sorted(prompt.index('@' + p) for p in paths))
                for path in paths:
                    self.assertIn('@' + path, prompt)
                env['build_plugin_context'].assert_not_called()
            finally:
                inbox.close()
                inbox.worker.join(2)

    def test_one_missing_image_blocks_partial_visual_claim(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            first = Path(directory) / 'first.png'
            first.write_bytes(b'synthetic')
            env = chat_tests.ChatIntegrationTests().chat([])
            reply = env['ask_ai_chat']('', '', '', '', '比较', [str(first), str(first.parent / 'missing.png')])
            self.assertIn('第2张', reply)
            env['invoke_agy_llm'].assert_not_called()

    def test_all_private_image_paths_are_removed_from_reply(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = [str(Path(directory) / f'image-{i}.png') for i in range(3)]
            for path in paths:
                Path(path).write_bytes(b'synthetic')
            env = chat_tests.ChatIntegrationTests().chat(['看完三张了。' + ' '.join(paths)])
            reply = env['ask_ai_chat']('', '', '', '', '看看', paths)
            for path in paths:
                self.assertNotIn(path, reply)

    def test_batch_passes_all_images_and_preserves_completion_order(self):
        calls = []
        store = Mock()
        store.mark_completed.side_effect = lambda keys: calls.append('complete')
        handler = Mock(side_effect=lambda *args, **kwargs: calls.append(args[5]))
        process = self.function('process_inbound_batch', {'handle_inbound_message': handler, 'get_conversation_store': lambda: store})
        state = {'args': ('url', 'token', 'alice', 'ctx'), 'texts': ['比较'], 'img': 'last.png',
                 'images': ['first.png', 'second.png', 'last.png'], 'files': [], 'errors': [], 'dedup_keys': ['key']}
        process(state)
        self.assertEqual(calls, [state['images'], 'complete'])

    def test_image_burst_wait_is_longer_and_duplicate_path_is_not_repeated(self):
        lock = threading.Lock()
        inbox = DebouncedInbox(Mock(), lock, Mock())
        try:
            with lock:
                inbox.enqueue('url', 'token', 'alice', 'ctx', '', image=['a.png', 'b.png'])
                inbox.enqueue('url', 'token', 'alice', 'ctx', '', image=['b.png', 'c.png'])
                state = inbox.states[('url', 'alice')]
                self.assertEqual(state['images'], ['a.png', 'b.png', 'c.png'])
                self.assertGreater(state['due'] - state['first'], 3.5)
                self.assertLessEqual(state['due'] - state['first'], 8)
        finally:
            inbox.close()
            inbox.worker.join(2)


if __name__ == '__main__':
    unittest.main()
