""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "bge-small-zh-v1.5-onnx"
MAX_LENGTH = 512


class Encoder:
    ""

    def __init__(self, model_dir: Path, threads: int = 0):
        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        if not model_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(f"模型目录不完整：{model_dir}")

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=MAX_LENGTH)
        self.tokenizer.enable_padding()
        self.lock = threading.Lock()

    def encode(self, texts: list[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(list(texts))
        ids = np.array([item.ids for item in encodings], dtype=np.int64)
        mask = np.array([item.attention_mask for item in encodings], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        with self.lock:
            outputs = self.session.run(None, feed)
        pooled = outputs[0][:, 0]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return pooled / norms


def make_handler(encoder: Encoder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/health"):
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.startswith("/v1/embeddings"):
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                texts = payload.get("input")
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not texts:
                    self._send(400, {"error": "input 必须是非空字符串数组"})
                    return
                matrix = encoder.encode([str(item) for item in texts])
                self._send(200, {
                    "data": [
                        {"index": index, "embedding": row.tolist()}
                        for index, row in enumerate(matrix)
                    ]
                })
            except Exception as error:  
                self._send(500, {"error": f"{type(error).__name__}: {error}"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="ONNX 嵌入服务（路线 B）")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args()

    start = time.perf_counter()
    try:
        encoder = Encoder(args.model_dir, args.threads)
    except Exception as error:  
        print(f"加载失败：{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
    load_ms = (time.perf_counter() - start) * 1000
    dim = encoder.encode(["预热"]).shape[1]
    print(f"模型就绪：{load_ms:.0f} ms 加载，dim={dim}，"
          f"输入 {sorted(encoder.input_names)}", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(encoder))
    print(f"监听 http://{args.host}:{args.port}/v1/embeddings", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
