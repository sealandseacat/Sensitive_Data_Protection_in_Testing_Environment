"""Local / self-hosted LLM provider.

Targets servers that expose either:
  * an Ollama-style ``/api/generate`` endpoint, or
  * an OpenAI-compatible ``/v1/chat/completions`` endpoint (LM Studio, vLLM...).

Nothing leaves your machine/network, which is ideal for masking work on
confidential databases.
"""
from __future__ import annotations

from typing import Sequence

from datamask.llm.base import SYSTEM_PROMPT, LLMProvider, LLMResult


class LocalProvider(LLMProvider):
    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        timeout: int = 120,
        api_style: str = "ollama",  # "ollama" | "openai"
    ):
        try:
            import requests  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "The 'requests' package is required for LocalProvider. "
                "Install it with: pip install 'datamask[local]'"
            ) from exc

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.api_style = api_style

    def classify(self, column_name: str, sample: Sequence[str]) -> LLMResult:
        import requests

        prompt = self.build_prompt(column_name, sample)
        if self.api_style == "openai":
            url = f"{self.base_url}/v1/chat/completions"
            payload = {
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = (data.get("usage") or {}).get("total_tokens", 0)
        else:  # ollama
            url = f"{self.base_url}/api/generate"
            payload = {
                "model": self.model,
                "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                "stream": False,
                "options": {"temperature": self.temperature},
            }
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("response", "")
            usage = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

        result = self.parse_response(content)
        result.token_usage = usage or 0
        return result
