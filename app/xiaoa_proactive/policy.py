# SPDX-License-Identifier: AGPL-3.0-only
# Adapted from Pancakes-Labs/astrbot_plugin_proactive_chat, commit
# 77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5.
# Sources: utils/time_utils.py:is_quiet_time,
# core/services/scheduler_service.py:SchedulerService.compute_next_trigger,
# core/features/gating_feature.py:GatingStage.handle (unanswered limit).
# Changes: inject clock/random/timezone; remove AstrBot container and logging.
from datetime import datetime, timedelta, timezone
import json
import math
import random

BEIJING = timezone(timedelta(hours=8))


def is_quiet_time(quiet_hours_str, now):
    start_str, end_str = quiet_hours_str.split('-')
    start_hour, end_hour = int(start_str), int(end_str)
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
        raise ValueError('Invalid quiet hours')
    if start_hour <= end_hour:
        return start_hour <= now.hour < end_hour
    return now.hour >= start_hour or now.hour < end_hour


def compute_next_trigger(schedule_conf, now, randint=random.randint, tz=BEIJING):
    min_interval = int(schedule_conf.get('min_interval_minutes', 30)) * 60
    max_interval = max(min_interval, int(schedule_conf.get('max_interval_minutes', 900)) * 60)
    random_interval = randint(min_interval, max_interval)
    scheduled_at = now
    next_trigger_time = scheduled_at + random_interval
    run_date = datetime.fromtimestamp(next_trigger_time, tz=tz)
    return (run_date, scheduled_at, min_interval, max_interval, random_interval, next_trigger_time)


def reached_unanswered_limit(unanswered_count, max_unanswered):
    return max_unanswered > 0 and unanswered_count >= max_unanswered


# Host-specific allowed-work-window adapter; not part of the upstream algorithms.
def normalize_send_window(value):
    if value is None or value == '':
        return ''
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data,dict):
        raise ValueError('工作时段配置需为对象。')
    days, periods = data.get('weekdays'), data.get('periods')
    if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
        raise ValueError('工作日需用0至6表示周一至周日。')
    if not isinstance(periods,list) or not periods:
        raise ValueError('请提供至少一个工作时段。')
    normalized=[]
    for pair in periods:
        if not isinstance(pair,(list,tuple)) or len(pair)!=2:
            raise ValueError('工作时段需提供开始和结束时间。')
        numbers=[]
        for item in pair:
            parts=str(item).split(':')
            if len(parts)!=2 or not all(p.isdigit() for p in parts):
                raise ValueError('时间格式为 HH:MM。')
            hour,minute=map(int,parts)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError('时间超出范围。')
            numbers.append(hour*60+minute)
        if numbers[0]>=numbers[1]:
            raise ValueError('工作时段的结束时间需晚于开始时间。')
        normalized.append([f'{n//60:02d}:{n%60:02d}' for n in numbers])
    return json.dumps({'weekdays':sorted(set(days)),'periods':sorted(normalized)},ensure_ascii=False)


def allowed_to_send(value, now):
    if value is None or value == '':
        return True
    try:
        data=json.loads(normalize_send_window(value))
        minute=now.hour*60+now.minute
        return now.weekday() in data['weekdays'] and any(
            int(start[:2])*60+int(start[3:]) <= minute < int(end[:2])*60+int(end[3:])
            for start,end in data['periods'])
    except (ValueError,TypeError,AttributeError):
        return False


def random_time_in_window(after, value, randint=random.randint):
    """Choose a point inside the next available window, rather than its opening edge."""
    if not value:
        return after
    data=json.loads(normalize_send_window(value))
    date=datetime.fromtimestamp(after,BEIJING)
    for offset in range(8):
        day=date.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=offset)
        if day.weekday() not in data['weekdays']:
            continue
        for start,end in data['periods']:
            lo=day.replace(hour=int(start[:2]),minute=int(start[3:])).timestamp()
            hi=day.replace(hour=int(end[:2]),minute=int(end[3:])).timestamp()-1
            lo=max(math.ceil(after),int(lo))
            if lo<=hi:
                return float(randint(int(lo),int(hi)))
    raise ValueError('未来一周没有可用的工作时段。')


def describe_send_window(value):
    if not value:
        return '23:00—09:00 安静'
    data=json.loads(normalize_send_window(value))
    days='周一至周五' if data['weekdays']==[0,1,2,3,4] else '、'.join('周'+ '一二三四五六日'[d] for d in data['weekdays'])
    return days+'：'+'、'.join(start+'—'+end for start,end in data['periods'])+'（北京时间，时段内随机）'
