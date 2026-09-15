""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MAIN_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = MAIN_DIR / "data" / "services.json"
LOG_DIR = MAIN_DIR / "logs"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB

LLAMA = Path(r"D:\Tookit\llama\llama-server.exe")



SERVICES = {
    "embed": (
        [str(LLAMA), "-m", str(MAIN_DIR / "models" / "bge-small-zh-v1.5"
                                 / "bge-small-zh-v1.5-f16.gguf"),
         "--embeddings", "-ngl", "0", "-c", "512",
         "--port", "8765", "--host", "127.0.0.1"],
        "http://127.0.0.1:8765/health",
    ),
    "qwen": (
        [str(LLAMA), "-m", r"D:\Tookit\llama\models\Qwen3-8B-IQ4_XS.gguf",
         "-ngl", "999", "-np", "1", "-c", "8192",
         "--port", "8080", "--host", "127.0.0.1"],
        "http://127.0.0.1:8080/health",
    ),
}


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def alive(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def pid_running(pid: int) -> bool:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
        capture_output=True, text=True)
    return result.stdout.strip().lower() == "true"


def cmd_start(args) -> int:
    state = load_state()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = 0
    for name, (command, health) in SERVICES.items():
        if alive(health):
            print(f"  {name:<6} 已在跑，跳过")
            continue
        if not Path(command[0]).exists():
            print(f"  {name:<6} 跳过：找不到 {command[0]}")
            continue
        log = (LOG_DIR / f"{name}.log").open("ab")
        process = subprocess.Popen(command, stdout=log, stderr=log,
                                   stdin=subprocess.DEVNULL, creationflags=FLAGS)
        state[name] = process.pid
        started += 1
        print(f"  {name:<6} 已启动 PID {process.pid}（日志 logs/{name}.log）")
    save_state(state)

    for name, (_, health) in SERVICES.items():
        for _ in range(int(args.wait / 2)):
            if alive(health):
                print(f"  {name:<6} 就绪")
                break
            time.sleep(2)
        else:
            print(f"  {name:<6} {args.wait} 秒内没就绪 —— 看 logs/{name}.log")
    return 0


def cmd_status(args) -> int:
    state = load_state()
    for name, (command, health) in SERVICES.items():
        up = alive(health)
        recorded = state.get(name)
        note = ""
        if recorded and not pid_running(int(recorded)) and up:
            note = f"（本脚本记的 PID {recorded} 已不在，但端口有服务 —— 别人起的）"
        elif recorded and pid_running(int(recorded)):
            note = f"（PID {recorded}）"
        print(f"  {name:<6} {'在跑' if up else '没跑':<4} {health}{note}")
    return 0


def cmd_stop(args) -> int:
    state = load_state()
    killed = 0
    for name in SERVICES:
        pid = state.pop(name, None)
        if pid and pid_running(int(pid)):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True)
            print(f"  {name:<6} 已停 PID {pid}")
            killed += 1
    save_state(state)
    if args.all:
        result = subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                                capture_output=True, text=True, errors="replace")
        print(f"  --all：{result.stdout.strip() or result.stderr.strip()}")
        killed += 1
    if not killed:
        print("  没有本脚本起过的服务在跑")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="本地模型服务启停（脱离 agent 进程树）。")
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--wait", type=int, default=90, help="启动后等多久就绪")
    parser.add_argument("--all", action="store_true",
                        help="stop 时按进程名停掉所有 llama-server")
    args = parser.parse_args()
    sys.exit({"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.action](args))


if __name__ == "__main__":
    main()
