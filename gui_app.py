# -*- coding: utf-8 -*-
"""视频/音频转录与文本精校 GUI。

推理交给 llama-server 子进程，本程序只做界面、音频提取与调度。
"""
import os
import sys
import threading
import queue
import re
import tempfile
import platform
import subprocess
from functools import partial

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext, messagebox
except ImportError:
    sys.exit("当前 Python 环境没有 tkinter，请用 conda base 环境或安装 tk。")

import numpy as np

from asr_server import Server, MODEL_TIERS
from chunker import split_by_energy

SAMPLE_RATE = 16000
# 单块目标时长。注意力开销随长度平方增长，且槽位越短越容易并行排满；
# 实测 60 秒块在 8 槽下可达 75 倍实时，整段送只有 2 倍。
CHUNK_SECONDS = 60.0
# 正常语速一秒 3～5 字，60 秒块上限约 300 token；给 600 留足余量，
# 同时避免偶发失控的块空转，拖住同批其他请求。
MAX_TOKENS = 600


def _import_av():
    import av
    import soundfile as sf
    return av, sf


def extract_audio(input_path: str, log) -> str:
    """把音视频文件里的音频抽成 16kHz 单声道 WAV，返回临时文件路径。

    调用方负责删除返回的临时文件。
    """
    av, sf = _import_av()
    container = av.open(input_path)
    try:
        audio_stream = next(s for s in container.streams if s.type == "audio")
    except StopIteration:
        container.close()
        raise ValueError("该文件不包含音频轨道。")

    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=SAMPLE_RATE
    )
    samples = []
    for frame in container.decode(audio_stream):
        for resampled in resampler.resample(frame):
            samples.append(resampled.to_ndarray().astype(np.float32) / 32768.0)
    container.close()
    for resampled in resampler.resample(None):
        samples.append(resampled.to_ndarray().astype(np.float32) / 32768.0)

    if not samples:
        raise ValueError("未能从该文件解码出音频。")
    audio = np.concatenate(samples, axis=1)[0]
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    sf.write(tmp.name, audio, SAMPLE_RATE)
    return tmp.name


def _load_wav(path):
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"音频采样率应为 {SAMPLE_RATE}Hz，实际 {sr}Hz：{path}")
    return data


_server_lock = threading.Lock()
_server: Server | None = None
_server_tier: str | None = None


def get_server(model_tier: str, log) -> Server:
    """取当前模型的服务实例；切换模型时先关旧的再开新的。"""
    global _server, _server_tier
    with _server_lock:
        if _server is not None and _server_tier != model_tier:
            log(f"🔄 切换模型：{_server_tier} → {model_tier}，重启推理服务…")
            _server.close()
            _server = None
        if _server is None:
            _server = Server(model_tier, log)
            _server_tier = model_tier
        return _server


def close_server():
    global _server, _server_tier
    with _server_lock:
        if _server is not None:
            _server.close()
            _server = None
            _server_tier = None


def transcribe_audio(audio_path: str, output_path: str, model_tier: str, log):
    server = get_server(model_tier, log)

    wav = _load_wav(audio_path)
    total_sec = len(wav) / SAMPLE_RATE
    chunks = split_by_energy(wav, SAMPLE_RATE, CHUNK_SECONDS)
    log(f"🎙️ 转录中：{total_sec/60:.1f} 分钟音频，切成 {len(chunks)} 段，"
        f"{server.slots} 路并发…")

    texts = server.transcribe_chunks([c for c, _ in chunks], SAMPLE_RATE, MAX_TOKENS)

    text = "".join(texts).strip()
    if not text:
        raise RuntimeError("转录结果为空：该音频没有可识别的语音。")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"✅ 转录完成：{output_path}（{len(text)} 字）")


