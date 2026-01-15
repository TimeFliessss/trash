import re
from datetime import datetime, timedelta

import requests

from game_for_peace.area_utils import locate_point_with_direction

def get_wonderful_times(game_open_id, data_url, areas=None, resources=None, mode=None, play_time=None, rank=None):
    r = requests.get(data_url)
    data = r.json()
    open_id = game_open_id
    players = data.get("players", [])
    ai_players = data.get("aiPlayers", [])
    teams = {}
    for p in players:
        teams.setdefault(p.get("teamid"), []).append(p)
    hero = next((p for p in players if p.get("openid") == open_id), None)
    if not hero:
        return {}

    start_ts = int(data.get("base", {}).get("startTime", 0))
    start = datetime.fromtimestamp(start_ts)

    hero_uid = hero.get("uid") if isinstance(hero, dict) else None
    related_uid = {hero_uid} if hero_uid else set()

    total_players = len(players) + len(ai_players)
    ai_count = len(ai_players)

    events = []
    for item in data.get("beats", []) or []:
        if item.get("uid") not in related_uid:
            continue
        locate = locate_point_with_direction((float(item['src']['x']), float(item['src']['y'])), areas)
        offset = _safe_int(item.get("time"))
        event_ts = start + timedelta(seconds=offset) if start_ts else None
        events.append(
            {
                "type": "击倒",
                "offset": offset,
                "event_ts": event_ts,
                "weapon": resources.get(str(item["resid"]), {}).get("name"),
                "target": (p := next((x for x in players if x["uid"] == item["tid"]), None)) and p.get("name"),
                "target_is_ai": item['tid'] in [x['uid'] for x in ai_players],
                "locate": locate
            }
        )

    for item in data.get("kills", []) or []:
        if item.get("uid") not in related_uid:
            continue
        locate = locate_point_with_direction((item['src']['x'], item['src']['y']), areas)
        offset = _safe_int(item.get("time"))
        event_ts = start + timedelta(seconds=offset) if start_ts else None
        events.append(
            {
                "type": "淘汰",
                "offset": offset,
                "event_ts": event_ts,
                "weapon": resources.get(str(item["resid"]), {}).get("name"),
                "target": (p := next((x for x in players if x["uid"] == item["tid"]), None)) and p.get("name"),
                "target_is_ai": item['tid'] in [x['uid'] for x in ai_players],
                "locate": locate
            }
        )

    events.sort(key=lambda x: x["offset"])

    info = {
        "start_time": start_ts,
        "start_time_text": play_time,
        "map": mode,
        "rank": rank,
        "total_players": total_players,
        "ai_count": ai_count,
        "events": events,
    }
    return info

def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _format_mmss(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    secs = max(0, seconds) % 60
    return f"{minutes:02d}:{secs:02d}"


def highlight_info_to_description(info: dict) -> str:
    if not info:
        return ""
    header = f"{info.get('start_time_text').replace(':','时')}分 rank:{info.get('rank')} {info.get('map')} {info.get('total_players')}({info.get('ai_count')}bots)"
    lines = [header]
    _RE_HAN = re.compile(r'[\u4e00-\u9fff]+')
    def remove_han_unless_all_han(s: str) -> str:
        t = s.strip()
        if t and _RE_HAN.sub('', t) == '':
            return s
        return _RE_HAN.sub('', s)

    last_time_token = None
    for e in info.get("events", []):
        group_token = e.get("label_time")
        show_time = e.get("label_show_time", True)
        time_token = group_token if show_time else ""
        locate = e.get("locate") or ""
        prefix = f"{time_token} {locate}".strip() if time_token else ""
        label = (
            f"{prefix} {remove_han_unless_all_han(e.get('weapon'))}"
            f"{e.get('type')}'"
            f"{'bot' if e.get('target_is_ai') else e.get('target')}'"
        ).strip()
        if group_token and group_token == last_time_token and lines:
            lines[-1] = f"{lines[-1]}, {label}"
        else:
            lines.append(label)
        if group_token:
            last_time_token = group_token

    desc = "\n".join(lines)
    return desc

def _split_description(text: str, max_chars: int = 1000) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    chunks = []
    current_lines = []
    current_len = 0
    for line in lines:
        extra = len(line) + (1 if current_lines else 0)
        if current_lines and current_len + extra > max_chars:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_len = 0
        if not current_lines and len(line) > max_chars:
            chunks.append(line)
            continue
        current_lines.append(line)
        current_len += extra
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks

def preprocess_wonderful(
    wonderful_times,
    start_time,
    end_time,
    pad_before=15,
    pad_after=6,
    label_advance=10,
):
    start_dt = datetime.fromtimestamp(start_time)
    total_len = max(0, end_time - start_time)

    # sort matches by start time
    match_infos = sorted(
        [i for i in wonderful_times if i],
        key=lambda x: x.get("start_time", 0),
    )

    intervals = []
    for info in match_infos:
        if len(info.get("events", [])) == 0:
            continue
        for event in info.get("events", []):
            event_ts = event.get("event_ts")
            if not isinstance(event_ts, datetime):
                continue
            off = (event_ts - start_dt).total_seconds()
            if off < 0 or off > total_len:
                continue
            s = max(0, off - pad_before)
            e = min(total_len, off + pad_after)
            if e > s:
                intervals.append({"start": s, "end": e, "event": event})

    if not intervals:
        return [], []

    intervals.sort(key=lambda x: (x["start"], x["end"]))
    segments = []
    for item in intervals:
        s, e = item["start"], item["end"]
        if not segments:
            segments.append({"start": s, "end": e, "events": [item["event"]]})
            continue
        last = segments[-1]
        if s <= last["end"]:
            last["end"] = max(last["end"], e)
            last["events"].append(item["event"])
        else:
            segments.append({"start": s, "end": e, "events": [item["event"]]})

    concat_cursor = 0.0
    included_events = set()
    for seg in segments:
        seg_duration = max(0.0, seg["end"] - seg["start"])
        label_offset = max(0, pad_before - label_advance)
        label_ts = concat_cursor + label_offset
        label_time = _format_mmss(int(label_ts))
        for idx, event in enumerate(seg["events"]):
            event["label_ts"] = label_ts
            event["label_time"] = label_time
            event["label_show_time"] = idx == 0
            included_events.add(id(event))
        concat_cursor += seg_duration

    for info in match_infos:
        info["events"] = [e for e in info.get("events", []) if id(e) in included_events]

    descriptions = [highlight_info_to_description(info) for info in match_infos]
    description = "\n".join([d for d in descriptions if d])
    description_chunks = _split_description(description, max_chars=1000)

    clips = [(round(seg["start"], 3), round(seg["end"] - seg["start"], 3)) for seg in segments]
    return clips, description_chunks
