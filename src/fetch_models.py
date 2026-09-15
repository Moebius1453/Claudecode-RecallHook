""

import pathlib
import sys
import time
import urllib.request

PROXY = "http://127.0.0.1:7890"
ROOT = pathlib.Path(r"D:\ClaudecodeMem\main\models")

FILES = [
    
    ("huggingface.co", "CompendiumLabs/bge-small-zh-v1.5-gguf",
     "bge-small-zh-v1.5-f16.gguf", ROOT / "bge-small-zh-v1.5" / "bge-small-zh-v1.5-f16.gguf"),
    
    ("huggingface.co", "Xenova/bge-small-zh-v1.5",
     "onnx/model.onnx", ROOT / "bge-small-zh-v1.5-onnx" / "model.onnx"),
    ("huggingface.co", "Xenova/bge-small-zh-v1.5",
     "tokenizer.json", ROOT / "bge-small-zh-v1.5-onnx" / "tokenizer.json"),
    ("huggingface.co", "Xenova/bge-small-zh-v1.5",
     "config.json", ROOT / "bge-small-zh-v1.5-onnx" / "config.json"),
    ("huggingface.co", "Xenova/bge-small-zh-v1.5",
     "special_tokens_map.json", ROOT / "bge-small-zh-v1.5-onnx" / "special_tokens_map.json"),
    ("huggingface.co", "Xenova/bge-small-zh-v1.5",
     "vocab.txt", ROOT / "bge-small-zh-v1.5-onnx" / "vocab.txt"),
]

MIRRORS = [
    ("hf-mirror", "https://hf-mirror.com", None),
    ("proxy", "https://huggingface.co", PROXY),
]


def fetch(url: str, proxy: str | None, timeout: float = 20):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()
    return opener.open(request, timeout=timeout)


def download(repo: str, filename: str, target: pathlib.Path) -> bool:
    if target.exists() and target.stat().st_size > 1024:
        print(f"[skip] {target.name} 已存在 ({target.stat().st_size // 1024} KB)")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    for label, base, proxy in MIRRORS:
        url = f"{base}/{repo}/resolve/main/{filename}"
        try:
            with fetch(url, proxy) as response:
                total = int(response.headers.get("Content-Length") or 0)
                print(f"[get ] {target.name} <- {label} ({total // 1024} KB)", flush=True)
                start = time.time()
                written = 0
                with target.open("wb") as handle:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if total and written % (8 << 20) < (1 << 20):
                            print(f"       {written * 100 // total}%", flush=True)
                rate = written / max(time.time() - start, 0.01) / 1024
                print(f"[ok  ] {target.name} {written // 1024} KB  {rate:.0f} KB/s", flush=True)
                return True
        except Exception as error:
            print(f"[fail] {target.name} via {label}: {type(error).__name__} "
                  f"{str(error)[:60]}", flush=True)
            if target.exists():
                target.unlink()
    return False


ok = True
for _, repo, filename, target in FILES:
    if not download(repo, filename, target):
        ok = False

print("\n=== 结果 ===")
for target in sorted(ROOT.rglob("*")):
    if target.is_file():
        print(f"  {target.relative_to(ROOT)}  {target.stat().st_size // 1024} KB")
sys.exit(0 if ok else 1)
