"""One-click TXT -> Edge TTS narration, SRT, WAV and black-background MP4.

The original text is retained on screen; pronunciation rules affect speech only.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import traceback


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "文稿.txt"
DEFAULT_DICTIONARY = ROOT / "发音词典.tsv"
WORK = ROOT / "work" / "caption_video_runs"
KNOWN_FFMPEG = None


def find_ffmpeg() -> tuple[Path, Path]:
    choices = [ROOT / "bin" / "ffmpeg.exe", os.environ.get("FFMPEG_EXE"), shutil.which("ffmpeg"), KNOWN_FFMPEG]
    for choice in choices:
        if choice and Path(choice).is_file():
            executable = Path(choice).resolve()
            probe = executable.with_name("ffprobe.exe")
            if probe.is_file():
                return executable, probe
    raise RuntimeError("找不到 FFmpeg 和 FFprobe。请安装 FFmpeg 或设置 FFMPEG_EXE。")


def load_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("TXT 文件编码无法识别；请保存为 UTF-8。")
    if not text.strip():
        raise RuntimeError("TXT 文件没有可朗读的内容。")
    return text


def load_dictionary(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    rules = []
    for number, line in enumerate(load_text(path).splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2 or not all(parts):
            raise RuntimeError(f"发音词典第 {number} 行须为：原文<Tab>读法")
        rules.append((parts[0], parts[1]))
    return sorted(rules, key=lambda item: len(item[0]), reverse=True)


def pronounce(text: str, rules: list[tuple[str, str]]) -> str:
    for original, spoken in rules:
        left = r"(?<![A-Za-z0-9])" if original[0].isascii() and original[0].isalnum() else ""
        right = r"(?![A-Za-z0-9])" if original[-1].isascii() and original[-1].isalnum() else ""
        text = re.sub(left + re.escape(original) + right, lambda _: spoken, text)
    return text


def split_long(clause: str, limit: int, rules: list[tuple[str, str]]) -> list[str]:
    pieces = []
    while len(clause) > limit:
        cut = limit
        # Keep English words and technical abbreviations together.
        while (
            cut > limit // 2
            and cut < len(clause)
            and (clause[cut - 1].isascii() and clause[cut - 1].isalnum())
            and (clause[cut].isascii() and clause[cut].isalnum())
        ):
            cut -= 1
        if cut <= limit // 2:
            cut = limit
        for original, _ in rules:
            start = clause.rfind(original, 0, cut)
            if start >= 0 and start < cut < start + len(original):
                cut = start if start > limit // 2 else start + len(original)
                break
        pieces.append(clause[:cut])
        clause = clause[cut:]
    if clause:
        pieces.append(clause)
    return pieces


def plan_segments(text: str, limit: int, rules: list[tuple[str, str]]):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    display_segments = []
    spoken_lines = []
    for line in lines:
        clauses = re.findall(r".*?[，,。！？!?；;：:]|.+$", line)
        clauses = [clause for clause in clauses if clause]
        if "".join(clauses) != line:
            raise RuntimeError("无法完整分段原文。")
        parts = [part for clause in clauses for part in split_long(clause, limit, rules)]
        display_segments.extend(parts)
        spoken_lines.append("".join(pronounce(part, rules) for part in parts))
    speech = "。".join(spoken_lines)
    if not display_segments or not normalized(speech):
        raise RuntimeError("没有可朗读的文字。")
    return display_segments, speech


def normalized(text: str) -> str:
    return "".join(char.lower() for char in text if char.isalnum())


def align_segments(display_segments, rules, speech, boundaries):
    expected = normalized(speech)
    actual = "".join(normalized(item["text"]) for item in boundaries)
    if expected != actual:
        at = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
        raise RuntimeError(
            "配音返回的文字与计划朗读文字不一致，已停止以避免字幕错位。"
            f"差异位置 {at}；期望：{expected[max(0, at-15):at+25]}；"
            f"实际：{actual[max(0, at-15):at+25]}。"
        )
    event_spans = []
    position = 0
    for boundary in boundaries:
        length = len(normalized(boundary["text"]))
        if length:
            event_spans.append((position, position + length, boundary))
            position += length
    timeline = []
    position = 0
    for display in display_segments:
        end = position + len(normalized(pronounce(display, rules)))
        relevant = [span for span in event_spans if span[1] > position and span[0] < end]
        if not relevant:
            raise RuntimeError(f"找不到字幕时间轴：{display}")
        first_left, first_right, first = relevant[0]
        last_left, last_right, last = relevant[-1]
        # The service can put text across a subtitle split into one word
        # boundary (for example, C:\N). Allocate its duration by character.
        start_fraction = (position - first_left) / (first_right - first_left)
        end_fraction = (end - last_left) / (last_right - last_left)
        start_sec = (first["offset"] + first["duration"] * start_fraction) / 10_000_000
        end_sec = (last["offset"] + last["duration"] * end_fraction) / 10_000_000
        if timeline and start_sec < timeline[-1]["end"]:
            start_sec = timeline[-1]["end"]
        if end_sec <= start_sec:
            raise RuntimeError(f"字幕时间轴发生重叠：{display}")
        timeline.append({"display": display, "start": start_sec, "end": end_sec})
        position = end
    if position != len(expected):
        raise RuntimeError("字幕没有覆盖全部朗读文字。")
    return timeline


def srt_stamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hour, rest = divmod(millis, 3_600_000)
    minute, rest = divmod(rest, 60_000)
    second, millis = divmod(rest, 1000)
    return f"{hour:02}:{minute:02}:{second:02},{millis:03}"


def ass_stamp(seconds: float) -> str:
    centis = round(seconds * 100)
    hour, rest = divmod(centis, 360_000)
    minute, rest = divmod(rest, 6000)
    second, centis = divmod(rest, 100)
    return f"{hour}:{minute:02}:{second:02}.{centis:02}"


def write_subtitles(timeline, srt_path: Path, ass_path: Path):
    srt = "\n\n".join(
        f"{i}\n{srt_stamp(item['start'])} --> {srt_stamp(item['end'])}\n{item['display']}"
        for i, item in enumerate(timeline, 1)
    ) + "\n"
    srt_path.write_text(srt, encoding="utf-8")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.6,0,2,80,80,96,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for item in timeline:
        display = item["display"]
        # ASS treats braces as style tags and sequences such as \N as newlines.
        # A zero-width separator keeps a visible backslash literal.
        ass_display = display.replace("\\", "\\\u200b").replace("{", "\\{").replace("}", "\\}")
        events.append(
            f"Dialogue: 0,{ass_stamp(item['start'])},{ass_stamp(item['end'])},"
            f"Default,,0,0,0,,{ass_display}"
        )
    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def execute(command: list[str], log_path: Path):
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if result.returncode:
        raise RuntimeError(f"处理失败，详情见 {log_path}")


def probe_media(probe: Path, path: Path):
    result = subprocess.check_output(
        [str(probe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        text=True,
        encoding="utf-8",
    )
    return json.loads(result)


async def synthesize_edge(text: str, voice: str, rate: int, audio: Path):
    import edge_tts

    rate_text = f"{rate:+d}%"
    communicate = edge_tts.Communicate(text, voice, rate=rate_text, boundary="WordBoundary")
    boundaries = []
    with audio.open("wb") as stream:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                stream.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append({"text": chunk["text"], "offset": chunk["offset"], "duration": chunk["duration"]})
    return boundaries


def generate(input_path: Path, output_parent: Path, voice: str, rate: int, dictionary: Path, limit: int, progress=print):
    if not input_path.is_file():
        raise RuntimeError(f"找不到文字文件：{input_path}")
    if not (-50 <= rate <= 50):
        raise RuntimeError("语速必须在 -50 到 +50 之间。")
    if not (16 <= limit <= 80):
        raise RuntimeError("字幕每段字数必须在 16 到 80 之间。")
    ffmpeg, ffprobe = find_ffmpeg()
    rules = load_dictionary(dictionary)
    display_segments, speech = plan_segments(load_text(input_path), limit, rules)

    WORK.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run = WORK / run_id
    run.mkdir()
    progress("正在生成自然配音…")
    audio = run / "output.mp3"
    boundaries = asyncio.run(synthesize_edge(speech, voice, rate, audio))
    if not audio.is_file() or audio.stat().st_size < 1000:
        raise RuntimeError("语音服务没有返回有效音频。")
    (run / "word_boundaries.json").write_text(json.dumps(boundaries, ensure_ascii=False, indent=2), encoding="utf-8")
    timeline = align_segments(display_segments, rules, speech, boundaries)
    srt_path = run / "captions.srt"
    ass_path = run / "captions.ass"
    write_subtitles(timeline, srt_path, ass_path)
    audio_duration = float(probe_media(ffprobe, audio)["format"]["duration"])
    video_duration = audio_duration + 0.7
    ass_filter_path = ass_path.resolve().as_posix().replace(":", "\\:").replace("'", "'\\''")
    video_path = run / "video.mp4"
    progress("正在生成纯黑底字幕视频…")
    execute(
        [
            str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-filter_threads", "1",
            "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=30:d={video_duration:.3f}",
            "-i", str(audio), "-vf", f"ass='{ass_filter_path}'", "-af", "apad=pad_dur=0.7",
            "-t", f"{video_duration:.3f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-threads", "4", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-ar", "48000", "-b:a", "160k", "-movflags", "+faststart", str(video_path),
        ],
        run / "render.log",
    )
    wav_path = run / "audio.wav"
    execute(
        [str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-i", str(video_path),
         "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(wav_path)],
        run / "wav.log",
    )
    progress("正在核对音频、字幕和视频…")
    video_info = probe_media(ffprobe, video_path)
    wav_info = probe_media(ffprobe, wav_path)
    video = next(item for item in video_info["streams"] if item["codec_type"] == "video")
    sound = next(item for item in video_info["streams"] if item["codec_type"] == "audio")
    wav = next(item for item in wav_info["streams"] if item["codec_type"] == "audio")
    if (video["codec_name"], video["width"], video["height"]) != ("h264", 1920, 1080):
        raise RuntimeError("视频编码或尺寸不符合预期。")
    if sound["codec_name"] != "aac" or wav["codec_name"] != "pcm_s16le":
        raise RuntimeError("音频格式不符合预期。")
    if abs(float(video_info["format"]["duration"]) - float(wav_info["format"]["duration"])) > 0.1:
        raise RuntimeError("视频与 WAV 时长不一致。")
    if timeline[-1]["end"] > audio_duration + 0.05:
        raise RuntimeError("字幕结束时间超过音频时长。")
    execute([str(ffmpeg), "-v", "error", "-i", str(video_path), "-f", "null", "-"], run / "decode.log")

    output_parent.mkdir(parents=True, exist_ok=True)
    folder = output_parent / f"{input_path.stem}_{run_id}"
    folder.mkdir()
    for source, destination in (
        (video_path, folder / f"{input_path.stem}.mp4"),
        (wav_path, folder / f"{input_path.stem}.wav"),
        (srt_path, folder / f"{input_path.stem}.srt"),
    ):
        shutil.copy2(source, destination)
    manifest = {
        "source": str(input_path), "voice": voice, "rate_percent": rate,
        "subtitle_segments": len(timeline), "duration_seconds": video_duration,
        "dictionary": str(dictionary), "upstream": "dvchd/story2video; rany2/edge-tts",
    }
    (folder / "制作记录.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    resolved_run = run.resolve()
    resolved_work = WORK.resolve()
    if resolved_run != resolved_work and resolved_run.is_relative_to(resolved_work):
        shutil.rmtree(resolved_run)
    progress(f"完成：{folder}")
    return folder


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("黑底字幕视频生成器")
    root.geometry("760x340")
    root.resizable(False, False)
    root.columnconfigure(1, weight=1)
    input_var = tk.StringVar(value=str(DEFAULT_INPUT))
    output_var = tk.StringVar(value=str(DEFAULT_INPUT.parent / "字幕视频输出"))
    dictionary_var = tk.StringVar(value=str(DEFAULT_DICTIONARY))
    voice_var = tk.StringVar(value="zh-CN-YunyangNeural")
    rate_var = tk.IntVar(value=-10)
    status_var = tk.StringVar(value="选择 TXT 后点击“生成视频”；默认自然男声，0.9 倍速。")

    def row(index, label, variable, browse):
        ttk.Label(root, text=label).grid(row=index, column=0, padx=12, pady=8, sticky="w")
        ttk.Entry(root, textvariable=variable).grid(row=index, column=1, padx=4, pady=8, sticky="ew")
        ttk.Button(root, text="浏览", command=browse).grid(row=index, column=2, padx=12, pady=8)

    def select_input():
        choice = filedialog.askopenfilename(filetypes=[("文字文件", "*.txt"), ("所有文件", "*.*")])
        if choice:
            input_var.set(choice)
            output_var.set(str(Path(choice).parent / "字幕视频输出"))

    def select_output():
        choice = filedialog.askdirectory()
        if choice:
            output_var.set(choice)

    def select_dictionary():
        choice = filedialog.askopenfilename(filetypes=[("词典文件", "*.tsv"), ("所有文件", "*.*")])
        if choice:
            dictionary_var.set(choice)

    row(0, "文字 TXT", input_var, select_input)
    row(1, "输出目录", output_var, select_output)
    row(2, "发音词典", dictionary_var, select_dictionary)
    ttk.Label(root, text="配音音色").grid(row=3, column=0, padx=12, pady=8, sticky="w")
    ttk.Combobox(
        root, textvariable=voice_var, state="readonly",
        values=["zh-CN-YunyangNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"],
    ).grid(row=3, column=1, padx=4, pady=8, sticky="ew")
    ttk.Label(root, text="语速调整 (%)").grid(row=4, column=0, padx=12, pady=8, sticky="w")
    ttk.Spinbox(root, from_=-50, to=50, increment=5, textvariable=rate_var, width=10).grid(
        row=4, column=1, padx=4, pady=8, sticky="w"
    )
    ttk.Label(root, text="-10% 约为 0.9 倍速；每次生成独立文件夹，不覆盖旧成品。", foreground="#555555").grid(
        row=5, column=0, columnspan=3, padx=12, pady=6, sticky="w"
    )
    button = ttk.Button(root, text="生成黑底字幕视频")
    button.grid(row=6, column=0, columnspan=3, padx=12, pady=10)
    ttk.Label(root, textvariable=status_var, wraplength=720).grid(
        row=7, column=0, columnspan=3, padx=12, pady=8, sticky="w"
    )

    def start():
        button.configure(state="disabled")
        options = (Path(input_var.get()), Path(output_var.get()), voice_var.get(),
                   int(rate_var.get()), Path(dictionary_var.get()), 30)

        def report(message):
            root.after(0, lambda: status_var.set(message))

        def worker():
            try:
                folder = generate(*options, progress=report)
                root.after(0, lambda: messagebox.showinfo("制作完成", f"视频、WAV、SRT 已保存到：\n{folder}"))
            except Exception as error:
                log = ROOT / "work" / "caption_video_last_error.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(traceback.format_exc(), encoding="utf-8")
                report(f"生成失败：{error}")
                root.after(0, lambda: messagebox.showerror("生成失败", f"{error}\n\n详细日志：{log}"))
            finally:
                root.after(0, lambda: button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    button.configure(command=start)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="TXT 转自然配音、黑底字幕 MP4、WAV 和 SRT")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--voice", default="zh-CN-YunyangNeural")
    parser.add_argument("--rate", type=int, default=-10)
    parser.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    parser.add_argument("--max-chars", type=int, default=30)
    args = parser.parse_args()
    if args.input is None:
        run_gui()
    else:
        folder = generate(
            args.input.resolve(),
            (args.output_dir or args.input.parent / "字幕视频输出").resolve(),
            args.voice, args.rate, args.dictionary.resolve(), args.max_chars,
        )
        print(folder)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if len(sys.argv) == 1:
            from tkinter import messagebox
            messagebox.showerror("启动失败", traceback.format_exc())
        else:
            traceback.print_exc()
        sys.exit(1)
