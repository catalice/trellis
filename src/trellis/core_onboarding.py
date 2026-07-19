from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID


ONBOARDING_SYSTEM = """\
You are Trellis — setting up a new user's profile. Do not claim to know them \
already and do not reference or invent prior interactions.

Your job is to learn who they are through natural conversation. Ask one question \
at a time. No lists, no forms, no bullet points. Just talk. Warm, direct, efficient.

What to find out (in whatever order feels natural):
- Their name, or what they want to be called
- Life context that matters: cognitive profile, physical health, energy patterns, \
location, lifestyle — whatever shapes how they operate
- Their goals — anything they want Trellis to hold

As you learn things, save them with the tools. Don't wait until the end.

Use save_identity once you have their name and a sense of who they are. \
Use add_goal for each goal they mention.

If they start dumping tasks, ideas, or thoughts mid-onboarding, call brain_dump \
with their exact words — it captures everything and extracts the tasks. Never \
tell them Trellis can't hold tasks; it can. Then continue onboarding naturally.

If they want to skip onboarding, respect that: call save_identity with whatever \
you have (even just a name) and wrap up immediately.

When you've covered the essentials and saved the key things, wrap up naturally. \
One brief summary of what you've captured, then hand off. \
Don't ask if there's anything else — just close cleanly.
"""

_SAVE_IDENTITY_TOOL = {
    "name": "save_identity",
    "description": (
        "Save the user's name and life context. Call once you have their name "
        "and a clear enough picture of who they are. Can be called again later "
        "to update notes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The user's name or preferred name.",
            },
            "physical_notes": {
                "type": "string",
                "description": (
                    "Physical context: health conditions, injuries, anything "
                    "relevant to training and coaching."
                ),
            },
            "cognitive_notes": {
                "type": "string",
                "description": (
                    "Cognitive and executive function context: neurodivergence, "
                    "energy patterns, lifestyle factors, anything that shapes "
                    "how they work and think."
                ),
            },
        },
        "required": ["name"],
    },
}


class _ProfileService(Protocol):
    def update(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        physical_notes: str | None = None,
        cognitive_notes: str | None = None,
    ): ...

    def get(self, user_id: UUID): ...


def onboarding_tools(profile_service: _ProfileService) -> list[tuple[dict, callable]]:
    def handle_save_identity(user_id: UUID, input_dict: dict, now: datetime) -> str:
        name = input_dict.get("name", "").strip()
        if not name:
            return "Name is required."
        profile_service.update(
            user_id,
            name=name,
            physical_notes=input_dict.get("physical_notes") or None,
            cognitive_notes=input_dict.get("cognitive_notes") or None,
        )
        return f"Identity saved. Welcome, {name}."

    return [(_SAVE_IDENTITY_TOOL, handle_save_identity)]


def needs_onboarding(profile_service: _ProfileService, user_id: UUID) -> bool:
    profile = profile_service.get(user_id)
    return profile is None or not profile.name
