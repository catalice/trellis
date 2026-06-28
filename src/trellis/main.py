from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from anthropic import Anthropic
from telegram import Update

from trellis.assembler import Assembler
from trellis.summariser import make_summariser
from trellis.config import Settings
from trellis.garmin import GarminClient
from trellis.garmin_push import GarminDirectService
from trellis.garmin_setup import PostgresGarminConnectionRepository
from trellis.garmin_sync import GarminSyncService
from trellis.goals import GoalService
from trellis.health_postgres import PostgresHealthRepository
from trellis.health_status import HealthStatusService
from trellis.history import PostgresConversationHistory
from trellis.meta_tools import meta_tools
from trellis.oracle import Oracle
from trellis.postgres import (
    PostgresArcRepository,
    PostgresCaptureRepository,
    PostgresCycleRepository,
    PostgresCurrentContextRepository,
    PostgresDatabase,
    PostgresGoalRepository,
    PostgresInsightRepository,
    PostgresPreferencesRepository,
    PostgresReminderRepository,
    PostgresStrengthSessionRepository,
    PostgresTaskRepository,
    PostgresTrainingAnchorRepository,
    PostgresUserProfileRepository,
    PostgresSessionCompletionRepository,
    PostgresWorkoutCheckinRepository,
)
from trellis.readiness_service import ReadinessService
from trellis.registry import TrellisRegistry
from trellis.reminders import ReminderService
from trellis.session_completion import SessionCompletionService, WorkoutCheckinService
from trellis.telegram_bot import TelegramTrellis
from trellis.training import TrainingPlanner
from trellis.training_arc import TrainingArc
from trellis.training_context import training_context_loader
from trellis.training_history import TrainingHistoryService
from trellis.training_insights import DataSummariser, PatternEngine
from trellis.training_postgres import PostgresTrainingRepository
from trellis.training_service import TrainingService
from trellis.training_strength import StrengthSessionService
from trellis.training_tool import (
    TRAINING_SIGNALS, training_tools,
    ADD_GOAL_TOOL, SET_TRAINING_ANCHOR_TOOL,
    handle_add_goal, handle_set_training_anchor,
)
from trellis.cycle import CycleService
from trellis.intelligence_context import intelligence_context_loader
from trellis.ef_context import ef_context_loader
from trellis.ef_tool import EF_SIGNALS, ef_tools
from trellis.learn_context import learn_context_loader
from trellis.learn_service import LearningService
from trellis.learn_tool import (
    LEARN_SIGNALS, learn_tools,
    START_THREAD_TOOL, handle_start_learning_thread,
)
from trellis.postgres import PostgresLearningEntryRepository, PostgresLearningThreadRepository
from trellis.notes_context import notes_context_loader
from trellis.notes_tool import NOTES_SIGNALS, notes_tools
from trellis.obsidian import ObsidianRawCaptureProjection
from trellis.tasks import TaskService
from trellis.tracking_context import tracking_context_loader
from trellis.tracking_tool import tracking_tools
from trellis.onboarding_tool import ONBOARDING_SYSTEM, needs_onboarding, onboarding_tools
from trellis.user_context import AnchorService, CurrentContextService, UserProfileService


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


def _goals_loader(svc: GoalService):
    def loader(user_id: UUID, now: datetime) -> str | None:
        goals = svc.repository.list_active(user_id)
        if not goals:
            return None
        lines = "\n".join(f"- {g.summary()}" for g in goals)
        return f"[Goals]\n{lines}"
    return loader


def _stub(user_id: UUID, now: datetime) -> str | None:
    return None


