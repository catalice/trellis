"""
TEMPLATE: domain_claude.py
Copy to: src/trellis/{domain}_claude.py

Rules:
- All prompts are module-level constants — never inline strings inside methods
- Every method returns typed data or None — never raises, never returns raw strings
- Parse failures log a warning and return None — callers handle gracefully
- max_tokens must be generous for calls returning structured JSON (8192+)
- This module only talks to Claude — no DB, no business logic
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from anthropic import Anthropic

_log = logging.getLogger(__name__)


# --- Prompts (module-level constants) -----------------------------------

_EXAMPLE_PROMPT = """\
You are Trellis. Given the following context, do something useful.

CONTEXT:
{context}

Respond with ONLY a JSON object:
{{
  "result": "...",
  "confidence": "high" | "medium" | "low"
}}
"""


# --- Return types -------------------------------------------------------

@dataclass(frozen=True)
class ExampleResult:
    result: str
    confidence: str


# --- Claude module ------------------------------------------------------

class ExampleClaude:
    def __init__(self, client: Anthropic, model: str) -> None:
        self.client = client
        self.model = model

    def example_call(self, context: str) -> ExampleResult | None:
        prompt = _EXAMPLE_PROMPT.replace("{context}", context)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            parsed = json.loads(raw)
            return ExampleResult(
                result=str(parsed["result"]),
                confidence=str(parsed.get("confidence", "medium")),
            )
        except Exception:
            _log.warning("ExampleClaude.example_call failed", exc_info=True)
            return None
