from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from anthropic import Anthropic
from telegram import Update

from trellis.core_assembler import Assembler
from trellis.core_config import Settings
from trellis.core_history import PostgresConversationHistory
from trellis.core_meta_tool import meta_tools
from trellis.core_onboarding import ONBOARDING_SYSTEM, needs_onboarding, onboarding_tools
from trellis.core_oracle import Oracle
from trellis.core_registry import TrellisRegistry
from trellis.core_summariser import make_summariser
from trellis.core_telegram import TelegramTrellis, make_transcriber
from trellis.infra_obsidian import ObsidianVault
from trellis.postgres import (
    PostgresCurrentContextRepository,
    PostgresDatabase,
    PostgresPreferencesRepository,
    PostgresUserProfileRepository,
)
from trellis.user_context import CurrentContextService, UserProfileService

# Second brain domain — the product. Training and learn are future modules;
# their files exist but are not registered until rebuilt on the lean architecture.
from trellis.domain_second_brain_claude import BrainDumpClaude
from trellis.domain_second_brain_repo import (
    PostgresCaptureRepository,
    PostgresEffortRepository,
    PostgresGoalRepository,
    PostgresReminderRepository,
    PostgresStateRepository,
    PostgresTaskRepository,
)
from trellis.domain_second_brain_service import (
    BrainDumpService,
    CaptureService,
    CleanupService,
    EffortService,
    GoalService,
    ReminderService,
    StateService,
    TaskService,
)
from trellis.domain_second_brain_tool import (
    ADD_GOAL_TOOL, handle_add_goal,
    BRAIN_DUMP_TOOL, handle_brain_dump,
    LOG_STATE_TOOL, handle_log_state,
    SECOND_BRAIN_SIGNALS,
    second_brain_context_loader,
    second_brain_snapshot,
    second_brain_tools,
)


# --- Permanent context loaders --------------------------------------------

def _profile_loader(svc: UserProfileService):
    def loader(user_id: UUID, now: datetime) -> str | None:
        profile = svc.get(user_id)
        if not profile or profile.is_empty():
            return None
        return f"[User profile]\n{profile.for_coach()}"
    return loader


def _current_context_loader(svc: CurrentContextService):
    def loader(user_id: UUID, now: datetime) -> str | None:
        ctx = svc.get_valid(user_id, now.date())
        if not ctx:
            return None
        text = ctx.for_coach()
        return f"[Current context]\n{text}" if text else None
    return loader


# --- Main -----------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = Settings.from_env()
    settings.validate()

    database = PostgresDatabase(settings.database_url)
    database.migrate(Path(__file__).with_name("migrations"))

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    brain_dump_claude = BrainDumpClaude(anthropic_client, settings.anthropic_model)

    summariser = None
    transcriber = None
    if settings.groq_api_key:
        from groq import Groq as GroqClient
        groq_client = GroqClient(api_key=settings.groq_api_key)
        summariser = make_summariser(groq_client)
        transcriber = make_transcriber(groq_client)

    history = PostgresConversationHistory(database, settings.timezone)
    preferences_repository = PostgresPreferencesRepository(database)

    # --- Permanent context services ---
    profile_service = UserProfileService(PostgresUserProfileRepository(database))
    context_service = CurrentContextService(PostgresCurrentContextRepository(database))

    # --- Second brain domain services ---
    capture_repo = PostgresCaptureRepository(database)
    effort_repo = PostgresEffortRepository(database)
    task_repo = PostgresTaskRepository(database)
    reminder_repo = PostgresReminderRepository(database)
    goal_repo = PostgresGoalRepository(database)
    state_repo = PostgresStateRepository(database)

    vault = ObsidianVault(
        settings.obsidian_vault, settings.timezone,
        task_repo, reminder_repo, effort_repo,
        state_repo=state_repo,
    )

    capture_service = CaptureService(capture_repo, projection=vault)
    effort_service = EffortService(effort_repo, projection=vault)
    task_service = TaskService(task_repo, settings.timezone, projection=vault)
    reminder_service = ReminderService(reminder_repo, settings.timezone, projection=vault)
    goal_service = GoalService(goal_repo)
    brain_dump_service = BrainDumpService(
        capture_repo, task_repo, brain_dump_claude, settings.timezone, projection=vault,
    )
    cleanup_service = CleanupService(capture_repo, effort_repo, brain_dump_claude)
    state_service = StateService(state_repo, settings.timezone, projection=vault)

    # --- Registry ---
    # Register ALL domains before constructing Assembler.
    # The router snapshots signals at Assembler init — domains added after are invisible.
    registry = TrellisRegistry()

    registry.add_domain(
        "second_brain",
        second_brain_context_loader(task_service, goal_service, effort_service),
        second_brain_tools(
            task_service=task_service,
            goal_service=goal_service,
            capture_service=capture_service,
            effort_service=effort_service,
            reminder_service=reminder_service,
            cleanup_service=cleanup_service,
            state_service=state_service,
            tz=settings.timezone,
        ),
        SECOND_BRAIN_SIGNALS,
    )

    oracle = Oracle(client=anthropic_client, model=settings.anthropic_model)

    assembler = Assembler(
        oracle=oracle,
        registry=registry,
        history=history,
        permanent=[
            ("profile", _profile_loader(profile_service)),
            ("current_context", _current_context_loader(context_service)),
            ("snapshot", second_brain_snapshot(task_service, reminder_service, state_service)),
        ],
        always_tools=[
            (
                BRAIN_DUMP_TOOL,
                lambda uid, inp, now: handle_brain_dump(
                    uid, inp, now, brain_dump_service=brain_dump_service
                ),
            ),
            (
                LOG_STATE_TOOL,
                lambda uid, inp, now: handle_log_state(
                    uid, inp, now, state_service=state_service, tz=settings.timezone
                ),
            ),
            *meta_tools(context_service, preferences_repository),
        ],
        summariser=summariser,
        default_domain="second_brain",
        onboarding_check=lambda uid: needs_onboarding(profile_service, uid),
        onboarding_system=ONBOARDING_SYSTEM,
        onboarding_tools=[
            *onboarding_tools(profile_service),
            (
                BRAIN_DUMP_TOOL,
                lambda uid, inp, now: handle_brain_dump(
                    uid, inp, now, brain_dump_service=brain_dump_service
                ),
            ),
            (
                ADD_GOAL_TOOL,
                lambda uid, inp, now: handle_add_goal(uid, inp, now, goal_service=goal_service),
            ),
        ],
    )

    application = TelegramTrellis(
        settings,
        database,
        assembler,
        reminder_service,
        transcriber=transcriber,
    ).build()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
