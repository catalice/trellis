"""
Trellis health worker for Garmin Connect.

This service is stateless except for short-lived in-memory MFA sessions. Trellis
owns durable credentials, session dumps, and normalized metrics outside this
process.

Authentication: every non-health request must include:
  X-Worker-Secret: <HEALTH_WORKER_SECRET>
"""

from __future__ import annotations

import logging
import os
from datetime import date as Date
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

import garmin as garmin_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORKER_SECRET = os.getenv("HEALTH_WORKER_SECRET", "")
MAX_SYNC_DAYS = int(os.getenv("MAX_SYNC_DAYS", "90"))

app = FastAPI(title="trellis-health-worker", docs_url=None, redoc_url=None)


def _auth(x_worker_secret: str = Header(...)) -> None:
    if not WORKER_SECRET:
        raise RuntimeError("HEALTH_WORKER_SECRET is not set")
    if x_worker_secret != WORKER_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


class ConnectRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MfaRequest(BaseModel):
    session_id: str = Field(min_length=1)
    mfa_code: str = Field(min_length=1)


class SyncRequest(BaseModel):
    session_dump: str = Field(min_length=1)
    start_date: Date
    end_date: Date


class ActivitiesRequest(BaseModel):
    session_dump: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    date: Date | None = None


class ActivityDetailRequest(BaseModel):
    session_dump: str = Field(min_length=1)
    activity_id: str = Field(min_length=1)


class DailyHealthRequest(BaseModel):
    session_dump: str = Field(min_length=1)
    date: Date


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/connect")
def connect(req: ConnectRequest, x_worker_secret: str = Header(...)) -> dict:
    _auth(x_worker_secret)
    try:
        logger.info("Garmin connect requested")
        return garmin_service.authenticate(req.email, req.password)
    except Exception as error:
        detail = _safe_error(error, req.email, req.password)
        logger.warning("Garmin connect failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


@app.post("/mfa")
def mfa(req: MfaRequest, x_worker_secret: str = Header(...)) -> dict:
    _auth(x_worker_secret)
    try:
        logger.info("Garmin MFA completion requested")
        return garmin_service.complete_mfa(req.session_id, req.mfa_code)
    except Exception as error:
        detail = _safe_error(error, req.session_id, req.mfa_code)
        logger.warning("Garmin MFA failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


@app.post("/sync")
def sync(req: SyncRequest, x_worker_secret: str = Header(...)) -> dict[str, list[dict]]:
    _auth(x_worker_secret)
    if req.end_date < req.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be >= start_date",
        )
    if (req.end_date - req.start_date).days > MAX_SYNC_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range exceeds {MAX_SYNC_DAYS} days",
        )
    try:
        logger.info("Garmin sync requested for %s to %s", req.start_date, req.end_date)
        metrics = garmin_service.fetch_metrics(
            req.session_dump,
            req.start_date,
            req.end_date,
        )
        logger.info("Garmin sync returned %d daily records", len(metrics))
        return {"metrics": metrics}
    except Exception as error:
        detail = _safe_error(error, req.session_dump)
        logger.warning("Garmin sync failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


@app.post("/activities")
def activities(req: ActivitiesRequest, x_worker_secret: str = Header(...)) -> dict[str, list[dict]]:
    _auth(x_worker_secret)
    try:
        logger.info("Garmin activities requested: limit=%s date=%s", req.limit, req.date)
        records = garmin_service.fetch_recent_activities(
            req.session_dump,
            req.limit,
            req.date.isoformat() if req.date else None,
        )
        logger.info("Garmin activities returned %d records", len(records))
        return {"activities": records}
    except Exception as error:
        detail = _safe_error(error, req.session_dump)
        logger.warning("Garmin activities failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


@app.post("/activity-detail")
def activity_detail(req: ActivityDetailRequest, x_worker_secret: str = Header(...)) -> dict:
    _auth(x_worker_secret)
    try:
        logger.info("Garmin activity detail requested for %s", req.activity_id)
        return garmin_service.fetch_activity_detail(req.session_dump, req.activity_id)
    except Exception as error:
        detail = _safe_error(error, req.session_dump)
        logger.warning("Garmin activity detail failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


@app.post("/daily-health")
def daily_health(req: DailyHealthRequest, x_worker_secret: str = Header(...)) -> dict:
    _auth(x_worker_secret)
    try:
        logger.info("Garmin daily health requested for %s", req.date)
        return garmin_service.fetch_daily_health(req.session_dump, req.date.isoformat())
    except Exception as error:
        detail = _safe_error(error, req.session_dump)
        logger.warning("Garmin daily health failed: %s", detail, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from error


def _safe_error(error: Exception, *secrets: str | None) -> str:
    message = str(error).strip()
    if not message:
        message = error.__class__.__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    if WORKER_SECRET:
        message = message.replace(WORKER_SECRET, "[redacted]")
    return message[:300]


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
