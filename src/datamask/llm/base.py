"""Base classes and shared prompt for LLM-based detection.

Unlike the original script — which had to call an internal corporate wrapper URL
before reaching a model — providers here talk to standard endpoints directly:
the official OpenAI API, any OpenAI-compatible gateway, or a local server such
as Ollama / LM Studio. No proprietary middle-man.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

SYSTEM_PROMPT = (
    "You are a data privacy analyst. You identify whether a sample of column "
    "values is sensitive (personally identifiable, confidential, or regulated) "
    "and, if so, what kind of data it is."
)

# The model must answer in a strict, parseable format.
USER_PROMPT_TEMPLATE = """\
Below is a random sample of distinct values taken from a single database column.

Decide whether this column contains sensitive data. Sensitive data includes (but
is not limited to): full names, personal/organization addresses, phone numbers,
email addresses, government IDs (e.g. SSN), financial/account numbers, credentials,
health information, dates of birth, and free-text that reveals such details.

Respond with a single line of JSON and nothing else:
{{"sensitive": true|false, "rule": "<short_snake_case_label>", "confidence": 0.0-1.0}}

Use "rule" to name the data type using a short snake_case label such as
"full_name", "email", "phone", "address", "ssn", "credit_card", "date_of_birth".
If not sensitive, set "rule" to null.

Column name: {column_name}
Sample values:
{sample}
"""


@dataclass
class LLMResult:
    sensitive: bool
    rule: Optional[str]
    confidence: float
    token_usage: int = 0
    raw: str = ""


class LLMProvider(ABC):
    """Common interface so the pipeline is provider-agnostic."""

    @abstractmethod
    def classify(self, column_name: str, sample: Sequence[str]) -> LLMResult:
        ...

    @staticmethod
    def build_prompt(column_name: str, sample: Sequence[str]) -> str:
        body = "\n".join(f"- {v}" for v in sample)
        return USER_PROMPT_TEMPLATE.format(column_name=column_name, sample=body)

    @staticmethod
    def parse_response(text: str) -> LLMResult:
        """Parse the model's JSON line; degrade gracefully if it misbehaves."""
        import json
        import re

        text = (text or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return LLMResult(
                    sensitive=bool(data.get("sensitive", False)),
                    rule=data.get("rule"),
                    confidence=float(data.get("confidence", 0.5) or 0.0),
                    raw=text,
                )
            except (ValueError, TypeError):
                pass
        # Fallback: look for an affirmative word.
        sensitive = bool(re.search(r"\b(yes|true|sensitive)\b", text, re.IGNORECASE))
        return LLMResult(sensitive=sensitive, rule=None, confidence=0.3, raw=text)
