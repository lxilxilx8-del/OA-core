"""Organize local runtime data without shipping or replacing user data."""
from pathlib import Path
import os
import shutil
import sqlite3
from contextlib import closing


def prepare_storage(directory):
    root = Path(directory)
    memory = root / 'assistant_data' / 'memory'
    for folder in (memory, root / 'logs', root / 'cache', root / 'inbound_images'):
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe storage directory')

    # SQLite backup reads WAL correctly; keep the original as a recovery copy.
    old_db = root / 'assistant_data' / 'conversation.sqlite3'
    new_db = memory / 'conversation.sqlite3'
    if old_db.exists() and not new_db.exists():
        tmp = memory / 'conversation.sqlite3.migrating'
        if tmp.exists():
            raise FileExistsError('Unfinished memory migration; inspect before retrying')
        try:
            with closing(sqlite3.connect(old_db.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(tmp)) as dest:
                    source.backup(dest)
                    if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('Memory database integrity check failed')
            os.replace(tmp, new_db)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    for old, new in ((root / 'chat_history.json', memory / 'chat_history.json'),
                     (root / 'user_memory.json', memory / 'user_memory.json'),
                     (root / 'clawbot_service.log', root / 'logs' / 'clawbot_service.log'),
                     (root / 'search_cache.json', root / 'cache' / 'search_cache.json')):
        if old.is_file() and not old.is_symlink() and not new.exists():
            shutil.copy2(old, new)
