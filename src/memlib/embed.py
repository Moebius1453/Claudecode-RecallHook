""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING
import urllib.error
import urllib.request

if TYPE_CHECKING:
    import numpy as np


@dataclass
class NullEmbedder:
    model_id: str = "none"
    dim: int = 0
    available: bool = False
    reason: str = "embed-serve 未启用"

    def embed(self, texts: list[str]) -> "np.ndarray":
        raise RuntimeError(self.reason)


@dataclass
class HttpEmbedder:
    endpoint: str
    model_id: str
    dim: int
    timeout_s: float = 2.0

    available: bool = True
    reason: str = ""

    def embed(self, texts: list[str]) -> "np.ndarray":
        import numpy as np

        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        payload = json.dumps({"input": list(texts), "model": self.model_id}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
        rows = body.get("data")
        if not rows:
            raise RuntimeError(f"嵌入端点返回里没有 data：{str(body)[:200]}")
        rows = sorted(rows, key=lambda item: item.get("index", 0))
        matrix = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.dim:
            raise RuntimeError(
                f"维度不符：config 写 {self.dim}，端点给 {matrix.shape}。"
                "换模型请同步改 [embed] 并跑 index_docs.py --reembed"
            )
        return matrix


def probe(endpoint: str, timeout_s: float = 1.0) -> bool:
    try:
        request = urllib.request.Request(endpoint.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(request, timeout=timeout_s):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def get_embedder(config: dict, require: bool = False, bulk: bool = False):
    ""
    settings = config.get("embed", {})
    endpoint = settings.get("endpoint", "")
    model_id = settings.get("model", "unknown")
    dim = int(settings.get("dim", 0))
    timeout_s = float(
        settings.get("batch_timeout_s", 30.0) if bulk else settings.get("timeout_s", 2.0)
    )

    if not endpoint:
        return NullEmbedder(reason="config.toml 未配置 [embed].endpoint")
    if not probe(endpoint, timeout_s=min(timeout_s, 1.0)):
        return NullEmbedder(
            model_id=model_id, dim=dim, reason=f"embed-serve 不可达：{endpoint}"
        )
    return HttpEmbedder(
        endpoint=endpoint, model_id=model_id, dim=dim, timeout_s=timeout_s
    )
