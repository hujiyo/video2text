# -*- coding: utf-8 -*-
"""能量切分：在低能量处断开，保证拼接无损。"""
import numpy as np


def split_by_energy(wav, sr, max_sec=60.0, search_expand_sec=5.0, min_window_ms=100.0):
    """按最大时长切分，切点落在附近的低能量位置。

    切点只在 [cut-expand, cut+expand] 窗口内搜索，所以每块长度在
    (max_sec-expand, max_sec+expand) 之间。所有块首尾相接，拼回去与原音频
    逐样本相同，不会丢字也不会重复。

    返回 [(chunk_ndarray, offset_sec), ...]。
    """
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.mean(axis=-1)

    total = len(wav)
    max_len = int(max_sec * sr)
    if total <= max_len:
        return [(wav, 0.0)]

    expand = int(search_expand_sec * sr)
    win = max(4, int(min_window_ms / 1000.0 * sr))

    chunks = []
    start = 0
    offset = 0.0
    while total - start > max_len:
        cut = start + max_len
        left = max(start, cut - expand)
        right = min(total, cut + expand)

        if right - left <= win:
            boundary = cut
        else:
            seg = np.abs(wav[left:right])
            # 滑动窗求最小能量处
            sums = np.convolve(seg, np.ones(win, dtype=np.float32), mode="valid")
            pos = int(np.argmin(sums))
            # 窗内再取最安静的那一点
            inner = int(np.argmin(seg[pos:pos + win]))
            boundary = left + pos + inner

        boundary = max(min(boundary, total), start + 1)
        chunks.append((wav[start:boundary], offset))
        offset += (boundary - start) / sr
        start = boundary

    chunks.append((wav[start:], offset))
    return chunks


def probe(wav_path):
    import soundfile as sf
    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    chunks = split_by_energy(data, sr, 60.0)
    total = 0
    for c, off in chunks:
        total += len(c)
    print(f"块数 {len(chunks)}  原始样本 {len(data)}  拼接样本 {total}  无损 {len(data)==total}")
    lens = [len(c) / sr for c, _ in chunks]
    print(f"块长范围 {min(lens):.1f}~{max(lens):.1f} 秒  平均 {sum(lens)/len(lens):.1f} 秒")
    return chunks
