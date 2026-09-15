# 部署与运行说明

## 环境要求

- Windows 64 位
- Python 3.13
- FFmpeg 与 FFprobe
- 可访问 Edge TTS 在线语音服务的网络

## 快速部署

1. 下载或克隆本仓库。
2. 将 `ffmpeg.exe` 和 `ffprobe.exe` 放入项目根目录的 `bin` 文件夹，或者将 FFmpeg 加入系统 `PATH`。
3. 双击 `启动源码版.cmd`。
4. 首次启动会自动创建 `.venv` 并安装依赖。
5. 浏览器打开 `http://127.0.0.1:8765/` 后即可使用。

## 手动运行

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe portable_app.py
```

生成的视频、音频和字幕保存在项目根目录的 `成品` 文件夹。

## 注意事项

- 这是本机服务，仅监听 `127.0.0.1:8765`，不会直接向公网开放。
- Edge TTS 合成语音时会把待朗读文字发送到在线语音服务。
- 若提示找不到 FFmpeg，请检查 `bin` 文件夹或系统 `PATH`。
