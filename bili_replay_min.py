import subprocess
import requests

SESSION = requests.Session()
COOKIES = {}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
}
FFMPEG = "./bin/ffmpeg"

def init(cookie_str : str, headers=None, ffmpeg_path=None):
    global COOKIES, HEADERS, FFMPEG
    COOKIES = dict(
        item.split("=", 1)
        for item in (p.strip() for p in cookie_str.replace("\n", " ").split(";"))
        if item and "=" in item
    )
    if headers:
        HEADERS.update(headers)
    if ffmpeg_path:
        FFMPEG = ffmpeg_path

def get_replay_list(page=1, page_size=20):
    url = f"https://api.live.bilibili.com/xlive/app-blink/v1/anchorVideo/AnchorGetReplayList?page={page}&page_size={page_size}"
    r = SESSION.get(url, headers=HEADERS, cookies=COOKIES)
    data = r.json()
    return [{"live_key": x["live_key"], "start_time": x["start_time"], "end_time": x["end_time"]}
            for x in data["data"]["replay_info"]]

def get_streams(live_info):
    url = "https://api.live.bilibili.com/xlive/app-blink/v1/anchorVideo/GetSliceStream?live_key={}&start_time={}&end_time={}".format(
        live_info["live_key"], live_info["start_time"], live_info["end_time"]
    )
    r = SESSION.get(url, headers=HEADERS, cookies=COOKIES)
    data = r.json()
    return data["data"]["list"]

def cut_hls_segment(m3u8_url, start, duration, output_path):
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    args += ["-ss", str(start), "-i", m3u8_url, "-t", str(duration)]
    args += ["-c", "copy", "-movflags", "+faststart"]
    args += [output_path]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 裁剪失败: {proc.stderr}")
