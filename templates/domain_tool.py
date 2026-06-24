"""
TEMPLATE: domain_tool.py
Copy to: src/trellis/{domain}_tool.py

Rules:
- One tool schema dict and one handler function per action
- Handler signature always: (user_id, input_dict, now) -> str
- Schema and handler live together — easy to find, easy to register
- Tools are the API surface — UI will call these same handlers eventually
- No business logic here — delegate to the service
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from trellis.domain_service import ExampleService


# --- Tool definition ----------------------------------------------------

EXAMPLE_TOOL = {
    "name": "example_action",
    "description": "Does something useful for the user.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string", "description": "The value to act on."},
        },
        "required": ["value"],
    },
}


# --- Handler ------------------------------------------------------------

def handle_example(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    service: ExampleService,
) -> str:
    value = str(input_dict.get("value", "")).strip()
    if not value:
        return "No value provided."
    try:
        return service.save_record(user_id, value)
    except Exception:
        return "Something went wrong — try again in a moment."


# --- Registration helper ------------------------------------------------
# In main.py: tools.extend(example_tools(service))

def example_tools(service: ExampleService) -> list[tuple[dict, callable]]:
    return [
        (EXAMPLE_TOOL, lambda uid, inp, now: handle_example(uid, inp, now, service=service)),
    ]
