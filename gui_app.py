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
SAMPLE_RATE = 16000


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
            max_inference_batch_size=1,
            max_new_tokens=4096,
        )
        log("✅ 模型加载完成。")
        return _model


# 单条音频一次推理的时长上限。注意力开销随长度平方增长，20 分钟的整块在
# 8GB 显卡上直接显存溢出；切成小块后总吞吐反而更高（实测 15 分钟素材，
# 600 秒块要 99 秒、60 秒块只要 20 秒），且切碎后逐字判断更准，不丢内容。
CHUNK_SECONDS = 60


def _plan_batch(model, chunk_seconds, log):
    """按块长和当前可用显存设定批大小与单条生成上限。

    模型加载后显存已被权重占住，剩余空间才是推理可用的。实测 60 秒音频在
    bf16 下每条约 0.35GB 峰值，取 80% 余量避免注意力峰值波动顶满。
    批大小上限 16：再往上批处理收益已趋平，显存峰值却仍线性增长。

    生成上限按块长收紧。正常语音一秒约 3～5 个汉字，60 秒块最多约 300 个
    token；给到 4096 会让偶发失控的块空转几十倍时长，而批处理时这一块会把
    同批其余音频一起拖住。
    """
    import torch

    model.max_new_tokens = int(chunk_seconds * 8) + 64

    if not torch.cuda.is_available():
        model.max_inference_batch_size = 1
        return

    free = torch.cuda.mem_get_info()[0] / 1e9
    per_item = 0.35 * (chunk_seconds / 60.0)
    batch = min(16, max(1, int(free * 0.8 // per_item)))
    model.max_inference_batch_size = batch
    log(f"📐 显存余 {free:.1f}GB，每条音频按 {per_item:.2f}GB 估算，批大小取 {batch}。")


def _load_wav(path):
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"音频采样率应为 {SAMPLE_RATE}Hz，实际 {sr}Hz：{path}")
    return data


def transcribe_audio(audio_path: str, output_path: str, log):
    model = load_model(log)
    _plan_batch(model, CHUNK_SECONDS, log)

    import torch
    from qwen_asr.inference.utils import split_audio_into_chunks

    wav = _load_wav(audio_path)
    total_sec = len(wav) / SAMPLE_RATE
    chunks = split_audio_into_chunks(wav, SAMPLE_RATE, CHUNK_SECONDS)
    batch = model.max_inference_batch_size
    log(f"🎙️ 开始转录：{total_sec/60:.1f} 分钟音频，切成 {len(chunks)} 段，每批 {batch} 段...")

    texts = []
    done_sec = 0.0
    for start in range(0, len(chunks), batch):
        group = chunks[start : start + batch]
        wavs = [(c, SAMPLE_RATE) for c, _ in group]
        texts.extend(r.text for r in model.transcribe(audio=wavs, language=LANGUAGE))
        done_sec += sum(len(c) for c, _ in group) / SAMPLE_RATE
        log(f"  已转 {done_sec/60:.1f}/{total_sec/60:.1f} 分钟"
            f"（段 {min(start + batch, len(chunks))}/{len(chunks)}）")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    text = "".join(texts).strip()
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


def pipeline_transcribe(input_file: str, log, out_dir: str | None = None):
    tmp_path = None
    try:
        log(f"📁 提取音频：{input_file}")
        tmp_path = extract_audio(input_file, log)
        out = transcript_path(input_file, out_dir)
        transcribe_audio(tmp_path, out, log)
        log("🎯 转录流程完成。")
        return out
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            os.remove(tmp_path)


def transcript_path(input_file: str, out_dir: str | None = None):
    """转录结果路径：与源文件同名，后缀换成 .txt。

    给了 out_dir 就放到该目录下（批量任务常用），否则落在源文件旁边。
    """
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


def pipeline_transcribe_many(input_files, log, out_dir: str | None = None):
    """批量转录，返回成功产出的结果路径列表。

    单个文件失败不中断整批：记日志后继续下一个。
    """
    outputs = []
    for i, src in enumerate(input_files, 1):
        log(f"===== [{i}/{len(input_files)}] {os.path.basename(src)} =====")
        try:
            outputs.append(pipeline_transcribe(src, log, out_dir))
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
        ttk.Label(top, text="步骤 1：选择 视频/音频 文件（可多选，一行一个）").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
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

        def job():
            out_dir = self.var_outdir.get().strip() or None
            outs = pipeline_transcribe_many(srcs, self.log, out_dir)
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

        def job():
            out_dir = self.var_outdir.get().strip() or None
            polished = []
            for i, src in enumerate(srcs, 1):
                self.log(f"===== [{i}/{len(srcs)}] {os.path.basename(src)} =====")
                try:
                    out1 = pipeline_transcribe(src, self.log, out_dir)
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