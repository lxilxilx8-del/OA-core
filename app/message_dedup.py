"""Inbound ID dedup, adapted from dorokuma/wechatbridge (MIT).

Upstream ccb0770: main.py:_msg_dedup_key, _is_duplicate_msg.
Adaptations: owner/account scope, locking, TTL, and completion-key hashing.
Full upstream license is in THIRD_PARTY_WECHATBRIDGE_LICENSE.txt.
"""
from collections import OrderedDict
import hashlib
import json
import threading
import time


def message_key(msg):
    for field in ('message_id','client_id'):
        value=msg.get(field)
        if value:return f'{field}:{value}'
    items=msg.get('item_list')
    if isinstance(items,list) and items and isinstance(items[0],dict):
        value=items[0].get('msg_id')
        if value:return f'item_msg_id:{value}'
    value=msg.get('seq')
    if value:return f'seq:{value}'
    return None


class MessageDedup:
    def __init__(self, capacity=1000, ttl=600, clock=time.monotonic):
        self.seen=OrderedDict()
        self.lock=threading.Lock()
        self.capacity=capacity
        self.ttl=ttl
        self.clock=clock

    def identify(self, scope, owner, message):
        key=message_key(message)
        if key is None:return ''
        return hashlib.sha256(json.dumps([scope,owner,key],ensure_ascii=False).encode('utf-8')).hexdigest()

    def is_duplicate(self, key, completed=lambda key:False):
        if not key:return False
        with self.lock:
            now=self.clock()
            while self.seen and now-next(iter(self.seen.values()))>self.ttl:
                self.seen.popitem(last=False)
            if key in self.seen or completed(key):return True
            self.seen[key]=now
            while len(self.seen)>self.capacity:self.seen.popitem(last=False)
            return False
