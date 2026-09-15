# 黑底字幕视频工具 · 源码版 2.0

这是桌面便携版对应的**唯一一份当前源码**。它以 GitHub 项目 [Story2Video](https://github.com/dvchd/story2video) 的 `c0c5b81a70a620b6dc51a96ba43a90cc07dc8f8d` 提交为基础，加入中文简洁界面、纯黑底 MP4、独立 WAV、保留原文的 SRT、0.9 倍语速和可编辑发音词典。Story2Video 的 MIT 许可证在 `software/story2video/LICENSE`。

## 文件

- `portable_app.py`：本地网页服务与一键生成接口。
- `make_caption_video.py`：Edge TTS 配音、字幕对齐、FFmpeg 视频/WAV 制作。
- `software/story2video/main.py`：GitHub 原项目的界面和配音后端，做了本地模式入口适配。
- `software/story2video/home_cn.html`：中文简洁界面。
- `发音词典.tsv`：左列原文、右列朗读内容，以 Tab 分隔；字幕仍显示原文。

## 运行

需要 Windows 64 位、Python 3.13、FFmpeg 和 FFprobe。可把 `ffmpeg.exe`、`ffprobe.exe` 放进本目录的 `bin` 文件夹，或让它们处于系统 PATH。**正式便携版已经内置这些组件，使用便携版无需安装。**

双击 `启动源码版.cmd`，首次会建立 `.venv` 并安装 `requirements.txt` 中的依赖；之后浏览器会打开 `http://127.0.0.1:8765/`。也可以手动执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe portable_app.py
```

输入 TXT 或粘贴文字后，成品保存在本目录的 `成品` 文件夹。Edge TTS 合成自然语音时需要联网，文字会发送到语音服务。英文缩写及专业词汇请试听 WAV，并按需调整发音词典。

本文件夹没有测试文稿、成品、虚拟环境或旧版本源码。