def read_transcript(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    texts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\[.*?\]\s*(.*)$", line)
        texts.append(m.group(1) if m else line)
    return "".join(texts)


def paragraphize(text: str, sentences_per_para: int = 4):
    parts, start = [], 0
    cnt = 0
    for i, ch in enumerate(text):
        if ch in "。！？!?":
            cnt += 1
            if cnt >= sentences_per_para:
                parts.append(text[start : i + 1])
                start, cnt = i + 1, 0
    tail = text[start:]
    if tail.strip():
        parts.append(tail)
    return "\n\n".join(p.strip() for p in parts)


def transcript_path(input_file: str, out_dir: str | None = None):
    """转录结果路径：与源文件同名，后缀换成 .txt。"""
    stem = os.path.splitext(os.path.basename(input_file))[0]
    base = out_dir if out_dir else os.path.dirname(input_file)
    return os.path.join(base, stem + ".txt")


def split_inputs(raw: str):
    """把一个输入框里的多行路径拆成列表，去掉空行和引号。"""
    items = []
    for line in raw.splitlines():
        p = line.strip().strip('"').strip("'")
        if p:
            items.append(p)
    return items


def pipeline_transcribe(input_file: str, model_tier: str, log, out_dir: str | None = None):
    tmp_path = None
    try:
        log(f"📁 提取音频：{input_file}")
        tmp_path = extract_audio(input_file, log)
        out = transcript_path(input_file, out_dir)
        transcribe_audio(tmp_path, out, model_tier, log)
        return out
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            os.remove(tmp_path)


def pipeline_transcribe_many(input_files, model_tier, log, out_dir: str | None = None):
    """批量转录，返回成功产出的结果路径列表。

    单个文件失败不中断整批：记日志后继续下一个。
    """
    outputs = []
    for i, src in enumerate(input_files, 1):
        log(f"===== [{i}/{len(input_files)}] {os.path.basename(src)} =====")
        try:
            outputs.append(pipeline_transcribe(src, model_tier, log, out_dir))
        except Exception as e:
            log(f"💥 第 {i} 个失败：{type(e).__name__}: {e}")
    return outputs


def pipeline_polish(input_file: str, log):
    raw = read_transcript(input_file)
    log(f"📄 已读取原文 {len(raw)} 字，正在分段...")
    polished = paragraphize(raw)
    base, _ = os.path.splitext(input_file)
    if base.endswith("_转录结果"):
        base = base[: -len("_转录结果")]
    output_file = f"{base}_精校.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(polished)
    log(f"✅ 精校完成：{output_file}")
    return output_file


TIER_LABELS = {
    "fast": "极速（0.6B Q8_0，下载 1.0GB，显存约 3GB）",
    "balanced": "推荐（1.7B Q8_0，下载 2.5GB，显存约 5.5GB）",
    "precise": "高精（1.7B bf16，下载 4.7GB，显存约 7.3GB）",
}


def available_tiers() -> list[str]:
    """返回本地已存在完整模型文件的档位。"""
    tiers = []
    for key, (model, mmproj) in MODEL_TIERS.items():
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        if os.path.isfile(os.path.join(d, model)) and os.path.isfile(os.path.join(d, mmproj)):
            tiers.append(key)
    return tiers


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("视频转录 & 文本精校工具")
        root.geometry("860x700")
        root.minsize(720, 580)

        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_transcript: str | None = None
        self.last_polished: str | None = None

        self._build_ui()
        self._poll_queue()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        # ===== 模型选择 =====
        row = 0
        ttk.Label(top, text="模型").grid(row=row, column=0, sticky="w")
        self.var_tier = tk.StringVar()
        tiers = available_tiers()
        self.combo_tier = ttk.Combobox(
            top, textvariable=self.var_tier, state="readonly", width=48,
            values=[TIER_LABELS[t] for t in tiers],
        )
        self.combo_tier.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        if tiers:
            self.combo_tier.current(0)
        self._tier_keys = tiers

        row += 1
        if not tiers:
            ttk.Label(
                top, text="⚠️ 未找到模型文件，请检查程序目录下的 models/ 文件夹。",
                foreground="#c0392b",
            ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ===== 输入文件选择 =====
        row += 1
        ttk.Label(top, text="步骤 1：选择 视频/音频 文件（可多选，一行一个）").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 4)
        )
        row += 1
        self.txt_src = tk.Text(top, height=3, wrap="none", font=("Consolas", 9))
        self.txt_src.grid(row=row, column=0, columnspan=2, sticky="ew", padx=(0, 6))
        ttk.Button(top, text="浏览…", command=self._browse_src).grid(
            row=row, column=2, sticky="w"
        )
        top.columnconfigure(0, weight=1)

        row += 1
        ttk.Label(top, text="输出目录（留空则放在源文件旁边）").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        row += 1
        self.var_outdir = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_outdir).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=(0, 6)
        )
        ttk.Button(top, text="选择…", command=self._browse_outdir).grid(
            row=row, column=2, sticky="w"
        )

        row += 1
        self.btn_transcribe = ttk.Button(
            top, text="① 开始转录", command=self._on_transcribe
        )
        self.btn_transcribe.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        self.btn_open_transcript = ttk.Button(
            top, text="打开转录结果", command=partial(self._open_file, "transcript")
        )
        self.btn_open_transcript.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self.btn_open_transcript.state(["disabled"])

        # ===== 精校文件 =====
        row += 1
        ttk.Label(top, text="步骤 2：选择 转录结果 .txt 文件（可多选，一行一个）").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        row += 1
        self.txt_txt = tk.Text(top, height=3, wrap="none", font=("Consolas", 9))
        self.txt_txt.grid(row=row, column=0, columnspan=2, sticky="ew", padx=(0, 6))
        ttk.Button(top, text="浏览…", command=self._browse_txt).grid(
            row=row, column=2, sticky="w"
        )

        row += 1
        self.btn_polish = ttk.Button(
            top, text="② 开始精校（分段）", command=self._on_polish
        )
        self.btn_polish.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        self.btn_open_polished = ttk.Button(
            top, text="打开精校结果", command=partial(self._open_file, "polished")
        )
        self.btn_open_polished.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self.btn_open_polished.state(["disabled"])

        # ===== 一键流程 =====
        row += 1
        self.btn_all = ttk.Button(
            top,
            text="⭐ 一键：转录 + 精校",
            command=self._on_pipeline,
        )
        self.btn_all.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 4))

        # ===== 日志 =====
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=6)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_box = scrolledtext.ScrolledText(
            log_frame, wrap="word", height=14, font=("Consolas", 10)
        )
        self.log_box.pack(fill="both", expand=True)
        self.log_box.configure(state="disabled")

        status = ttk.Frame(self.root, padding=(10, 0))
        status.pack(fill="x")
        self.var_status = tk.StringVar(value="就绪。")
        ttk.Label(status, textvariable=self.var_status).pack(side="left")
        self.btn_clear = ttk.Button(status, text="清空日志", command=self._clear_log)
        self.btn_clear.pack(side="right")

    # ---------- 文件选择 ----------
    @staticmethod
    def _read(widget: tk.Text) -> str:
        return widget.get("1.0", "end").strip()

    @staticmethod
    def _write(widget: tk.Text, text: str):
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _browse_src(self):
        paths = filedialog.askopenfilenames(
            title="选择视频/音频（可多选）",
            filetypes=[
                ("音视频", "*.mp4 *.mkv *.mov *.avi *.flv *.wav *.mp3 *.m4a *.aac *.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._write(self.txt_src, "\n".join(paths))

    def _browse_txt(self):
        paths = filedialog.askopenfilenames(
            title="选择转录结果 txt（可多选）",
            filetypes=[("文本", "*.txt"), ("所有文件", "*.*")],
        )
        if paths:
            self._write(self.txt_txt, "\n".join(paths))

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.var_outdir.set(p)

    def _open_file(self, which: str):
        path = self.last_transcript if which == "transcript" else self.last_polished
        if not path or not os.path.isfile(path):
            return
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])

    # ---------- 日志 ----------
    def log(self, msg: str):
        self.msg_queue.put(str(msg) + "\n")

    def _poll_queue(self):
        try:
            while True:
                line = self.msg_queue.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", line)
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # ---------- 按钮回调 ----------
    def _set_busy(self, busy: bool):
        state = ["disabled"] if busy else ["!disabled"]
        for b in (
            self.btn_transcribe,
            self.btn_open_transcript,
            self.btn_polish,
            self.btn_open_polished,
            self.btn_all,
            self.btn_clear,
        ):
            b.state(state)
        self.var_status.set("处理中…" if busy else "就绪。")

    def _worker(self, fn, on_done):
        try:
            fn()
        except Exception as e:
            self.log(f"💥 出错：{e}")
        finally:
            self.root.after(0, on_done)

    def _current_tier(self) -> str:
        idx = self.combo_tier.current()
        if idx < 0:
            raise RuntimeError("请先选择一个模型。")
        return self._tier_keys[idx]

    def _on_transcribe(self):
        srcs = split_inputs(self._read(self.txt_src))
        missing = [s for s in srcs if not os.path.isfile(s)]
        if not srcs or missing:
            messagebox.showerror(
                "提示",
                "请先选择有效的 视频/音频 文件。\n找不到：\n" + "\n".join(missing)
                if missing
                else "请先选择有效的 视频/音频 文件。",
            )
            return
        tier = self._current_tier()

        def job():
            out_dir = self.var_outdir.get().strip() or None
            outs = pipeline_transcribe_many(srcs, tier, self.log, out_dir)
            if outs:
                self.last_transcript = outs[-1]
                self.root.after(0, lambda: self.btn_open_transcript.state(["!disabled"]))
                if not self._read(self.txt_txt):
                    self._write(self.txt_txt, "\n".join(outs))
            self.log(f"🎯 共完成 {len(outs)}/{len(srcs)} 个文件。")

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()

    def _on_polish(self):
        srcs = split_inputs(self._read(self.txt_txt))
        missing = [s for s in srcs if not os.path.isfile(s)]
        if not srcs or missing:
            messagebox.showerror(
                "提示",
                "请先选择有效的 转录结果 txt 文件。\n找不到：\n" + "\n".join(missing)
                if missing
                else "请先选择有效的 转录结果 txt 文件。",
            )
            return

        def job():
            outs = []
            for i, src in enumerate(srcs, 1):
                try:
                    outs.append(pipeline_polish(src, self.log))
                except Exception as e:
                    self.log(f"💥 第 {i} 个失败：{type(e).__name__}: {e}")
            if outs:
                self.last_polished = outs[-1]
                self.root.after(0, lambda: self.btn_open_polished.state(["!disabled"]))
            self.log(f"🎯 共完成 {len(outs)}/{len(srcs)} 个文件。")

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()

    def _on_pipeline(self):
        srcs = split_inputs(self._read(self.txt_src))
        missing = [s for s in srcs if not os.path.isfile(s)]
        if not srcs or missing:
            messagebox.showerror(
                "提示",
                "一键流程需要先选择有效的 视频/音频 文件。\n找不到：\n" + "\n".join(missing)
                if missing
                else "一键流程需要先选择有效的 视频/音频 文件。",
            )
            return
        tier = self._current_tier()

        def job():
            out_dir = self.var_outdir.get().strip() or None
            polished = []
            for i, src in enumerate(srcs, 1):
                self.log(f"===== [{i}/{len(srcs)}] {os.path.basename(src)} =====")
                try:
                    out1 = pipeline_transcribe(src, tier, self.log, out_dir)
                    self.last_transcript = out1
                    self.root.after(
                        0, lambda: self.btn_open_transcript.state(["!disabled"])
                    )
                    out2 = pipeline_polish(out1, self.log)
                    polished.append(out2)
                    self.last_polished = out2
                    self.root.after(
                        0, lambda: self.btn_open_polished.state(["!disabled"])
                    )
                except Exception as e:
                    self.log(f"💥 第 {i} 个失败：{type(e).__name__}: {e}")
            self.log(f"🎉 一键流程结束，完成 {len(polished)}/{len(srcs)} 个。")

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()

    def _on_close(self):
        close_server()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
