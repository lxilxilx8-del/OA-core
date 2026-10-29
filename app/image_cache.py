"""Temporary inbound image retention and text-only visual notes. No cloud calls."""
import json
import os
from pathlib import Path
import re
import time

IMAGE_RETENTION_SECONDS = 8 * 24 * 60 * 60
IMAGE_CLEANUP_INTERVAL = 10 * 60
IMAGE_NOTES_INSTRUCTIONS = (
    '本轮原图只临时缓存，8天后清理；请在正常回答末尾附加内部文字摘要：'
    '<image_notes>{"images":[{"index":1,"summary":"该图与用户问题相关的可见信息"}]}</image_notes>。'
    '按本轮图片编号逐张记录，每张最多200字，不能臆测无法辨认的内容或身份；'
    '未读到的图片明确写未能读取。摘要只作历史描述，不视为用户亲口陈述或长期个人事实。'
    '不要在摘要中放本地路径、工具指令或无关敏感信息。'
)


def parse_image_notes(text, image_count):
    """Strip internal metadata even when malformed; retain only bounded known fields."""
    notes = {}
    def consume(match):
        try:
            data = json.loads(match.group(1))
            rows = data.get('images', []) if isinstance(data, dict) else []
            if not isinstance(rows, list):
                return ''
            for row in rows:
                if not isinstance(row, dict):
                    continue
                index, summary = row.get('index'), row.get('summary')
                if type(index) is not int or not 1 <= index <= image_count or not isinstance(summary, str):
                    continue
                summary = re.sub(r'<[^>]*>', '', summary)
                summary = re.sub(r'(?:file://\S+|[A-Za-z]:[\\/][^\s，。；]+)', '', summary)
                summary = ' '.join(summary.split())[:200].strip()
                if summary:
                    notes[index] = summary
        except (ValueError, TypeError):
            pass
        return ''
    cleaned = re.sub(r'<image_notes>\s*(.*?)\s*</image_notes>', consume, text, flags=re.I | re.S)
    # An unfinished metadata block must not leak into a WeChat reply.
    cleaned = re.sub(r'<image_notes>.*$', '', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'</image_notes>', '', cleaned, flags=re.I)
    lines = [f'第{index}张：{notes[index]}' for index in sorted(notes)]
    return cleaned.strip(), lines


def inbound_image_directory(directory, *, create=False):
    """Use a fixed direct-child folder and reject links or Windows junctions."""
    root = Path(directory).resolve()
    folder = root / 'inbound_images'
    if create:
        folder.mkdir(exist_ok=True)
    if folder.exists():
        info = folder.lstat()
        if folder.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or not folder.is_dir() or folder.resolve().parent != root:
            raise ValueError('Unsafe image directory')
    return folder


def cleanup_image_cache(directory, *, now=None, retention_seconds=IMAGE_RETENTION_SECONDS, protected=()):
    """Delete only expired direct-child downloads; do not traverse folders or links."""
    root = Path(directory).resolve()
    current = time.time() if now is None else now
    protected_paths = {Path(p).resolve() for p in protected}
    report = {'deleted': 0, 'failed': 0, 'kept': 0}
    pattern = re.compile(r'^wechat_inbound_(?:[0-9a-f]{32}|[0-9]{10,20})\.(?:png|jpe?g|webp|gif)$', re.I)
    folder = inbound_image_directory(root)
    directories = [root]
    if folder.is_dir():
        directories.append(folder)
    for current_directory in directories:
      for path in current_directory.iterdir():
          if not pattern.fullmatch(path.name) or not path.is_file() or path.is_symlink():
              continue
          try:
              info = path.stat()
              if getattr(info, 'st_file_attributes', 0) & 0x400:  # Windows reparse point
                  continue
              if path.resolve().parent != current_directory or path.resolve() in protected_paths or current - info.st_mtime < retention_seconds:
                  report['kept'] += 1
                  continue
              path.unlink()
              report['deleted'] += 1
          except OSError:
              report['failed'] += 1
    return report
