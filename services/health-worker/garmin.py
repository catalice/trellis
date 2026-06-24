"""Garmin Connect integration for the Trellis health worker."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_AUTH_WORKER_URL = os.getenv("AUTH_WORKER_URL", "").rstrip("/")
_AUTH_WORKER_SECRET = os.getenv("AUTH_WORKER_SECRET", "")

_pending: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

MFA_TIMEOUT_SECS = 310
LOGIN_TIMEOUT_SECS = 60


def authenticate(email: str, password: str) -> dict[str, Any]:
    """Start Garmin authentication."""
    _cleanup_expired()

    if _use_auth_worker():
        return _authenticate_via_auth_worker(email, password)

    session_id = str(uuid.uuid4())
    mfa_q: queue.Queue[str] = queue.Queue()
    result_q: queue.Queue[dict[str, Any]] = queue.Queue()
    mfa_event = threading.Event()

    with _lock:
        _pending[session_id] = {
            "mfa_queue": mfa_q,
            "result_queue": result_q,
            "created_at": datetime.utcnow(),
        }

    thread = threading.Thread(
        target=_login_thread,
        args=(email, password, session_id, mfa_q, result_q, mfa_event),
        daemon=True,
    )
    thread.start()

    deadline = datetime.utcnow() + timedelta(seconds=LOGIN_TIMEOUT_SECS)
    while datetime.utcnow() < deadline:
        if mfa_event.wait(timeout=0.3):
            return {"status": "mfa_required", "session_id": session_id}
        try:
            result = result_q.get_nowait()
            if result["status"] == "success":
                return result
            raise RuntimeError(result["error"])
        except queue.Empty:
            pass

    with _lock:
        _pending.pop(session_id, None)
    raise RuntimeError("Login timeout: Garmin did not respond in 60s")


def complete_mfa(session_id: str, mfa_code: str) -> dict[str, Any]:
    """Submit an MFA code for a pending Garmin authentication."""
    with _lock:
        session = _pending.get(session_id)

    if not session:
        raise RuntimeError("MFA session not found or expired. Please connect again.")

    if "auth_worker_state" in session:
        logger.info("Completing Garmin MFA through auth worker")
        result = _auth_worker_request(
            "/login-complete",
            {"state": session["auth_worker_state"], "mfa_code": mfa_code},
        )
        with _lock:
            _pending.pop(session_id, None)
        return {
            "status": "success",
            "session_dump": _tokens_to_garth_dump(result["tokens"]),
        }

    session["mfa_queue"].put(mfa_code)

    deadline = datetime.utcnow() + timedelta(seconds=LOGIN_TIMEOUT_SECS)
    while datetime.utcnow() < deadline:
        try:
            result = session["result_queue"].get(timeout=0.3)
            if result["status"] == "success":
                return result
            raise RuntimeError(result["error"])
        except queue.Empty:
            pass

    raise RuntimeError("MFA timeout: authentication did not complete in 60s")


def fetch_metrics(session_dump: str, start_date: date, end_date: date) -> list[dict]:
    """Fetch daily health metrics from Garmin for a date range."""
    garmin = _garmin_from_session(session_dump)
    results: list[dict[str, Any]] = []

    current = start_date
    total = (end_date - start_date).days + 1
    day_num = 0

    while current <= end_date:
        day_num += 1
        date_str = current.isoformat()
        logger.info("[%d/%d] Fetching Garmin health metrics for %s", day_num, total, date_str)
        results.append(_fetch_daily_health_from_client(garmin, date_str, include_date=True))
        current += timedelta(days=1)

    return results


def fetch_daily_health(session_dump: str, metric_date: str) -> dict:
    """Fetch daily health metrics for a specific ISO date."""
    garmin = _garmin_from_session(session_dump)
    return _fetch_daily_health_from_client(garmin, metric_date, include_date=False)


def fetch_recent_activities(
    session_dump: str,
    limit: int = 10,
    metric_date: str | None = None,
) -> list[dict]:
    """Fetch recent Garmin activities or activities for a specific ISO date."""
    garmin = _garmin_from_session(session_dump)
    if metric_date:
        logger.info("Fetching Garmin activities for %s", metric_date)
        activities = garmin.get_activities_by_date(metric_date, metric_date)
    else:
        logger.info("Fetching %d recent Garmin activities", limit)
        activities = garmin.get_activities(0, limit)

    if not isinstance(activities, list):
        logger.warning("Garmin activities response was %s, expected list", type(activities).__name__)
        return []

    records: list[dict[str, Any]] = []
    for activity in activities[:limit]:
        if not isinstance(activity, dict):
            continue
        activity_id = activity.get("activityId")
        if not activity_id:
            continue
        try:
            records.append(_normalize_activity(activity))
        except Exception as error:
            logger.warning("Skipping malformed Garmin activity %s: %s", activity_id, error)
    return records


def fetch_activity_detail(session_dump: str, activity_id: str) -> dict[str, Any]:
    """Fetch Garmin activity detail, split and set payloads for one activity."""
    garmin = _garmin_from_session(session_dump)
    result: dict[str, Any] = {"activityId": activity_id}

    for key, method_name in (
        ("activity", "get_activity"),
        ("details", "get_activity_details"),
        ("splits", "get_activity_splits"),
        ("splitSummaries", "get_activity_split_summaries"),
        ("typedSplits", "get_activity_typed_splits"),
        ("exerciseSets", "get_activity_exercise_sets"),
    ):
        try:
            method = getattr(garmin, method_name)
            result[key] = method(activity_id)
        except Exception as error:
            logger.warning("Garmin %s failed for activity %s: %s", method_name, activity_id, error)
            result[f"{key}Error"] = str(error)[:300]

    return result


def _fetch_daily_health_from_client(
    garmin: Any,
    metric_date: str,
    *,
    include_date: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {"date": metric_date} if include_date else {}

    try:
        stats = garmin.get_stats(metric_date)
        if stats:
            row["steps"] = stats.get("totalSteps")
            row["calories"] = stats.get("totalKilocalories")
            row["distance_meters"] = stats.get("totalDistanceMeters")
            row["active_minutes"] = (
                (stats.get("moderateIntensityMinutes") or 0)
                + (stats.get("vigorousIntensityMinutes") or 0)
            )
            row["floors_climbed"] = stats.get("floorsAscended")
    except Exception as error:
        logger.warning("Garmin activity stats failed for %s: %s", metric_date, error)

    try:
        hr = garmin.get_heart_rates(metric_date)
        if hr:
            row["resting_hr"] = hr.get("restingHeartRate")
            row["avg_hr"] = hr.get("averageHeartRate")
            row["max_hr"] = hr.get("maxHeartRate")
    except Exception as error:
        logger.warning("Garmin heart rate failed for %s: %s", metric_date, error)

    try:
        sleep = garmin.get_sleep_data(metric_date)
        if sleep and sleep.get("dailySleepDTO"):
            sleep_dto = sleep["dailySleepDTO"]
            row["sleep_duration_minutes"] = _seconds_to_minutes(sleep_dto.get("sleepTimeSeconds"))
            row["sleep_deep_minutes"] = _seconds_to_minutes(sleep_dto.get("deepSleepSeconds"))
            row["sleep_light_minutes"] = _seconds_to_minutes(sleep_dto.get("lightSleepSeconds"))
            row["sleep_rem_minutes"] = _seconds_to_minutes(sleep_dto.get("remSleepSeconds"))
            row["sleep_awake_minutes"] = _seconds_to_minutes(sleep_dto.get("awakeSleepSeconds"))
            scores = sleep_dto.get("sleepScores") or {}
            row["sleep_score"] = (scores.get("overall") or {}).get("value")
    except Exception as error:
        logger.warning("Garmin sleep failed for %s: %s", metric_date, error)

    try:
        body_battery = garmin.get_body_battery(metric_date, metric_date)
        if body_battery and isinstance(body_battery[0], dict):
            day_data = body_battery[0]
            levels = [
                value[1]
                for value in (day_data.get("bodyBatteryValuesArray") or [])
                if value and len(value) > 1 and value[1] is not None
            ]
            row["body_battery_max"] = max(levels) if levels else None
            row["body_battery_min"] = min(levels) if levels else None
            row["body_battery_end"] = levels[-1] if levels else None
            row["body_battery_charged"] = day_data.get("charged")
            row["body_battery_drained"] = day_data.get("drained")
    except Exception as error:
        logger.warning("Garmin body battery failed for %s: %s", metric_date, error)

    try:
        stress = garmin.get_stress_data(metric_date)
        if stress:
            row["stress_avg"] = stress.get("avgStressLevel")
            row["stress_max"] = stress.get("maxStressLevel")
            row["stress_rest_duration_minutes"] = _seconds_to_minutes(
                stress.get("restStressDuration")
            )
    except Exception as error:
        logger.warning("Garmin stress failed for %s: %s", metric_date, error)

    try:
        hrv = garmin.get_hrv_data(metric_date)
        if hrv:
            summary = hrv.get("hrvSummary") or {}
            row["hrv_summary_keys"] = sorted(summary.keys())
            row["hrv_weekly_avg"] = summary.get("weeklyAvg")
            row["hrv_last_night"] = _first_present(
                summary,
                "lastNight",
                "lastNightAvg",
                "lastNightAverage",
            )
            row["hrv_status"] = summary.get("hrvStatusText")
    except Exception as error:
        logger.warning("Garmin HRV failed for %s: %s", metric_date, error)

    return row


def _normalize_activity(activity: dict[str, Any]) -> dict[str, Any]:
    activity_type = activity.get("activityType") or {}
    if not isinstance(activity_type, dict):
        activity_type = {}

    return {
        "activityId": activity.get("activityId"),
        "activityName": activity.get("activityName", "Unknown"),
        "activityType": (
            activity_type.get("typeKey")
            or activity_type.get("displayValue")
            or "Unknown"
        ),
        "startTimeInSeconds": _start_time_seconds(activity.get("startTimeLocal")),
        "duration": activity.get("duration"),
        "calories": activity.get("calories"),
        "avgHeartRate": activity.get("averageHeartRate"),
        "maxHeartRate": activity.get("maxHeartRate"),
        "distance": activity.get("distance"),
        "elevationGain": activity.get("elevationGain"),
        "elevationLoss": activity.get("elevationLoss"),
        "activeSets": activity.get("activeSets"),
        "totalExerciseReps": activity.get("totalExerciseReps"),
        "summarizedExerciseSets": activity.get("summarizedExerciseSets"),
    }


def _login_thread(
    email: str,
    password: str,
    session_id: str,
    mfa_queue: "queue.Queue[str]",
    result_queue: "queue.Queue[dict[str, Any]]",
    mfa_needed_event: threading.Event,
) -> None:
    from garminconnect import Garmin

    def prompt_mfa() -> str:
        logger.info("Garmin MFA required for pending session")
        mfa_needed_event.set()
        try:
            return mfa_queue.get(timeout=MFA_TIMEOUT_SECS)
        except queue.Empty as error:
            raise RuntimeError("MFA timeout: code not provided in time") from error

    try:
        garmin = Garmin(email=email, password=password)
        _apply_browser_headers(garmin)
        garmin.garth.login(email, password, prompt_mfa=prompt_mfa)
        result_queue.put({"status": "success", "session_dump": garmin.garth.dumps()})
    except Exception as error:
        detail = _redact(str(error), email, password)
        logger.warning("Garmin login failed: %s", detail)
        result_queue.put({"status": "error", "error": detail})
    finally:
        with _lock:
            _pending.pop(session_id, None)


def _garmin_from_session(session_dump: str) -> Any:
    from garminconnect import Garmin

    garmin = Garmin()
    garmin.garth.loads(session_dump)
    _apply_browser_headers(garmin)
    profile = garmin.garth.profile
    garmin.display_name = profile.get("displayName")
    garmin.full_name = profile.get("fullName")
    return garmin


def _apply_browser_headers(garmin: Any) -> None:
    garmin.garth.sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "origin": "https://sso.garmin.com",
            "referer": "https://sso.garmin.com/",
        }
    )


def _use_auth_worker() -> bool:
    return bool(_AUTH_WORKER_URL and _AUTH_WORKER_SECRET)


def _authenticate_via_auth_worker(email: str, password: str) -> dict[str, Any]:
    logger.info("Starting Garmin auth through external auth worker")
    result = _auth_worker_request("/login-start", {"email": email, "password": password})

    if result.get("mfa_required"):
        session_id = str(uuid.uuid4())
        with _lock:
            _pending[session_id] = {
                "auth_worker_state": result["state"],
                "created_at": datetime.utcnow(),
            }
        return {"status": "mfa_required", "session_id": session_id}

    return {
        "status": "success",
        "session_dump": _tokens_to_garth_dump(result["tokens"]),
    }


def _auth_worker_request(path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{_AUTH_WORKER_URL}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Worker-Secret": _AUTH_WORKER_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Auth worker HTTP {error.code}: {_redact(detail, *body.values())}"
        ) from error
    if not isinstance(parsed, dict):
        raise RuntimeError("Auth worker returned non-object JSON")
    return parsed


def _tokens_to_garth_dump(tokens: dict[str, Any]) -> str:
    from garminconnect import Garmin
    from garth.auth_tokens import OAuth1Token, OAuth2Token

    oauth1 = tokens["oauth1"]
    oauth2 = tokens["oauth2"]

    client = Garmin()
    client.garth.configure(
        oauth1_token=OAuth1Token(
            oauth_token=oauth1["oauth_token"],
            oauth_token_secret=oauth1["oauth_token_secret"],
            mfa_token=oauth1.get("mfa_token"),
            domain="garmin.com",
        ),
        oauth2_token=OAuth2Token(
            scope=oauth2["scope"],
            jti=oauth2["jti"],
            token_type=oauth2["token_type"],
            access_token=oauth2["access_token"],
            refresh_token=oauth2["refresh_token"],
            expires_in=int(oauth2["expires_in"]),
            expires_at=int(oauth2["expires_at"]),
            refresh_token_expires_in=int(oauth2["refresh_token_expires_in"]),
            refresh_token_expires_at=int(oauth2["refresh_token_expires_at"]),
        ),
    )
    return client.garth.dumps()


def _cleanup_expired() -> None:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    with _lock:
        expired = [
            session_id
            for session_id, session in _pending.items()
            if session["created_at"] < cutoff
        ]
        for session_id in expired:
            del _pending[session_id]


def _seconds_to_minutes(value: Any) -> int | None:
    if value is None:
        return None
    return int(value / 60)


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _start_time_seconds(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    return int(datetime.fromisoformat(value.replace(".0", "")).timestamp())


def _redact(message: str, *secrets: Any) -> str:
    result = message
    for secret in secrets:
        if isinstance(secret, str) and secret:
            result = result.replace(secret, "[redacted]")
    if _AUTH_WORKER_SECRET:
        result = result.replace(_AUTH_WORKER_SECRET, "[redacted]")
    return result
