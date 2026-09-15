""

from dataclasses import dataclass
import json
import re
import urllib.request

_NO_THINK = "/no_think"


@dataclass
class LlamaClient:
    endpoint: str
    model: str = "local"
    timeout_s: float = 120.0

    def chat(self, prompt: str, *, max_tokens: int = 400, temperature: float = 0.0,
             no_think: bool = True) -> str:
        content = prompt + ("\n\n" + _NO_THINK if no_think else "")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"].get("content") or ""

    def chat_json(self, prompt: str, *, max_tokens: int = 400) -> dict | None:
        ""
        raw = self.chat(prompt, max_tokens=max_tokens)
        return extract_json(raw)


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def get_client(config: dict) -> LlamaClient:
    settings = config.get("llama", {})
    return LlamaClient(
        endpoint=settings.get("endpoint", "http://127.0.0.1:8080"),
        model=settings.get("model", "local"),
        timeout_s=float(settings.get("timeout_s", 120.0)),
    )


def is_up(client: LlamaClient) -> bool:
    try:
        with urllib.request.urlopen(client.endpoint.rstrip("/") + "/health", timeout=2):
            return True
    except Exception:
        return False