class _NullProjection:
    def write(self, plan) -> None:
        pass


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

    # --- Infrastructure ---
    health_repository = PostgresHealthRepository(database)
    garmin_connection_repository = PostgresGarminConnectionRepository(
        database, settings.trellis_secret_key
    )
    garmin_sync = None
    if settings.health_worker_url and settings.health_worker_secret and settings.trellis_secret_key:
        garmin_sync = GarminSyncService(
            connection_repository=garmin_connection_repository,
            health_repository=health_repository,
            client=GarminClient(
                settings.health_worker_url,
                settings.health_worker_secret,
                timeout=120.0,
            ),
        )
    garmin_direct = GarminDirectService(garmin_connection_repository)

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)

    summariser = None
    if settings.groq_api_key:
        from groq import Groq as GroqClient
        summariser = make_summariser(GroqClient(api_key=settings.groq_api_key))

    history = PostgresConversationHistory(database, settings.timezone)
    reminder_service = ReminderService(PostgresReminderRepository(database))
    task_service = TaskService(PostgresTaskRepository(database), _NullProjection())
    cycle_service = CycleService(PostgresCycleRepository(database))
    capture_repository = PostgresCaptureRepository(database)
    capture_projection = ObsidianRawCaptureProjection(settings.obsidian_vault, settings.timezone)
    preferences_repository = PostgresPreferencesRepository(database)

    # --- Permanent context services ---
    profile_service = UserProfileService(PostgresUserProfileRepository(database))
    context_service = CurrentContextService(PostgresCurrentContextRepository(database))
    goal_service = GoalService(PostgresGoalRepository(database))

    # --- Learning ---
    learning_service = LearningService(
        PostgresLearningThreadRepository(database),
        PostgresLearningEntryRepository(database),
    )

    # --- Training services ---
    training_repository = PostgresTrainingRepository(database)
    readiness_service = ReadinessService(health_repository)
    anchor_service = AnchorService(PostgresTrainingAnchorRepository(database))
    training_history_service = TrainingHistoryService(health_repository)
    arc_repository = PostgresArcRepository(database)
    planner = TrainingPlanner()
    training_service = TrainingService(
        repository=training_repository,
        projection=_NullProjection(),
        planner=planner,
        timezone=settings.timezone,
        readiness=readiness_service,
        training_history=training_history_service,
    )
    health_status_service = HealthStatusService(health_repository, garmin_connection_repository)

    # --- Intelligence (pattern engine) ---
    strength_session_service = StrengthSessionService(
        PostgresStrengthSessionRepository(database)
    )
    workout_checkin_service = WorkoutCheckinService(
        PostgresWorkoutCheckinRepository(database)
    )
    completion_service = SessionCompletionService(
        repository=PostgresSessionCompletionRepository(database),
        activity_source=health_repository,
        plan_source=training_repository,
    )
    insight_repository = PostgresInsightRepository(database)
    pattern_engine = PatternEngine(
        repository=insight_repository,
        summariser=DataSummariser(
            health_repository=health_repository,
            strength_session_service=strength_session_service,
            workout_checkin_service=workout_checkin_service,
            lthr=settings.lthr,
        ),
        anthropic_client=anthropic_client,
        model=settings.anthropic_model,
    )

    # --- Oracle layer ---
    # IMPORTANT: register ALL domains before constructing Assembler.
    # The router snapshots signals at Assembler init — domains added after are invisible.
    registry = TrellisRegistry()

    registry.add_domain(
        "training",
        training_context_loader(
            health_repository=health_repository,
            readiness_provider=readiness_service,
            training_repository=training_repository,
            timezone=settings.timezone,
            workout_checkin_service=workout_checkin_service,
            strength_session_service=strength_session_service,
            arc_repository=arc_repository,
            anchor_service=anchor_service,
            training_history_service=training_history_service,
            cycle_service=cycle_service,
            preferences_repository=preferences_repository,
            garmin_sync_service=garmin_sync,
            completion_service=completion_service,
        ),
        training_tools(
            training_service=training_service,
            health_repository=health_repository,
            garmin_sync=garmin_sync,
            timezone=settings.timezone,
            health_status_service=health_status_service,
            goal_service=goal_service,
            completion_service=completion_service,
            workout_checkin_service=workout_checkin_service,
            strength_session_service=strength_session_service,
            pattern_engine=pattern_engine,
            anchor_service=anchor_service,
            garmin_direct=garmin_direct,
            arc_repository=arc_repository,
            planner=planner,
            training_repository=training_repository,
        ),
        TRAINING_SIGNALS,
    )

    registry.add_domain(
        "ef",
        ef_context_loader(task_service, reminder_service, preferences_repository),
        ef_tools(task_service, reminder_service, insight_repository=insight_repository),
        EF_SIGNALS,
    )
    registry.add_domain(
        "notes",
        notes_context_loader(capture_repository, preferences_repository),
        notes_tools(capture_repository),
        NOTES_SIGNALS,
    )
    registry.add_domain(
        "learn",
        learn_context_loader(learning_service, preferences_repository),
        learn_tools(learning_service),
        LEARN_SIGNALS,
    )

    oracle = Oracle(client=anthropic_client, model=settings.anthropic_model)

    assembler = Assembler(
        oracle=oracle,
        registry=registry,
        history=history,
        permanent=[
            ("profile", _profile_loader(profile_service)),
            ("current_context", _current_context_loader(context_service)),
            ("goals", _goals_loader(goal_service)),
        ],
        tracking_summary=("tracking", tracking_context_loader(health_repository, cycle_service)),
        intelligence=("intelligence", intelligence_context_loader(insight_repository)),
        always_tools=[*meta_tools(capture_repository, context_service, preferences_repository, capture_projection=capture_projection), *tracking_tools(health_repository, cycle_service)],
        summariser=summariser,
        onboarding_check=lambda uid: needs_onboarding(profile_service, uid),
        onboarding_system=ONBOARDING_SYSTEM,
        onboarding_tools=[
            *onboarding_tools(profile_service),
            (ADD_GOAL_TOOL,
             lambda uid, inp, now: handle_add_goal(uid, inp, now, goal_service=goal_service)),
            (SET_TRAINING_ANCHOR_TOOL,
             lambda uid, inp, now: handle_set_training_anchor(uid, inp, now, anchor_service=anchor_service)),
            (START_THREAD_TOOL,
             lambda uid, inp, now: handle_start_learning_thread(uid, inp, now, learning_service=learning_service)),
        ],
    )

    application = TelegramTrellis(
        settings,
        database,
        assembler,
        reminder_service,
        garmin_sync_service=garmin_sync,
        pattern_engine=pattern_engine,
    ).build()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
