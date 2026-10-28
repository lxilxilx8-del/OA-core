"""Conservative local output policy; server limits remain server-controlled."""
import threading
import time

TEXT_LIMIT = 1600
OUTPUT_BYTES = 10 * 1024 * 1024
_LOCK = threading.Lock()
_LAST_SEND = 0.0
_PAUSED_UNTIL = 0.0


class WeixinRejected(RuntimeError):
    pass


def pause_remaining():
    return max(0.0, _PAUSED_UNTIL - time.time())


def ensure_session_active():
    if pause_remaining():
        raise WeixinRejected('微信账号凭证已失效，接口暂缓一小时；请检查扫码登录状态。')


def inspect_api_result(response):
    global _PAUSED_UNTIL
    if isinstance(response, dict) and any(response.get(k) in (-14, '-14') for k in ('ret','errcode')):
        _PAUSED_UNTIL = time.time() + 3600
        raise WeixinRejected('微信账号凭证已失效，接口暂缓一小时；结果保留在本地。')


def pace_send():
    global _LAST_SEND
    with _LOCK:
        delay = 1.0 - (time.monotonic() - _LAST_SEND)
        if delay > 0:
            time.sleep(delay)
        _LAST_SEND = time.monotonic()


def check_response(response):
    if not isinstance(response, dict) or not response:
        raise WeixinRejected('微信未返回可确认的发送结果。')
    for key in ('ret', 'errcode'):
        if response.get(key) not in (None, 0, '0'):
            # A business rejection is not retried repeatedly with the same expired context.
            raise WeixinRejected(f'微信拒绝发送（{key}={response[key]}）；结果已保留，等待下次互动。')
    return response
