"""Small local runtime primitives. No model, network or subprocess side effects."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time


def atomic_write_text(path, text):
    """Replace only after a complete, flushed write in the same directory."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


class DebouncedInbox:
    """One worker; pending turns stay mergeable while a previous reply runs.

    Preserves arrival order between users and uses the latest session context.
    max_wait prevents an uninterrupted stream from deferring a turn forever.
    Pending input is memory-only, as in the original host.
    """
    def __init__(self, handler, process_lock, on_error, debounce=1.8, max_wait=8.0):
        self.handler = handler
        self.process_lock = process_lock
        self.on_error = on_error
        self.debounce = debounce
        self.max_wait = max_wait
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.states = {}
        self.worker = None
        self.closed = False

    def enqueue(self, base_url, token, owner, context, text, image='', files=None, errors=None, dedup_key=''):
        with self.changed:
            if self.closed:
                raise RuntimeError('Inbox is closed')
            now = time.monotonic()
            key = (base_url, owner)
            state = self.states.setdefault(key, {'texts': [], 'img': '', 'files': [],
                                                 'errors': [], 'first': now, 'dedup_keys': []})
            if text:
                state['texts'].append(text)
            if image:
                state['img'] = image
            state['files'].extend(files or [])
            state['errors'].extend(errors or [])
            if dedup_key:
                state['dedup_keys'].append(dedup_key)
            state['args'] = (base_url, token, owner, context)
            state['due'] = min(now + self.debounce, state['first'] + self.max_wait)
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name='xiaoa-inbox', daemon=True)
                self.worker.start()
            self.changed.notify_all()

    def _run(self):
        while True:
            with self.changed:
                while not self.closed:
                    if not self.states:
                        self.changed.wait()
                        continue
                    state = next(iter(self.states.values()))
                    delay = state['due'] - time.monotonic()
                    if delay > 0:
                        self.changed.wait(delay)
                        continue
                    break
                if self.closed:
                    return
            # Never remove a batch before acquiring the host's foreground lock.
            # New fragments can still join it during a slow prior operation.
            with self.process_lock:
                with self.changed:
                    if self.closed:
                        return
                    if not self.states:
                        continue
                    key = next(iter(self.states))
                    state = self.states[key]
                    if state['due'] > time.monotonic():
                        continue
                    self.states.pop(key)
                try:
                    self.handler(state)
                except Exception as exc:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass  # A logger failure must not permanently stop replies.

    def close(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()


class ModelCallError(RuntimeError):
    """A local model process failed; its output is not a chat reply."""
