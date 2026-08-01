from __future__ import annotations

import logging
from datetime import datetime, timezone
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
from trellis.infra_embeddings import LocalEmbedder
from trellis.infra_memory import MemoryIndex
from trellis.infra_obsidian import ObsidianVault
from trellis.infra_search import TavilySearch
from trellis.infra_postgres import PostgresDatabase
from trellis.core_profile import (
    CurrentContextService,
    PostgresCurrentContextRepository,
    PostgresPreferencesRepository,
    PostgresUserProfileRepository,
    UserProfileService,
)

# Second brain domain — the product. Training and learn are future modules;
# their files exist but are not registered until rebuilt on the lean architecture.
from trellis.domain_focus_claude import BrainDumpClaude
from trellis.domain_focus_repo import (
    PostgresCaptureRepository,
    PostgresEffortRepository,
    PostgresGoalRepository,
    PostgresReminderRepository,
    PostgresTaskRepository,
)
from trellis.domain_focus_service import (
    BrainDumpService,
    CaptureService,
    CleanupService,
    EffortService,
    GoalService,
    ReminderService,
    TaskService,
)
from trellis.domain_focus_tool import (
    ADD_GOAL_TOOL, handle_add_goal,
    BRAIN_DUMP_TOOL, handle_brain_dump,
    RECALL_TOOL, handle_recall,
    FOCUS_SIGNALS,
    focus_context_loader,
    focus_snapshot,
    focus_tools,
)

# Sense domain — Mind / wellbeing tracking (mood, energy, meds, sleep, period) +
# the reading of Garmin health/readiness. The monitoring side.
from trellis.domain_sense_repo import PostgresStateRepository
from trellis.domain_sense_service import SenseService
from trellis.domain_sense_tool import (
    SENSE_SIGNALS,
    sense_context_loader,
    sense_snapshot,
    sense_tools,
)

