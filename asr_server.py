# -*- coding: utf-8 -*-
"""llama-server 子进程管理与并发转录。

设计要点：
- 模型推理完全交给 llama-server.exe，本进程只负责调度。
- 服务崩溃不影响界面；重启即可恢复。
- 并发用槽位（-np），每个槽位独立 KV cache，谁先完成谁先返回。
"""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

def _app_root() -> str:
    """程序根目录（存放 runtime 与随包资源）。

    PyInstaller 打包后 __file__ 落在 _internal 里，而 runtime 和 exe 同级。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _models_root() -> str:
    """模型默认位置。

    优先选一个剩余空间足够的非系统盘。模型动辄 2~5GB，放 C 盘很容易
    把系统盘塞满。都找不到合适的盘时退回用户数据目录。
    """
    import shutil

    need = 6 * 1024**3  # 最大档约 4.7GB，留些余量
    for letter in ("D", "E", "F"):
        drive = f"{letter}:\\"
        if not os.path.isdir(drive):
            continue
        try:
            if shutil.disk_usage(drive).free >= need:
                return os.path.join(drive, "Video2Text", "models")
        except OSError:
            continue
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, "Video2Text", "models")
    return os.path.join(_app_root(), "models")


def _config_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Video2Text", "config.json")


def load_config() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**kwargs):
    cfg = load_config()
    cfg.update(kwargs)
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def models_dir() -> str:
    """当前生效的模型目录，优先取用户配置。"""
    configured = load_config().get("models_dir")
    return configured if configured else _models_root()


HERE = _app_root()
BIN_DIR = os.path.join(HERE, "runtime")

# 模型档位：名称 -> (主模型文件名, mmproj 文件名)
MODEL_TIERS = {
    "fast": ("Qwen3-ASR-0.6B-Q8_0.gguf", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"),
    "balanced": ("Qwen3-ASR-1.7B-Q8_0.gguf", "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"),
    "precise": ("Qwen3-ASR-1.7B-bf16.gguf", "mmproj-Qwen3-ASR-1.7B-bf16.gguf"),
}


def _find_free_port(start=18080, end=18180):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("本地端口全被占用，无法启动推理服务。")


class Server:
    """一个 llama-server 子进程，外加并发转录接口。"""

    def __init__(self, model_tier, log, ctx_per_slot=2048, max_slots=8):
        self.tier = model_tier
        self.log = log
        self.port = None
        self.proc = None
        self.slots = 1
        self._lock = threading.Lock()

        model_name, mmproj_name = MODEL_TIERS[model_tier]
        mdir = models_dir()
        self.model_path = os.path.join(mdir, model_name)
        self.mmproj_path = os.path.join(mdir, mmproj_name)
        for p in (self.model_path, self.mmproj_path):
            if not os.path.isfile(p):
                raise FileNotFoundError(f"模型文件缺失：{p}")

        free_gb = self._free_vram_gb()
        self.slots = self._plan_slots(free_gb, max_slots)
        self.ctx_total = ctx_per_slot * self.slots
        self._start(free_gb)

    @staticmethod
    def _free_vram_gb():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            first = out.stdout.strip().splitlines()[0]
            return float(first) / 1024
        except Exception:
            return 0.0

    def _plan_slots(self, free_gb, cap):
        """槽位越多吞吐越高，但 KV cache 随槽位线性增长，贴满显存会掉速。"""
        if free_gb <= 0:
            raise RuntimeError("未检测到 NVIDIA 显卡。本工具需要 CUDA 显卡。")
        # 实测：1.7B Q8_0 + 2048 上下文每槽约 0.35GB；0.6B 约 0.2GB。
        per_slot = 0.2 if self.tier == "fast" else 0.35
        n = int(free_gb * 0.75 // per_slot)
        n = max(1, min(cap, n))
        self.log(f"🎛️ 显存余 {free_gb:.1f}GB，每槽约 {per_slot}GB，开 {n} 个并发槽。")
        return n

    def _start(self, free_gb):
        exe = os.path.join(BIN_DIR, "llama-server.exe")
        if not os.path.isfile(exe):
            raise FileNotFoundError(f"缺少推理程序：{exe}")

        self.port = _find_free_port()
        cmd = [
            exe,
            "-m", self.model_path,
            "--mmproj", self.mmproj_path,
            "-ngl", "99",
            "-c", str(self.ctx_total),
            "-np", str(self.slots),
            "-lv", "1",
            "--no-cache-prompt",   # 缓存会让计时与结果失真，且音频无复用价值
            "--port", str(self.port),
            "--host", "127.0.0.1",
        ]
        self.log(f"🚀 启动推理服务（{self.slots} 槽 / 总上下文 {self.ctx_total}）…")
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self._wait_ready(timeout=180)

    def _wait_ready(self, timeout):
        url = f"http://127.0.0.1:{self.port}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"推理服务启动失败，退出码 {self.proc.returncode}。")
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        self.log("✅ 推理服务就绪。")
                        return
            except Exception:
                time.sleep(1)
        raise RuntimeError("推理服务启动超时。")

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ---------- 转录 ----------

    def transcribe_chunks(self, chunks, sr, max_tokens=600, on_done=None):
        """把若干音频块并发送进服务，按原顺序返回文本列表。

        串行提交但并发执行：ThreadPoolExecutor 的 size 等于槽位数，
        超出槽位的请求会在本地排队，不会把服务打爆。
        on_done() 每完成一块回调一次，供界面更新进度。
        """
        def one(chunk):
            return self._request(chunk, sr, max_tokens)

        texts = [None] * len(chunks)
        with ThreadPoolExecutor(max_workers=self.slots) as pool:
            futures = {pool.submit(one, chunk): i for i, chunk in enumerate(chunks)}
            for fut in as_completed(futures):
                texts[futures[fut]] = fut.result()
                if on_done is not None:
                    on_done()
        return texts

    def _request(self, wav, sr, max_tokens):
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((wav * 32767).astype("int16").tobytes())
        b64 = base64.b64encode(buf.getvalue()).decode()

        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "input_audio",
                     "input_audio": {"data": "data:audio/wav;base64," + b64}},
                    {"type": "text", "text": "请转录这段音频"},
                ],
            }],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "cache_prompt": False,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.load(resp)
        text = data["choices"][0]["message"]["content"]
        return _strip_asr_marker(text)


def _strip_asr_marker(text):
    """服务输出形如 `language Chinese<asr_text>正文`，只保留正文。"""
    marker = "<asr_text>"
    idx = text.find(marker)
    return text[idx + len(marker):].strip() if idx >= 0 else text.strip()
