# 和平精英精彩集锦工具

自动拉取近 24 小时对局的精彩时刻，匹配 B 站直播回放并裁剪成 MP4 片段。内置独立登录/下载/合并/上传脚本。

当前版本额外支持：
- 直播回放包含多个码流时，自动按时间切分并拼接精彩片段
- 对局匹配窗口会相对回放开始时间向前放宽 20 分钟，减少“开播前已在对局中”导致的漏检

## 快速开始

1. 安装 Python 3.10+（需要能在命令行执行 `py --version` 或 `python --version`）。
2. 先做一次 B 站扫码登录：双击 `run_bili_login.bat`（会生成 `BiliLoginInfo.json` 并同步更新 `cookie.txt`）。
3. 双击 `run_all_in_one.bat`，在菜单里选择你要的动作（登录/下载/合并/上传/一条龙）。

脚本会自动创建/复用 `venv` 并安装依赖。`run_highlight_downloader.bat` 会启动本地服务并自动打开 `http://127.0.0.1:8000`。

## 登录相关

- 首次使用或登录失效时，脚本会在控制台输出微信二维码，扫码后会写入 `g4p_accounts/` 下的账号文件。
- 只想单独登录/刷新登录信息：双击 `run_login_check.bat`。
- G4P 多账号通过 `g4p_accounts/` 管理（可用 `run_g4p_account_manager.bat` 添加/删除）。
- B 站登录信息会保存在 `BiliLoginInfo.json`，脚本会自动尝试刷新并同步 `cookie.txt`。
- 只想单独刷新/登录 B 站：双击 `run_bili_login.bat`。

## 输出文件

- 精彩片段：`clips/<live_key>/clip_<开始时间>_<时长>.mp4`
- 合并后成片：`clips/<live_key>/clips_all.mp4`
- 页面会列出最新导出结果。

## 常见问题

- **cookie.txt 未配置**：先运行 `run_bili_login.bat`，或检查是否仍是 `PUT_YOUR_BILIBILI_COOKIE_HERE`。
- **依赖安装失败**：确认网络可访问 PyPI 镜像，或手动 `pip install -r requirements.txt`。
- **片段为空**：确认回放时间段覆盖精彩时刻，可调整 `main.py` 中的 `RECORDING_TAB_MODES` 或 `QUERY_RANGE_SECONDS`。
- **上传失败 DNS 错误**：`getaddrinfo failed` 通常是 DNS/代理问题，先关闭代理或更换 DNS 再试。

## 进阶：手动运行

```powershell
cd D:\g4p_highlights
py -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 一条龙脚本

双击 `run_all_in_one.bat` 后，菜单可选：
- 登录（G4P + B 站）
- 下载剪辑
- 合并剪辑
- 上传
- 一条龙（登录 + 下载 + 合并 + 上传，仅最近 1 场直播）
- 一条龙 + 自动关机（仅最近 1 场直播）
- 一条龙（最近 N 场直播）
- 一条龙 + 自动关机（最近 N 场直播）
- 清理合并产物

其中“最近 N 场直播”会在执行前提示输入数量，例如输入 `2` 表示最近两场直播都参与精彩片段生成、合并和上传。

## 一键开播

双击 `run_one_key_live.bat` 启动一键开播脚本。

配置在 `alert_config.json`（模板 `alert_config.template.json`）的 `one_key_live` 节点：
- `exe_path`：直播姬程序路径（可空，优先从注册表查找）
- `exe_names`：直播姬可执行文件名列表
- `display_name_keywords`：注册表匹配关键词
- `window_title` / `window_class`：用于激活窗口
- `hotkey_send_method`：`scancode` / `vk`
- `delay_seconds`：激活窗口后等待再发送热键
- `pause_on_exit`：脚本结束是否暂停
- `post_live_programs`：开播后自动启动的程序列表

`post_live_programs` 示例：
```json
{
  "path": "C:/Path/To/App.exe",
  "args": ["--flag", "value"],
  "cwd": "C:/Path/To"
}
```

## 片段拼接（手动）

如果只想手动拼接某个直播回放目录：

```powershell
python concat_clips.py --dir clips/<live_key> --out clips_all.mp4 --strategy copy
```

## upload_template.json 字段说明

模板文件位置：`bilibili/upload_template.json`（标题与视频路径不在模板内填写，运行时输入）

- `tid`：分区 ID（默认 4，游戏区）
- `tags`：标签数组，如 `["游戏","FPS","直播"]`（最多 10 个）
- `cover_path`：封面本地路径（必填）
- `description`：默认简介（可空）
- `dynamic`：动态文案（可空）
- `original`：是否原创（默认 true）
- `no_reprint`：禁止转载（默认 false）
- `recreate`：允许二创（默认 false）
- `open_elec`：展示充电（默认 false）
- `up_selection_reply`：开启评论精选（默认 false）
- `up_close_danmu`：关闭弹幕（默认 false）
- `up_close_reply`：关闭评论（默认 false）
- `lossless_music`：无损音乐（默认 false）
- `dolby`：杜比音效（默认 false）
- `watermark`：水印（默认 false）
- `delay_time`：定时发布时间（时间戳秒，留空表示立即）

## alert_config 配置说明

提醒配置采用“模板 + 用户配置”两层：
- 模板：`alert_config.template.json`（提交到仓库）
- 用户配置：`alert_config.json`（已加入 `.gitignore`，不会提交）

使用方法：
1. 复制模板为用户配置：`alert_config.template.json` -> `alert_config.json`
2. 将 `enabled` 设为 `true`
3. 按需开启/填写各渠道凭据

可用事件：
- `upload_complete`：Bilibili 上传完成后提醒
- `all_in_one_complete`：一条龙完成后提醒

可用渠道：
- 企业微信机器人 `wecom_bot`
- 钉钉机器人 `dingtalk_bot`
- Bark `bark`
- Telegram 机器人 `telegram_bot`
- ntfy `ntfy`
