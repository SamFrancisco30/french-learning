"""Thin OpenAI wrapper for structured (schema-validated) generation.

Exercise generation must return machine-usable objects, so every call goes through
`response_format={"type": "json_schema", strict: true}`. Strict mode guarantees the
shape, which removes the entire class of "the model wrote prose around the JSON" bugs.

Strict-mode constraints to remember when editing schemas:
  * every property must appear in `required`
  * every object needs `additionalProperties: false`
  * `minItems`/`maxItems` are not enforced — ask for counts in the prompt and
    validate in Python (see generator.py)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..config import settings

log = logging.getLogger(__name__)

MAX_RETRIES = 3


class LLMError(RuntimeError):
    pass


class StructuredLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        key = api_key or settings.openai_api_key
        if not key:
            raise LLMError("OPENAI_API_KEY is not set (see backend/.env.example)")
        self.model = model or settings.llm_model
        self._client = OpenAI(api_key=key, timeout=180.0)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "result",
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        },
                    },
                )
                choice = resp.choices[0]
                if choice.finish_reason == "length":
                    raise LLMError("response truncated — raise max_tokens")
                content = choice.message.content or ""
                if getattr(choice.message, "refusal", None):
                    raise LLMError(f"model refused: {choice.message.refusal}")
                return json.loads(content)
            except Exception as exc:  # noqa: BLE001 - retry transport + parse failures alike
                last = exc
                if attempt == MAX_RETRIES:
                    break
                backoff = 1.5 * (2 ** (attempt - 1))
                log.warning("LLM call failed (%s); retry %d in %.1fs", exc, attempt, backoff)
                time.sleep(backoff)
        raise LLMError(f"structured completion failed after {MAX_RETRIES} attempts: {last}") from last
