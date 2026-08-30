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
from trellis.core_watcher import (
    PATTERN_RESPONSE_TOOL,
    PostgresWatcherRepository,
    Watcher,
    WatcherDiscovery,
    handle_pattern_response,
)
from trellis.infra_embeddings import LocalEmbedder
from trellis.infra_memory import MemoryIndex
from trellis.infra_obsidian import ObsidianVault
from trellis.infra_search import SearchGateway
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
    EffortService,
    GoalService,
    ReminderService,
    TaskService,
)
from trellis.domain_focus_tool import (
    ADD_GOAL_TOOL, handle_add_goal,
    BRAIN_DUMP_TOOL, handle_brain_dump,
    RECALL_TOOL, handle_recall,
    FOCUS_ROOMS,
    FOCUS_SIGNALS,
    focus_context_loader,
    focus_snapshot,
    focus_tools,
)

# Sense domain — Mind / wellbeing tracking (mood, energy, meds, sleep, period) +
# the reading of Garmin health/readiness. The monitoring side.
from trellis.domain_learn_repo import PostgresLearnRepository
from trellis.domain_learn_service import LearnService
from trellis.domain_learn_tool import (
    LEARN_ROOMS,
    LEARN_SIGNALS,
    learn_context_loader,
    learn_tools,
)
from trellis.domain_sense_repo import PostgresStateRepository
from trellis.domain_sense_service import SenseService
from trellis.domain_sense_tool import (
    SENSE_ROOMS,
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
    MOVE_ROOMS,
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

def build_vault(database: PostgresDatabase, settings: Settings) -> ObsidianVault:
    """The ONE way to construct the vault, fully wired. Hand-built vaults with
    None repos shipped pages with silent holes twice — every consumer (main and
    scripts alike) goes through here."""
    return ObsidianVault(
        settings.obsidian_vault, settings.timezone,
        PostgresTaskRepository(database),
        PostgresReminderRepository(database),
        PostgresEffortRepository(database),
        state_repo=PostgresStateRepository(database),
        move_repo=PostgresMoveRepository(database, settings.timezone),
        health_repo=PostgresHealthRepository(database),
    )


def build_watcher(
    database: PostgresDatabase,
    settings: Settings,
    anthropic_client: Anthropic,
    *,
    vault: ObsidianVault | None = None,
    memory: MemoryIndex | None = None,
    history: PostgresConversationHistory | None = None,
) -> Watcher:
    """The ONE way to construct the Watcher, fully wired. A missing dep here
    doesn't error — it reads as a failed verification (10 Aug), so partial
    hand-wiring is banned. Pass shared instances to reuse them; omitted deps
    are built fresh (repos are stateless wrappers, duplicates are harmless)."""
    return Watcher(
        PostgresWatcherRepository(database),
        WatcherDiscovery(anthropic_client, settings.anthropic_model),
        state_repo=PostgresStateRepository(database),
        health_repo=PostgresHealthRepository(database),
        run_repo=PostgresMoveRepository(database, settings.timezone),
        tz=settings.timezone,
        vault=vault if vault is not None else build_vault(database, settings),
        task_repo=PostgresTaskRepository(database),
        capture_repo=PostgresCaptureRepository(database),
        effort_repo=PostgresEffortRepository(database),
        memory=memory if memory is not None else MemoryIndex(database, LocalEmbedder()),
        history=history if history is not None
        else PostgresConversationHistory(database, settings.timezone),
    )


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
    # Always constructed: pubmed/scholar/trials need no key, so even a bare
    # install can cite real papers on day one.
    web_search = SearchGateway(settings.tavily_api_key, settings.guardian_api_key)

    # Embeddings — semantic memory, LOCAL (fastembed/bge-small). No key, no
    # network, no rate limits — always constructed. The model loads lazily on
    # first embed; any runtime failure degrades gracefully (embed returns None ->
    # NULL embedding, recall reports unavailable), nothing crashes.
    embedder = LocalEmbedder()

    # The one Trellis-wide meaning index — every domain files cards here and
    # recall searches across the lot.
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

    move_repo = PostgresMoveRepository(database, settings.timezone)
    health_reader = PostgresHealthRepository(database)

    vault = build_vault(database, settings)

    capture_service = CaptureService(capture_repo, projection=vault, memory=memory)
    effort_service = EffortService(effort_repo, projection=vault, memory=memory)
    task_service = TaskService(task_repo, settings.timezone, projection=vault, memory=memory)
    reminder_service = ReminderService(reminder_repo, settings.timezone, projection=vault)
    goal_service = GoalService(goal_repo)
    learn_service = LearnService(
        PostgresLearnRepository(database), settings.timezone, projection=vault,
    )

    def _dump_hints(uid) -> str | None:
        bits = []
        try:
            efforts = [e.title for e in effort_service.list_all(uid)]
            if efforts:
                bits.append("efforts: " + ", ".join(efforts[:10]))
        except Exception:
            pass
        try:
            threads = [th.title for th in learn_service.list_threads(uid)]
            if threads:
                bits.append("learning threads: " + ", ".join(threads[:10]))
        except Exception:
            pass
        return "; ".join(bits) or None

    brain_dump_service = BrainDumpService(
        capture_repo, task_repo, brain_dump_claude, settings.timezone,
        projection=vault, memory=memory, hints_provider=_dump_hints,
    )

    # --- Move domain (reads goals from the second brain; stores its own plan) ---
    # Garmin push (workouts -> watch) + recent-run read + data sync. Gated: needs the
    # secret key (to decrypt the stored session) and, for reads/sync, a health-worker
    # URL. Absent -> the coach still plans; the Garmin tools just say "connect first".
    #
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
        move_repo,
        goal_service,
        settings.timezone,
        garmin_push=garmin_push,
        garmin_read=garmin_read,
        garmin_sync=garmin_sync,
        health_repo=health_reader,
        projection=vault,
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
            sense_service=sense_service,
            web_search=web_search,
            tz=settings.timezone,
        ),
        FOCUS_SIGNALS,
        rooms=FOCUS_ROOMS,
    )

    registry.add_domain(
        "sense",
        sense_context_loader(sense_service),
        sense_tools(sense_service, settings.timezone),
        SENSE_SIGNALS,
        rooms=SENSE_ROOMS,
    )

    registry.add_domain(
        "learn",
        learn_context_loader(learn_service),
        learn_tools(learn_service),
        LEARN_SIGNALS,
        rooms=LEARN_ROOMS,
    )

    registry.add_domain(
        "move",
        move_context_loader(move_service, goal_service),
        move_tools(move_service),
        MOVE_SIGNALS,
        rooms=MOVE_ROOMS,
    )

    oracle = Oracle(client=anthropic_client, model=settings.anthropic_model)

    # --- The Watcher (the big brain's slow mind) ---
    # Discovery is the ONLY source of hypotheses — nothing is planted here.
    watcher = build_watcher(
        database, settings, anthropic_client,
        vault=vault, memory=memory, history=history,
    )

    # Always-available tools — offered every turn regardless of the routed domain.
    always_tools = [
        (
            BRAIN_DUMP_TOOL,
            lambda uid, inp, now: handle_brain_dump(
                uid, inp, now, brain_dump_service=brain_dump_service,
                tz=settings.timezone,
            ),
        ),
        *meta_tools(context_service, preferences_repository),
        (
            PATTERN_RESPONSE_TOOL,
            lambda uid, inp, now: handle_pattern_response(uid, inp, now, watcher=watcher),
        ),
    ]
    # Recall is Trellis-wide, so it's always available (like brain_dump).
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
        intelligence=("watcher", watcher.intelligence_context),
        summariser=summariser,
        default_domain="focus",
        embedder=embedder,
        preferences=preferences_repository,
        onboarding_check=lambda uid: needs_onboarding(profile_service, uid),
        onboarding_system=ONBOARDING_SYSTEM,
        onboarding_tools=[
            *onboarding_tools(profile_service),
            (
                BRAIN_DUMP_TOOL,
                lambda uid, inp, now: handle_brain_dump(
                    uid, inp, now, brain_dump_service=brain_dump_service,
                    tz=settings.timezone,
                ),
            ),
            (
                ADD_GOAL_TOOL,
                lambda uid, inp, now: handle_add_goal(uid, inp, now, goal_service=goal_service),
            ),
        ],
    )

    # Background Garmin refresh (every 6h — see core_telegram) — keeps each
    # connected user's health/readiness and recent runs current without them
    # asking. Same path as the on-demand sync_garmin tool. Only wired when
    # Garmin sync is configured. Not-connected is expected (debug); anything
    # else is a real failure and must be VISIBLE (warning).
    background_garmin_sync = None
    if garmin_sync is not None:
        def background_garmin_sync() -> None:
            now = datetime.now(timezone.utc)
            for uid, _telegram_id in database.list_users():
                try:
                    move_service.sync_garmin(uid, now=now, days=3)
                except RuntimeError:
                    logging.getLogger("trellis.core_main").debug(
                        "garmin sync skipped for %s (not connected)", uid
                    )
                except Exception:
                    logging.getLogger("trellis.core_main").warning(
                        "garmin sync failed for %s", uid, exc_info=True
                    )

    application = TelegramTrellis(
        settings,
        database,
        assembler,
        reminder_service,
        transcriber=transcriber,
        memory=memory,
        garmin_sync=background_garmin_sync,
        watcher_tick=lambda: [
            watcher.tick(uid, datetime.now(timezone.utc))
            for uid, _tg in database.list_users()
        ],
    ).build()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
