import os
import time
from pathlib import Path
from typing import List, Sequence

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse

from auto_cut import get_wonderful_times, preprocess_wonderful
from bili_replay_min import init, get_replay_list, get_streams, cut_hls_segment
from g4p_battles import g4p_login, is_g4p_logged_in
from tqdm import tqdm

COOKIE_FILE = Path("cookie.txt")
RECORDING_TAB_MODES = [('计分', ['全部']), ('不计分', ['全部'])]
QUERY_RANGE_SECONDS = 86400


class HighlightPipelineError(RuntimeError):
    """Raised when the highlight pipeline fails."""


def _ensure_cookie() -> str:
    if not COOKIE_FILE.exists():
        COOKIE_FILE.write_text("PUT_YOUR_BILIBILI_COOKIE_HERE", encoding="utf-8")
        raise HighlightPipelineError("已自动创建 cookie.txt，请按照 README 步骤粘贴你的 B 站 Cookie 后重新运行。")

    cookie_str = COOKIE_FILE.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if not cookie_str or "PUT_YOUR_BILIBILI_COOKIE_HERE" in cookie_str:
        raise HighlightPipelineError("cookie.txt 未配置，请粘贴完整的 B 站 Cookie。")
    return cookie_str


def _progress(iterable: Sequence, enabled: bool, **kwargs):
    if enabled:
        return tqdm(iterable, **kwargs)
    return iterable


def _collect_recent_battles(g4p_client, query_time: float, roleId: str):
    tabs = g4p_client.get_battle_mode_tabs()
    all_battles = []
    for t in RECORDING_TAB_MODES:
        tab = next((x for x in tabs if x["tabName"] == t[0]), None)
        if not tab:
            continue
        modes = [x['mode'] for x in tab['modeList'] if x['name'] in t[1]]
        if not modes:
            continue
        battles = g4p_client.get_pubg_battle_list(page=1, count=30, tabIndex=tab['tabIndex'], modes=modes, role_id=roleId)
        all_battles.extend(battles['list'])
    return [x for x in all_battles if 0 <= query_time - int(x['startime']) <= QUERY_RANGE_SECONDS]


def _fetch_bili_replays(cookie_str: str):
    init(cookie_str)
    return get_replay_list()


def _select_target_replays(replays, selected_live_keys):
    if selected_live_keys:
        mapping = {r.get("live_key"): r for r in replays}
        selected = []
        for key in selected_live_keys:
            item = mapping.get(key)
            if item:
                selected.append(item)
        if not selected:
            raise HighlightPipelineError('选中的录像不存在或已过期。')
        return selected
    return replays[:1]


def run_highlight_pipeline(show_progress: bool = True, selected_live_keys=None) -> dict:
    cookie_str = _ensure_cookie()
    query_time = time.time()

    g4p_client = g4p_login()
    roles = [i['roleId'] for i in g4p_client.account_manager.role_list]
    recent_battles = []
    for roleId in roles:
        for b in _collect_recent_battles(g4p_client, query_time, roleId=roleId):
            recent_battles.append((b, roleId))
    if not recent_battles:
        raise HighlightPipelineError("最近 24 小时内没有找到有效对局。")
    wonderful_times = []
    for b, roleId in _progress(recent_battles, show_progress, desc="获取精彩时间", unit="局"):
        attempt = 15
        while attempt > 0:
            replay_data = g4p_client.parse_replay_data(battleId=b['battleId'], role_id=roleId)
            if replay_data["reviewStatus"] == 3:
                rep_data = g4p_client.get_pubg_replay_data(b['battleId'])
                wonderful_times.extend(get_wonderful_times(g4p_client.account_manager.game_open_id, rep_data['dataUrl']))
                break
            attempt -= 1
            time.sleep(1)

    if not wonderful_times:
        raise HighlightPipelineError("未能从最近对局中解析出精彩时间。")

    try:
        replays = _fetch_bili_replays(cookie_str)
    except Exception as exc:
        raise HighlightPipelineError(f"获取 B 站录像列表失败: {exc}") from exc

    if not replays:
        raise HighlightPipelineError("未找到可用的直播录像。")

    target_replays = _select_target_replays(replays, selected_live_keys)
    total_success = 0
    summary_failed = []
    all_clip_files = []
    replay_results = []

    for current_replay in target_replays:
        streams = get_streams(current_replay)
        if not streams:
            raise HighlightPipelineError("未获取到任何录像码流。")

        m3u8 = max(streams, key=lambda s: s['start_time'])
        output_dir = Path("clips") / str(current_replay["live_key"])
        output_dir.mkdir(parents=True, exist_ok=True)

        start_time = m3u8["start_time"]
        end_time = m3u8["end_time"]
        merged_clips = preprocess_wonderful(wonderful_times, start_time, end_time, pad_before=12, pad_after=5)
        if not merged_clips:
            raise HighlightPipelineError("近 24 小时内未检测到精彩时刻，或录像尚未生成。")

        success_count = 0
        failed_current = []
        clip_files_current = []
        for s, d in _progress(merged_clips, show_progress, desc="导出精彩片段", unit="段"):
            output_path = output_dir / f"clip_{int(start_time+s)}_{int(d)}.mp4"
            try:
                cut_hls_segment(m3u8['stream'], start=s, duration=d, output_path=str(output_path))
                success_count += 1
                clip_files_current.append(str(output_path.resolve()))
            except Exception as exc:
                msg = f"导出 {output_path.name} 失败：{exc}"
                failed_current.append(msg)
                summary_failed.append(msg)

        total_success += success_count
        all_clip_files.extend(clip_files_current)
        replay_results.append({
            "live_key": current_replay["live_key"],
            "output_dir": str(output_dir.resolve()),
            "success_count": success_count,
            "clip_files": clip_files_current,
            "failed_messages": failed_current,
        })

    return {
        "success_count": total_success,
        "failed_messages": summary_failed,
        "clip_files": all_clip_files,
        "live_keys": [entry["live_key"] for entry in replay_results],
        "replay_results": replay_results,
    }





