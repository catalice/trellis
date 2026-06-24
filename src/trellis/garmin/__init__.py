"""Typed client boundary for the Allerac Garmin health worker."""

from .client import (
    GarminClient,
    GarminClientError,
    GarminConfigurationError,
    GarminHTTPError,
    GarminResponseError,
    GarminTransportError,
    JsonTransport,
    UrllibJsonTransport,
)
from .models import (
    GarminActivity,
    GarminActivityDetail,
    GarminAuthResult,
    GarminAuthStatus,
    GarminDailyHealth,
)

__all__ = [
    "GarminActivity",
    "GarminActivityDetail",
    "GarminAuthResult",
    "GarminAuthStatus",
    "GarminClient",
    "GarminClientError",
    "GarminConfigurationError",
    "GarminDailyHealth",
    "GarminHTTPError",
    "GarminResponseError",
    "GarminTransportError",
    "JsonTransport",
    "UrllibJsonTransport",
]
