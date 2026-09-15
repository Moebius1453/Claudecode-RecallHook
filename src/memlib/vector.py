""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def pack(vector) -> bytes:
    import numpy as np

    return np.asarray(vector, dtype="<f4").tobytes()


def unpack(blob: bytes, dim: int) -> "np.ndarray":
    import numpy as np

    return np.frombuffer(blob, dtype="<f4").astype(np.float32)[:dim]


def stack(blobs: list[bytes], dim: int) -> "np.ndarray":
    import numpy as np

    if not blobs:
        return np.zeros((0, dim), dtype=np.float32)
    return np.vstack([unpack(blob, dim) for blob in blobs])


def cosine_topk(query: "np.ndarray", matrix: "np.ndarray", k: int) -> list[tuple[int, float]]:
    ""
    import numpy as np

    if matrix.size == 0:
        return []
    query = np.asarray(query, dtype=np.float32).ravel()
    query_norm = float(np.linalg.norm(query))
    if query_norm == 0.0:
        return []
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    scores = (matrix @ query) / (norms * query_norm)
    order = np.lexsort((np.arange(len(scores)), -scores))
    return [(int(index), float(scores[index])) for index in order[:k]]
