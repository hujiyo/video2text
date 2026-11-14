# -*- coding: utf-8 -*-
"""从 ModelScope 下载模型权重。

国内直连，不需要代理。支持断点续传、大小校验与进度回调。
用户也可以在本地选择已有模型，不必联网。
"""
import os
import threading
import urllib.request

MODELSCOPE_BASE = "https://modelscope.cn/models/ggml-org"

# 档位 -> (仓库名, [(文件名, 字节数), ...])
# 大小用于校验下载完整性，避免把半截文件当成模型。
MODEL_FILES = {
    "fast": (
        "Qwen3-ASR-0.6B-GGUF",
        [
            ("Qwen3-ASR-0.6B-Q8_0.gguf", 804749248),
            ("mmproj-Qwen3-ASR-0.6B-Q8_0.gguf", 214392480),
        ],
    ),
    "balanced": (
        "Qwen3-ASR-1.7B-GGUF",
        [
            ("Qwen3-ASR-1.7B-Q8_0.gguf", 2165034944),
            ("mmproj-Qwen3-ASR-1.7B-Q8_0.gguf", 355709344),
        ],
    ),
    "precise": (
        "Qwen3-ASR-1.7B-GGUF",
        [
            ("Qwen3-ASR-1.7B-bf16.gguf", 4069674944),
            ("mmproj-Qwen3-ASR-1.7B-bf16.gguf", 641773984),
        ],
    ),
}

TIER_SIZES = {key: sum(size for _, size in files) for key, (_, files) in MODEL_FILES.items()}


def url_for(repo: str, filename: str) -> str:
    return f"{MODELSCOPE_BASE}/{repo}/resolve/master/{filename}"


def find_local_model(directory: str) -> str | None:
    """检查目录里有哪些档位的模型是完整可用的，返回档位名或 None。"""
    if not os.path.isdir(directory):
        return None
    for tier, (_, files) in MODEL_FILES.items():
        if all(
            os.path.isfile(os.path.join(directory, name))
            and os.path.getsize(os.path.join(directory, name)) == size
            for name, size in files
        ):
            return tier
    return None


def download_file(url: str, dest: str, expect_size: int, on_progress, cancel_event=None):
    """下载单个文件到 dest，已存在且大小正确则跳过。

    on_progress(downloaded_bytes, total_bytes) 由调用方负责节流。
    """
    if os.path.isfile(dest) and os.path.getsize(dest) == expect_size:
        on_progress(expect_size, expect_size)
        return

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if have >= expect_size:
        os.remove(part)
        have = 0

    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = expect_size
        mode = "ab" if have else "wb"
        with open(part, mode) as f:
            done = have
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("下载已取消")
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                on_progress(done, total)

    got = os.path.getsize(part)
    if got != expect_size:
        raise IOError(
            f"{os.path.basename(dest)} 下载不完整：期望 {expect_size} 字节，实际 {got} 字节"
        )
    os.replace(part, dest)


def download_tier(tier: str, models_dir: str, on_progress, cancel_event=None):
    """下载某档位的全部文件。on_progress(done, total, 当前文件名)。

    已经完整存在的文件直接跳过，所以用户可以先用别的方式拿到模型，
    再让这里只补缺的部分。
    """
    repo, files = MODEL_FILES[tier]
    total_all = sum(size for _, size in files)

    done_files = [0.0] * len(files)
    lock = threading.Lock()

    def make_cb(index):
        def cb(done, total):
            with lock:
                done_files[index] = done
                on_progress(sum(done_files), total_all, files[index][0])

        return cb

    # 先扫一遍已存在的文件，把进度直接推进到实际状态
    for i, (name, size) in enumerate(files):
        path = os.path.join(models_dir, name)
        if os.path.isfile(path) and os.path.getsize(path) == size:
            done_files[i] = size
    on_progress(sum(done_files), total_all, "")

    for i, (name, size) in enumerate(files):
        dest = os.path.join(models_dir, name)
        download_file(url_for(repo, name), dest, size, make_cb(i), cancel_event)
