"""Model access through the Flower runtime (OpenAI Responses-compatible).

Models only ever *propose* (a stance, an action, an indicator phrase). Every
result is validated by code, and every decision records ``decided_by`` so the
audit shows whether a model or a deterministic fallback produced it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DEFAULT_COORDINATOR_MODEL = "flower-endeavor-v1.0"
DEFAULT_PARTICIPANT_MODEL = "openai/gpt-6-luna"


class ModelClient:
    def __init__(self, model: str, timeout: float = 90.0) -> None:
        self.model = model
        self.timeout = timeout
        self._client = None
        self.last_error: Optional[str] = None
        base = os.environ.get("FLWR_RUNTIME_BASE_URL")
        api_key = os.environ.get("FLWR_RUNTIME_API_KEY")
        if base and api_key and model and model != "none":
            from openai import OpenAI

            self._client = OpenAI(base_url=base, api_key=api_key, max_retries=0, timeout=timeout)

    @property
    def available(self) -> bool:
        return self._client is not None

    def json(self, instructions: str, prompt: str) -> Optional[dict[str, Any]]:
        """Ask for a JSON object. Returns None on any failure (caller falls back)."""
        if self._client is None:
            return None
        try:
            extra = {"reasoning": {"effort": "low"}} if "endeavor" in self.model else {}
            resp = self._client.responses.create(
                model=self.model, **extra,
                instructions=instructions + "\nRespond with a single JSON object and nothing else.",
                input=[{"type": "message", "role": "user", "content": prompt}],
            )
            return _parse_json(resp.output_text)
        except Exception as err:  # noqa: BLE001 - any provider error means fallback
            self.last_error = f"{type(err).__name__}: {str(err)[:160]}"
            return None


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(text[start:end + 1])
        return out if isinstance(out, dict) else None
    except ValueError:
        return None
