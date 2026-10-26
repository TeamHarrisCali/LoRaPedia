"""Hardware detection and model suggestions for the setup wizard. Read-only and offline."""

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _ram_gb() -> float:
    m = re.search(r"MemTotal:\s+(\d+) kB", _read("/proc/meminfo"))
    return round(int(m.group(1)) / 1024 / 1024, 1) if m else 0.0


def _cpu_model() -> str:
    m = re.search(r"(?:model name|Model)\s*:\s*(.+)", _read("/proc/cpuinfo"))
    return m.group(1).strip() if m else platform.processor() or platform.machine()


def _board() -> str | None:
    model = _read("/proc/device-tree/model").strip("\x00 \n").lower()
    if "raspberry pi" in model:
        return "Raspberry Pi"
    if "jetson" in model or "nvidia" in model or Path("/etc/nv_tegra_release").exists():
        return "NVIDIA Jetson"
    return None


def _gpu() -> dict | None:
    """First NVIDIA GPU via nvidia-smi, if present. (AMD/Intel are not probed: CPU path is fine.)"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        name, mem_mib = [x.strip() for x in out[0].split(",")]
        return {"name": name, "vram_gb": round(int(mem_mib) / 1024, 1)}
    except Exception:
        return None


def detect_hardware(data_dir: str | Path = ".") -> dict:
    path = Path(data_dir)
    path = path if path.exists() else Path(".")
    return {
        "cpu": _cpu_model(), "cores": os.cpu_count() or 1, "ram_gb": _ram_gb(), "arch": platform.machine(),
        "gpu": _gpu(), "board": _board(), "disk_free_gb": round(shutil.disk_usage(path).free / 1e9, 1),
        "os": platform.system(),
    }


# (tag, download GB, minimum RAM GB for CPU use, blurb). Small models are fine here: answers are ~200 bytes.
LLM_CATALOG = [
    ("llama3.2:1b", 1.3, 3, "Tiny and fast. Good for Raspberry Pi and older laptops; answers are plainer."),
    ("llama3.2:3b", 2.0, 6, "Best balance of speed and quality for a mini PC or laptop."),
    ("llama3.1:8b", 4.9, 12, "Most accurate. Needs a strong CPU or a GPU to feel responsive."),
]
EMBED_CATALOG = [
    ("nomic-embed-text", 0.27, "Recommended. Good retrieval quality, runs on CPU."),
    ("all-minilm", 0.05, "Very small. Use on the weakest hardware."),
]


def suggest_models(hw: dict) -> dict:
    """Pick one recommended LLM for this hardware, and flag which others will feel slow."""
    ram, gpu = hw["ram_gb"], hw.get("gpu")
    vram = gpu["vram_gb"] if gpu else 0
    # RAM decides what fits; only a capable GPU makes a bigger model fast enough to be pleasant.
    if ram < 6 or (hw.get("board") == "Raspberry Pi" and ram < 8):
        pick = "llama3.2:1b"
    elif gpu and vram >= 6 and ram >= 12:
        pick = "llama3.1:8b"
    else:
        pick = "llama3.2:3b"
    speed = "fast (GPU)" if gpu else "slower (CPU only)"
    llms = [{"tag": t, "size_gb": gb, "blurb": blurb, "recommended": t == pick, "fits": ram >= min_ram}
            for t, gb, min_ram, blurb in LLM_CATALOG]
    embeds = [{"tag": t, "size_gb": gb, "blurb": b, "recommended": t == (
        "all-minilm" if pick == "llama3.2:1b" and ram < 4 else "nomic-embed-text")} for t, gb, b in EMBED_CATALOG]
    return {"llm": llms, "embed": embeds, "speed": speed,
            "note": ("No GPU found: answers take a few seconds to a minute. That is fine for a mesh."
                     if not gpu else f"GPU found ({gpu['name']}): models will run quickly.")}
