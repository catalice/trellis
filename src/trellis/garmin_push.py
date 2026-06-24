"""
Direct Garmin Connect client for write operations (push workout, etc).
Uses python-garminconnect / garth session loaded from the stored session_dump.
Read operations still go through the health worker.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID


class _ConnectionRepo(Protocol):
    def get_session_dump(self, user_id: UUID) -> str | None: ...


class GarminDirectService:
    def __init__(self, connection_repository: _ConnectionRepo):
        self.connection_repository = connection_repository

    def _connect(self, user_id: UUID):
        dump = self.connection_repository.get_session_dump(user_id)
        if not dump:
            raise RuntimeError("Garmin not connected. Use /garmin_setup to connect.")
        try:
            import garth
            import garminconnect
            client = garminconnect.Garmin()
            client.garth.loads(dump)
            return client
        except Exception as exc:
            raise RuntimeError(f"Failed to load Garmin session: {exc}") from exc

    def push_workout(self, user_id: UUID, workout_json: str) -> str:
        try:
            client = self._connect(user_id)
            result = client.add_workout(workout_json)
            workout_id = str(result.get("workoutId", result))
            return workout_id
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to push workout to Garmin: {exc}") from exc

    def schedule_workout(self, user_id: UUID, workout_id: str, on_date: date) -> None:
        try:
            client = self._connect(user_id)
            client.schedule_workout(workout_id, on_date.isoformat())
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to schedule workout {workout_id}: {exc}") from exc

    def list_workouts(self, user_id: UUID, *, limit: int = 20) -> list[dict]:
        try:
            client = self._connect(user_id)
            result = client.get_workouts(0, limit)
            return result if isinstance(result, list) else []
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to list Garmin workouts: {exc}") from exc

    def delete_workout(self, user_id: UUID, workout_id: str) -> None:
        try:
            client = self._connect(user_id)
            client.delete_workout(workout_id)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to delete workout {workout_id}: {exc}") from exc
