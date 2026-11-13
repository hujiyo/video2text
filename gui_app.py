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

if not os.environ.get("HF_HOME"):
    # 模型约 4.7GB；用户目录在 C 盘时容易不够，默认放到程序所在目录下的 models/。
    os.environ["HF_HOME"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "models"
    )

import numpy as np

MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
LANGUAGE = "Chinese"


def _import_av():
    import av
    import soundfile as sf
    return av, sf


def extract_audio(input_path: str, log) -> str:
    av, sf = _import_av()
    container = av.open(input_path)
    try:
        audio_stream = next(s for s in container.streams if s.type == "audio")
    except StopIteration:
        container.close()
        raise ValueError("该文件不包含音频轨道。")

    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=16000
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
    sf.write(tmp.name, audio, 16000)
    return tmp.name


_model_lock = threading.Lock()
_model = None


def load_model(log):
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        import torch
        from qwen_asr import Qwen3ASRModel

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        log(f"⏳ 正在加载 {MODEL_ID}（设备：{device}），首次运行需下载，请耐心等待...")
        _model = Qwen3ASRModel.from_pretrained(
            MODEL_ID,
            dtype=dtype,
            device_map=device,
            max_inference_batch_size=8,
            max_new_tokens=4096,
        )
        log("✅ 模型加载完成。")
        return _model


def transcribe_audio(audio_path: str, output_path: str, log):
    model = load_model(log)
    log("🎙️ 开始转录...")
    result = model.transcribe(audio=[audio_path], language=LANGUAGE)[0]
    text = result.text.strip()

    if not text:
        raise RuntimeError("转录结果为空：该音频没有可识别的语音。")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"✅ 转录完成：{output_path}")


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


def pipeline_transcribe(input_file: str, log):
    tmp_path = None
    try:
        log(f"📁 提取音频：{input_file}")
        tmp_path = extract_audio(input_file, log)
        base_name = os.path.splitext(input_file)[0]
        out = f"{base_name}_转录结果.txt"
        transcribe_audio(tmp_path, out, log)
        log("🎯 转录流程完成。")
        return out
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            os.remove(tmp_path)


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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("视频转录 & 文本精校工具")
        root.geometry("820x620")
        root.minsize(700, 540)

        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_transcript: str | None = None
        self.last_polished: str | None = None

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        pad = {" padx": 8, "pady": 4}  # noqa
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        # ===== 输入文件选择 =====
        row = 0
        ttk.Label(top, text="步骤 1：选择 视频/音频 文件").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        row += 1
        self.var_src = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_src).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=(0, 6)
        )
        ttk.Button(top, text="浏览…", command=self._browse_src).grid(
            row=row, column=2, sticky="w"
        )
        top.columnconfigure(0, weight=1)

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
        ttk.Label(top, text="步骤 2：选择 转录结果 .txt 文件（可自动用上一步的结果）").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        row += 1
        self.var_txt = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_txt).grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=(0, 6)
        )
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
    def _browse_src(self):
        p = filedialog.askopenfilename(
            title="选择视频/音频",
            filetypes=[
                ("音视频", "*.mp4 *.mkv *.mov *.avi *.flv *.wav *.mp3 *.m4a *.aac *.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if p:
            self.var_src.set(p)

    def _browse_txt(self):
        p = filedialog.askopenfilename(
            title="选择转录结果 txt", filetypes=[("文本", "*.txt"), ("所有文件", "*.*")]
        )
        if p:
            self.var_txt.set(p)

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
        state = ("disabled" if busy else "!disabled",)
        for b in (
            self.btn_transcribe,
            self.btn_open_transcript,
            self.btn_polish,
            self.btn_open_polished,
            self.btn_all,
            self.btn_clear,
        ):
            try:
                b.state(state if busy else list(state))
            except tk.TclError:
                pass
        self.var_status.set("处理中…" if busy else "就绪。")

    def _worker(self, fn, on_done):
        try:
            fn()
        except Exception as e:
            self.log(f"💥 出错：{e}")
        finally:
            self.root.after(0, on_done)

    def _on_transcribe(self):
        src = self.var_src.get().strip().strip('"').strip("'")
        if not src or not os.path.isfile(src):
            messagebox.showerror("提示", "请先选择有效的 视频/音频 文件。")
            return

        def job():
            out = pipeline_transcribe(src, self.log)
            self.last_transcript = out
            self.root.after(0, lambda: self.btn_open_transcript.state(["!disabled"]))
            if not self.var_txt.get().strip():
                self.var_txt.set(out)

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()

    def _on_polish(self):
        src = self.var_txt.get().strip().strip('"').strip("'")
        if not src or not os.path.isfile(src):
            messagebox.showerror("提示", "请先选择有效的 转录结果 txt 文件。")
            return

        def job():
            out = pipeline_polish(src, self.log)
            self.last_polished = out
            self.root.after(0, lambda: self.btn_open_polished.state(["!disabled"]))

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()

    def _on_pipeline(self):
        src = self.var_src.get().strip().strip('"').strip("'")
        if not src or not os.path.isfile(src):
            messagebox.showerror("提示", "一键流程需要先选择 视频/音频 文件。")
            return

        def job():
            self.log("===== 步骤 1/2 转录 =====")
            out1 = pipeline_transcribe(src, self.log)
            self.last_transcript = out1
            self.root.after(0, lambda: self.btn_open_transcript.state(["!disabled"]))
            self.var_txt.set(out1)
            self.log("===== 步骤 2/2 精校 =====")
            out2 = pipeline_polish(out1, self.log)
            self.last_polished = out2
            self.root.after(0, lambda: self.btn_open_polished.state(["!disabled"]))
            self.log("🎉 一键流程全部完成。")

        def done():
            self._set_busy(False)

        self._set_busy(True)
        self.worker = threading.Thread(target=self._worker, args=(job, done), daemon=True)
        self.worker.start()


def main():
    try:
        import av  # noqa
    except ImportError:
        pass  # 稍后真正用到时再报，避免启动崩溃
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()