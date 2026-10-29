"""Public lookup and image transport. No embedded user settings."""
import base64
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from Crypto.Cipher import AES
from web_search import search_web_context, search_allowed
BASE_DIR = os.environ.get('XIAOA_DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c'
def get_live_weather(city: str) -> str:
    """Fetches real-time weather, temperature, humidity, and UV index via wttr.in JSON API."""
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=zh"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        curr = (data.get("current_condition") or [{}])[0]
        temp_c = curr.get("temp_C", "")
        feels_c = curr.get("FeelsLikeC", "")
        humidity = curr.get("humidity", "")
        uv = curr.get("uvIndex", "")
        desc_list = curr.get("lang_zh") or curr.get("weatherDesc") or [{}]
        desc = desc_list[0].get("value", "晴朗") if desc_list else "晴朗"

        weather_days = data.get("weather") or []
        forecast_lines = []
        for d in weather_days[:2]:
            date_str = d.get("date", "")
            max_t = d.get("maxtempC", "")
            min_t = d.get("mintempC", "")
            forecast_lines.append(f"{date_str}: {min_t}°C～{max_t}°C")

        return (
            f"【实时天气插件（{city}）】当前：{desc}，气温 {temp_c}°C（体感 {feels_c}°C），"
            f"相对湿度 {humidity}%，紫外线指数 {uv}。今明预报：{'；'.join(forecast_lines)}"
        )
    except Exception:
        return ""

def pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if 1 <= pad_len <= 16 and data.endswith(bytes([pad_len] * pad_len)):
        return data[:-pad_len]
    return data

def download_and_decrypt_wechat_image(m: dict) -> str:
    """
    Checks if inbound WeChat message `m` contains an image (`type == 2`).
    If so, downloads it from WeChat CDN and decrypts AES-128-ECB, saving to disk.
    Returns the saved local image path (or "" if no image).
    """
    items = m.get("item_list") or []
    for it in items:
        if it.get("type") == 2 and it.get("image_item"):
            img_item = it["image_item"]
            media = img_item.get("media") or {}
            enc_param = media.get("encrypt_query_param")
            aes_key_b64 = media.get("aes_key") or img_item.get("aeskey")
            if not enc_param:
                continue
            try:
                dl_url = f"{CDN_BASE_URL}/download?encrypted_query_param={urllib.parse.quote(enc_param)}"
                req = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    enc_bytes = resp.read()

                # Parse AES key (either 16 raw bytes or 32-char hex string inside base64)
                key_bytes = None
                if aes_key_b64:
                    if len(aes_key_b64) == 32 and all(c in "0123456789abcdefABCDEF" for c in aes_key_b64):
                        key_bytes = bytes.fromhex(aes_key_b64)
                    else:
                        decoded = base64.b64decode(aes_key_b64)
                        if len(decoded) == 16:
                            key_bytes = decoded
                        elif len(decoded) == 32:
                            try:
                                key_bytes = bytes.fromhex(decoded.decode("ascii"))
                            except Exception:
                                key_bytes = decoded[:16]

                if key_bytes and len(enc_bytes) % 16 == 0:
                    cipher = AES.new(key_bytes, AES.MODE_ECB)
                    raw_bytes = pkcs7_unpad(cipher.decrypt(enc_bytes))
                else:
                    raw_bytes = enc_bytes

                if raw_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
                    suffix = '.png'
                elif raw_bytes.startswith(b'\xff\xd8\xff'):
                    suffix = '.jpg'
                elif raw_bytes[:4] == b'RIFF' and raw_bytes[8:12] == b'WEBP':
                    suffix = '.webp'
                elif raw_bytes[:6] in (b'GIF87a', b'GIF89a'):
                    suffix = '.gif'
                else:
                    suffix = '.jpg'
                from image_cache import inbound_image_directory
                out_path = os.path.join(inbound_image_directory(BASE_DIR, create=True), f"wechat_inbound_{uuid.uuid4().hex}{suffix}")
                with open(out_path, "wb") as f:
                    f.write(raw_bytes)
                return out_path
            except Exception:
                continue
    return ""

def download_and_decrypt_wechat_images(message: dict, errors=None) -> list:
    """Download every image item in order; preserve successes and report failures."""
    paths = []
    number = 0
    for item in message.get('item_list') or []:
        if item.get('type') != 2:
            continue
        number += 1
        path = download_and_decrypt_wechat_image({'item_list': [item]})
        if path:
            paths.append(path)
        elif errors is not None:
            errors.append(f'第{number}张图片未下载成功，请重新发送。')
    return paths

def fetch_qrcode_bytes(text_data):
    # Login URLs are encoded locally, never sent to a third-party QR service.
    import qrcode
    image=qrcode.make(text_data)
    stream=io.BytesIO()
    image.save(stream,format='PNG')
    return stream.getvalue()

def build_plugin_context(user_text: str, recent_history: list = None, user_memories: list = None) -> str:
    """
    Automatically runs relevant plugins based on user_text and returns live data
    to inject into the AI system prompt.
    """
    t = user_text.strip()
    blocks = []
    if not search_allowed(t):
        return ""

    # 1. Weather & skin/humidity trigger
    if re.search(r"天气|会.{0,3}下雨|下雨.{0,3}[吗么]|气温|室外.{0,4}(?:温度|几度)|(?:今天|明天).{0,4}穿什么|湿度", t):
        cities = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "重庆", "西安", "东莞")
        city = next((c for c in cities if c in t), "")
        if not city:
            # Use explicit location statements, never a city merely recommended by the bot.
            statements = list(user_memories or []) + [str(h.get("content", "")) for h in (recent_history or []) if h.get("role") == "user"]
            for statement in reversed(statements):
                match = re.search(r"(?:我|用户)(?:现在|目前)?(?:住在|在|位于)\s*(" + "|".join(cities) + ")", statement)
                if match:
                    city = match.group(1)
                    break
        if city:
            w = get_live_weather(city)
            if w:
                return w
        else:
            return "【天气查询】尚不知道用户要查询的城市，请简短确认城市，不默认北京，也不自行猜测。"

    # 3. Live lookup also recognizes time-sensitive topics without 'latest'.
    news = search_web_context(t, history=recent_history, max_items=4)
    if news:
        blocks.append(news)

    return "\n\n".join(blocks)