HTML_PAGE_PATH = Path(__file__).parent / "web" / "index.html"

app = FastAPI(title="G4P Highlights API")


@app.get("/", response_class=HTMLResponse)
async def index():
    if not HTML_PAGE_PATH.exists():
        raise HTTPException(status_code=500, detail="缺少前端页面文件 web/index.html")
    return HTML_PAGE_PATH.read_text(encoding="utf-8")



@app.get("/api/status")
async def get_status():
    try:
        logged_in = is_g4p_logged_in()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"检测登录状态失败：{exc}") from exc
    return {"logged_in": bool(logged_in)}

@app.post("/api/run")
async def run_pipeline_api(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    selected_keys = None
    if isinstance(payload, dict):
        raw_keys = payload.get("live_keys")
        if isinstance(raw_keys, list):
            selected_keys = [str(k) for k in raw_keys if k]
    try:
        result = await run_in_threadpool(run_highlight_pipeline, True, selected_keys)
    except HighlightPipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{exc}") from exc
    return {"status": "ok", "result": result}


@app.get("/api/battles")
async def get_recent_battles():
    def _work():
        g4p_client = g4p_login()
        roles = [i['roleId'] for i in g4p_client.account_manager.role_list]
        battles = []
        for roleId in roles:
            battles.extend(_collect_recent_battles(g4p_client, time.time(), roleId=roleId))
        return {"count": len(battles), "battles": battles}

    try:
        return await run_in_threadpool(_work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取对局失败：{exc}") from exc


@app.get("/api/replays")
async def get_recent_replays():
    def _work():
        cookie_str = _ensure_cookie()
        replays = _fetch_bili_replays(cookie_str)
        return {"count": len(replays), "replays": replays}

    try:
        return await run_in_threadpool(_work)
    except HighlightPipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取 B 站录像失败：{exc}") from exc



@app.get("/api/clips")
async def list_clips():
    clip_dir = Path("clips")
    if not clip_dir.exists():
        return {"clips": []}
    clips = []
    for file in clip_dir.rglob("*.mp4"):
        clips.append({
            "name": file.name,
            "relative_path": str(file.relative_to(clip_dir)),
            "mtime": int(file.stat().st_mtime),
        })
    clips.sort(key=lambda x: x["mtime"], reverse=True)
    return {"clips": clips}


@app.get("/clip-files/{relative_path:path}")
async def serve_clip(relative_path: str):
    clip_dir = Path("clips").resolve()
    clip_path = (clip_dir / relative_path).resolve()
    if clip_dir not in clip_path.parents or not clip_path.is_file():
        raise HTTPException(status_code=404, detail="clip not found")
    if clip_path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="unsupported file type")
    return FileResponse(clip_path)
if __name__ == "__main__":
    host = os.getenv("HIGHLIGHT_HOST", "0.0.0.0")
    port = int(os.getenv("HIGHLIGHT_PORT", "8000"))
    visible_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"[INFO] 本地 Web 服务启动，浏览器访问 http://{visible_host}:{port} 点击按钮即可导出。")
    uvicorn.run(app, host=host, port=port)






