"""Receive WeChat FILE items using the existing CDN protocol, never execute them."""
import base64
import hashlib
from pathlib import Path
import urllib.parse
import urllib.request
import uuid

from Crypto.Cipher import AES
from document_library import MAX_FILE_BYTES, SUPPORTED, safe_name

CDN = 'https://novac2c.cdn.weixin.qq.com/c2c'


def decode_key(value):
    if not isinstance(value, str):
        raise ValueError('附件缺少解密凭证。')
    if len(value) == 32:
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) == 32:
        decoded = bytes.fromhex(decoded.decode('ascii'))
    if len(decoded) != 16:
        raise ValueError('附件解密凭证格式不正确。')
    return decoded


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('附件下载发生了意外跳转。')


def _download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'XiaoA-FileAssistant/1'})
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=20) as response:
        body = response.read(MAX_FILE_BYTES + 33)
    if len(body) > MAX_FILE_BYTES + 16:
        raise ValueError('附件超过本地设定的 20 MiB 上限。')
    return body


def receive_files(message, folder, download=_download):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files, errors = [], []
    items = [i for i in (message.get('item_list') or []) if i.get('type') == 4]
    for item in items[:3]:
        info = item.get('file_item') or {}
        name = safe_name(info.get('file_name') or '未命名附件')
        try:
            if sum(p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink()) >= 256*1024*1024 - MAX_FILE_BYTES:
                raise ValueError('临时文件缓存接近 256 MiB 上限，请等旧缓存清理或先整理资料库。')
            if Path(name).suffix.lower() not in SUPPORTED:
                raise ValueError('请使用 PDF、DOCX、XLSX、CSV、TXT、MD、JSON 或 LOG 格式。')
            expected_size = int(info.get('len') or 0)
            if expected_size < 0 or expected_size > MAX_FILE_BYTES:
                raise ValueError('附件超过本地设定的 20 MiB 上限。')
            media = info.get('media') or {}
            # Pin the known CDN origin. A filename or attachment cannot make us fetch a local URL.
            if media.get('encrypt_query_param'):
                url = CDN + '/download?encrypted_query_param=' + urllib.parse.quote(media['encrypt_query_param'], safe='')
            else:
                url = media.get('full_url') or ''
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname != 'novac2c.cdn.weixin.qq.com' or parsed.username or parsed.port not in (443, None):
                    raise ValueError('附件下载地址暂不支持，请重新发送文件。')
            key = decode_key(media.get('aes_key'))
            encrypted = download(url)
            if not encrypted or len(encrypted) % 16:
                raise ValueError('附件数据不完整，请重新发送。')
            body = AES.new(key, AES.MODE_ECB).decrypt(encrypted)
            padding = body[-1]
            if not 1 <= padding <= 16 or body[-padding:] != bytes([padding]) * padding:
                raise ValueError('附件解密失败，请重新发送。')
            body = body[:-padding]
            if len(body) > MAX_FILE_BYTES or (expected_size and expected_size != len(body)):
                raise ValueError('附件大小校验失败，请重新发送。')
            expected_md5 = info.get('md5')
            if expected_md5 and hashlib.md5(body).hexdigest().lower() != str(expected_md5).lower():
                raise ValueError('附件校验失败，请重新发送。')
            path = folder / (uuid.uuid4().hex + '_' + name)
            path.write_bytes(body)
            files.append({'path': str(path), 'name': name})
        except Exception as exc:
            # Never surface CDN URL/key contents or library exception dumps.
            error = str(exc) if isinstance(exc, ValueError) else '下载或读取失败，请稍后重新发送。'
            errors.append(name + '：' + error[:140])
    if len(items) > 3:
        errors.append('每次先处理 3 个附件，其余请分批发送。')
    return files, errors