# Move domain — lean running coach (Claude + tools; coaching happens in the turn).
from trellis.infra_garmin import (
    GarminActivityReader,
    GarminClient,
    GarminDirectService,
    GarminSyncService,
    PostgresGarminConnectionRepository,
)
from trellis.infra_tracking import PostgresHealthRepository
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_move_service import MoveService
from trellis.domain_move_tool import (
    MOVE_SIGNALS,
    move_context_loader,
    move_snapshot,
    move_tools,
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

    # Web search — read-only window on the outside world. None if no key configured.
    web_search = TavilySearch(settings.tavily_api_key) if settings.tavily_api_key else None

    # Embeddings — semantic memory, LOCAL (fastembed/bge-small). No key, no
    # network, no rate limits — always available. The model loads lazily on first
    # embed. Any failure degrades gracefully (embed returns None -> NULL embedding,
    # recall reports unavailable), nothing crashes.
    embedder = LocalEmbedder()

    # The one Trellis-wide meaning index — every domain files cards here and
    # recall searches across the lot. Uses the embedder above; when that's None,
    # remember() no-ops and recall reports itself unavailable. Nothing breaks.
    memory = MemoryIndex(database, embedder)

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

    capture_service = CaptureService(capture_repo, projection=vault, memory=memory)
    effort_service = EffortService(effort_repo, projection=vault, memory=memory)
    task_service = TaskService(task_repo, settings.timezone, projection=vault, memory=memory)
    reminder_service = ReminderService(reminder_repo, settings.timezone, projection=vault)
    goal_service = GoalService(goal_repo)
    brain_dump_service = BrainDumpService(
        capture_repo, task_repo, brain_dump_claude, settings.timezone,
        projection=vault, memory=memory,
    )
    cleanup_service = CleanupService(capture_repo, effort_repo, brain_dump_claude)

    # --- Move domain (reads goals from the second brain; stores its own plan) ---
    # Garmin push (workouts -> watch) + recent-run read + data sync. Gated: needs the
    # secret key (to decrypt the stored session) and, for reads/sync, a health-worker
    # URL. Absent -> the coach still plans; the Garmin tools just say "connect first".
    #
    # Recent health/readiness (sleep, HRV, body battery) — synced by the health worker;
    # the coach reads the latest to factor into planning. Always available as a reader;
    # returns None per-user when there's no synced health yet.
    health_reader = PostgresHealthRepository(database)

    # --- Sense domain (Mind / wellbeing tracking + reads Garmin health) ---
    sense_service = SenseService(
        state_repo, settings.timezone, projection=vault, health_reader=health_reader,
    )

    garmin_push = None
    garmin_read = None
    garmin_sync = None
    if settings.trellis_secret_key.strip():
        garmin_connections = PostgresGarminConnectionRepository(database, settings.trellis_secret_key)
        garmin_push = GarminDirectService(garmin_connections)
        if settings.health_worker_url.strip() and settings.health_worker_secret.strip():
            garmin_client = GarminClient(
                settings.health_worker_url, settings.health_worker_secret, timeout=120.0,
            )
            garmin_read = GarminActivityReader(garmin_connections, garmin_client)
            garmin_sync = GarminSyncService(
                connection_repository=garmin_connections,
                health_repository=health_reader,
                client=garmin_client,
            )

    move_service = MoveService(
        PostgresMoveRepository(database),
        goal_service,
        settings.timezone,
        garmin_push=garmin_push,
        garmin_read=garmin_read,
        garmin_sync=garmin_sync,
    )

    # --- Registry ---
    # Register ALL domains before constructing Assembler.
    # The router snapshots signals at Assembler init — domains added after are invisible.
    registry = TrellisRegistry()

    registry.add_domain(
        "focus",
        focus_context_loader(task_service, goal_service, effort_service),
        focus_tools(
            task_service=task_service,
            goal_service=goal_service,
            capture_service=capture_service,
            effort_service=effort_service,
            reminder_service=reminder_service,
            cleanup_service=cleanup_service,
            sense_service=sense_service,
            web_search=web_search,
            tz=settings.timezone,
        ),
        FOCUS_SIGNALS,
    )

    registry.add_domain(
        "sense",
        sense_context_loader(sense_service),
        sense_tools(sense_service, settings.timezone),
        SENSE_SIGNALS,
    )

    registry.add_domain(
        "move",
        move_context_loader(move_service, goal_service),
        move_tools(move_service),
        MOVE_SIGNALS,
    )

    oracle = Oracle(client=anthropic_client, model=settings.anthropic_model)

    # Always-available tools — offered every turn regardless of the routed domain.
    always_tools = [
        (
            BRAIN_DUMP_TOOL,
            lambda uid, inp, now: handle_brain_dump(
                uid, inp, now, brain_dump_service=brain_dump_service
            ),
        ),
        *meta_tools(context_service, preferences_repository),
    ]
    # Recall is Trellis-wide, so it's always available (like brain_dump) — but only
    # when embeddings are configured, so there's no dead tool otherwise.
    if embedder is not None:
        always_tools.append((
            RECALL_TOOL,
            lambda uid, inp, now: handle_recall(uid, inp, now, memory=memory),
        ))

    assembler = Assembler(
        oracle=oracle,
        registry=registry,
        history=history,
        permanent=[
            ("profile", _profile_loader(profile_service)),
            ("current_context", _current_context_loader(context_service)),
            ("snapshot", focus_snapshot(task_service, reminder_service)),
            ("sense_snapshot", sense_snapshot(sense_service)),
            ("move_snapshot", move_snapshot(move_service)),
        ],
        always_tools=always_tools,
        summariser=summariser,
        default_domain="focus",
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

    # Daily background Garmin refresh — keeps each connected user's health/readiness
    # and recent runs current without them asking. Best-effort per user (non-connected
    # users just raise "connect first", which we swallow). Same path as the on-demand
    # sync_garmin tool. Only wired when Garmin sync is configured.
    daily_garmin_sync = None
    if garmin_sync is not None:
        def daily_garmin_sync() -> None:
            now = datetime.now(timezone.utc)
            for uid, _telegram_id in database.list_users():
                try:
                    move_service.sync_garmin(uid, now=now, days=3)
                except Exception:
                    logging.getLogger("trellis.core_main").debug(
                        "daily garmin sync skipped for %s", uid, exc_info=True
                    )

    application = TelegramTrellis(
        settings,
        database,
        assembler,
        reminder_service,
        transcriber=transcriber,
        memory=memory,
        daily_garmin_sync=daily_garmin_sync,
    ).build()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
