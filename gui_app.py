# -*- coding: utf-8 -*-
"""视频/音频转录与文本精校：业务逻辑与程序入口。

推理由 llama-server 子进程承担，界面在 ui_qt.py。
本模块不含任何界面代码，便于单独复用与测试。
"""
import os
import sys
import threading
import tempfile

import numpy as np

from asr_server import Server, MODEL_TIERS
from chunker import split_by_energy

SAMPLE_RATE = 16000
# 单块目标时长。注意力开销随长度平方增长，且块越短越容易把并发槽排满；
# 实测 60 秒块在 8 槽下可达 75 倍实时，整段送只有 2 倍。
CHUNK_SECONDS = 60.0
# 正常语速一秒 3～5 字，60 秒块上限约 300 token；给 600 留足余量，
# 同时避免偶发失控的块空转，拖住同批其他请求。
MAX_TOKENS = 600


def _import_av():
    import av
    return av


def extract_audio(input_path: str, log) -> str:
    """把音视频文件里的音频抽成 16kHz 单声道 WAV，返回临时文件路径。

    调用方负责删除返回的临时文件。
    """
    import soundfile as sf

    av = _import_av()
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


def transcribe_audio(audio_path: str, output_path: str, model_tier: str, log, on_chunk=None):
    server = get_server(model_tier, log)

    wav = _load_wav(audio_path)
    total_sec = len(wav) / SAMPLE_RATE
    chunks = split_by_energy(wav, SAMPLE_RATE, CHUNK_SECONDS)
    log(f"🎙️ 转录中：{total_sec/60:.1f} 分钟音频，切成 {len(chunks)} 段，"
        f"{server.slots} 路并发…")

    done = 0
    done_lock = threading.Lock()

    def chunk_done():
        nonlocal done
        if on_chunk is None:
            return
        with done_lock:
            done += 1
            on_chunk(done, len(chunks))

    texts = server.transcribe_chunks(
        [c for c, _ in chunks], SAMPLE_RATE, MAX_TOKENS, chunk_done
    )

    text = "".join(texts).strip()
    if not text:
        raise RuntimeError("转录结果为空：该音频没有可识别的语音。")

    # 直接输出成段落形式，用户拿到就能读，不必再跑一遍精校
    text = paragraphize(text)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    chars = len(text.replace("\n", ""))
    log(f"✅ 转录完成：{output_path}（{chars} 字）")


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


def pipeline_transcribe(input_file: str, model_tier: str, log, out_dir: str | None = None, on_chunk=None):
    tmp_path = None
    try:
        log(f"📁 提取音频：{input_file}")
        tmp_path = extract_audio(input_file, log)
        out = transcript_path(input_file, out_dir)
        transcribe_audio(tmp_path, out, model_tier, log, on_chunk)
        return out
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            os.remove(tmp_path)


def pipeline_transcribe_many(input_files, model_tier, log, out_dir: str | None = None,
                             on_progress=None):
    """批量转录，返回成功产出的结果路径列表。

    单个文件失败不中断整批：记日志后继续下一个。
    on_progress(第几个, 总数, 当前文件已处理块数, 当前文件总块数, 当前文件路径)
    """
    outputs = []
    n = len(input_files)
    for i, src in enumerate(input_files, 1):
        log(f"===== [{i}/{n}] {os.path.basename(src)} =====")
        try:
            def chunk(d, t, _i=i, _src=src):
                if on_progress is not None:
                    on_progress(_i, n, d, t, _src)

            outputs.append(pipeline_transcribe(src, model_tier, log, out_dir, chunk))
        except Exception as e:
            log(f"💥 第 {i} 个失败：{type(e).__name__}: {e}")
    return outputs


TIER_LABELS = {
    "fast": "极速 · 0.6B Q8_0（下载 1.0 GB，显存约 3 GB）",
    "balanced": "推荐 · 1.7B Q8_0（下载 2.5 GB，显存约 5.5 GB）",
    "precise": "高精 · 1.7B bf16（下载 4.7 GB，显存约 7.3 GB）",
}


def available_tiers() -> list[str]:
    """返回本地已存在完整模型文件的档位。"""
    from asr_server import models_dir

    mdir = models_dir()
    return [
        key for key, (model, mmproj) in MODEL_TIERS.items()
        if os.path.isfile(os.path.join(mdir, model))
        and os.path.isfile(os.path.join(mdir, mmproj))
    ]


def main():
    from ui_qt import run_gui
    run_gui()


if __name__ == "__main__":
    main()
