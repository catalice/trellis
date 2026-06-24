from __future__ import annotations

import unittest
from datetime import date
from typing import Any, Mapping

from trellis.garmin import (
    GarminAuthStatus,
    GarminClient,
    GarminConfigurationError,
    GarminHTTPError,
    GarminResponseError,
    GarminTransportError,
)


class FakeTransport:
    def __init__(self, *responses: Any):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout: float,
    ) -> Any:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": dict(body) if body is not None else None,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GarminClientTest(unittest.TestCase):
    def client(self, transport: FakeTransport) -> GarminClient:
        return GarminClient(
            "http://health-worker:8001/",
            "worker-secret",
            timeout=12.5,
            transport=transport,
        )

    def test_connect_sends_auth_header_and_normalizes_success(self):
        transport = FakeTransport({"status": "success", "session_dump": "session-data"})

        result = self.client(transport).connect("cat@example.com", "password")

        self.assertEqual(GarminAuthStatus.SUCCESS, result.status)
        self.assertEqual("session-data", result.session_dump)
        self.assertFalse(result.requires_mfa)
        self.assertEqual(
            {
                "method": "POST",
                "url": "http://health-worker:8001/connect",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Worker-Secret": "worker-secret",
                },
                "body": {"email": "cat@example.com", "password": "password"},
                "timeout": 12.5,
            },
            transport.requests[0],
        )

    def test_connect_normalizes_mfa_and_complete_mfa(self):
        transport = FakeTransport(
            {"status": "mfa_required", "session_id": "pending-1"},
            {"status": "success", "session_dump": "authenticated-session"},
        )
        client = self.client(transport)

        pending = client.connect("cat@example.com", "password")
        completed = client.complete_mfa("pending-1", "123456")

        self.assertTrue(pending.requires_mfa)
        self.assertEqual("pending-1", pending.mfa_session_id)
        self.assertEqual("authenticated-session", completed.session_dump)
        self.assertEqual(
            {"session_id": "pending-1", "mfa_code": "123456"},
            transport.requests[1]["body"],
        )

    def test_sync_normalizes_daily_metrics_and_preserves_raw(self):
        transport = FakeTransport(
            {
                "metrics": [
                    {
                        "date": "2026-06-06",
                        "steps": 10234,
                        "sleep_duration_minutes": 445,
                        "resting_hr": 52,
                        "body_battery_max": 86,
                        "hrv_last_night": 48.5,
                        "future_metric": "preserved",
                    }
                ]
            }
        )

        metrics = self.client(transport).sync(
            "session",
            date(2026, 6, 6),
            date(2026, 6, 6),
        )

        self.assertEqual(1, len(metrics))
        self.assertEqual(date(2026, 6, 6), metrics[0].date)
        self.assertEqual(10234, metrics[0].steps)
        self.assertEqual(52, metrics[0].resting_heart_rate)
        self.assertEqual(86, metrics[0].body_battery_maximum)
        self.assertEqual(48.5, metrics[0].hrv_last_night)
        self.assertEqual("preserved", metrics[0].raw["future_metric"])
        self.assertEqual(
            {
                "session_dump": "session",
                "start_date": "2026-06-06",
                "end_date": "2026-06-06",
            },
            transport.requests[0]["body"],
        )

    def test_daily_health_uses_requested_date_when_worker_omits_it(self):
        transport = FakeTransport(
            {
                "steps": 8000,
                "avg_hr": 71,
                "sleep_score": 79,
                "hrv_status": "BALANCED",
            }
        )

        metric = self.client(transport).daily_health(
            "session",
            date(2026, 6, 7),
        )

        self.assertEqual(date(2026, 6, 7), metric.date)
        self.assertEqual(71, metric.average_heart_rate)
        self.assertEqual("BALANCED", metric.hrv_status)

    def test_activities_normalize_worker_field_names(self):
        transport = FakeTransport(
            {
                "activities": [
                    {
                        "activityId": 987654,
                        "activityName": "Barcelona Run",
                        "activityType": "running",
                        "startTimeInSeconds": 1780819200,
                        "duration": 3600,
                        "calories": 510,
                        "avgHeartRate": 151,
                        "maxHeartRate": 174,
                        "distance": 10012.5,
                        "elevationGain": 42.0,
                    }
                ]
            }
        )

        activities = self.client(transport).activities(
            "session",
            limit=20,
            on_date=date(2026, 6, 7),
        )

        self.assertEqual("987654", activities[0].activity_id)
        self.assertEqual("Barcelona Run", activities[0].name)
        self.assertEqual("running", activities[0].activity_type)
        self.assertEqual(3600000.0, activities[0].duration_milliseconds)
        self.assertEqual(10012.5, activities[0].distance_meters)
        self.assertEqual(
            {"session_dump": "session", "limit": 20, "date": "2026-06-07"},
            transport.requests[0]["body"],
        )

    def test_activities_keep_large_duration_values_as_milliseconds(self):
        transport = FakeTransport(
            {
                "activities": [
                    {
                        "activityId": 987654,
                        "activityName": "Barcelona Run",
                        "activityType": "running",
                        "duration": 3600000,
                    }
                ]
            }
        )

        activity = self.client(transport).activities("session")[0]

        self.assertEqual(3600000.0, activity.duration_milliseconds)

    def test_activity_detail_preserves_split_payloads(self):
        transport = FakeTransport(
            {
                "activityId": 987654,
                "splits": [{"distance": 1000, "duration": 360}],
                "splitSummaries": {"running": [{"distance": 1000}]},
                "typedSplits": {"intervals": []},
            }
        )

        detail = self.client(transport).activity_detail("session", "987654")

        self.assertEqual("987654", detail.activity_id)
        self.assertEqual([{"distance": 1000, "duration": 360}], detail.splits)
        self.assertEqual({"intervals": []}, detail.typed_splits)
        self.assertEqual(
            {"session_dump": "session", "activity_id": "987654"},
            transport.requests[0]["body"],
        )

    def test_malformed_responses_raise_clear_contract_errors(self):
        cases = [
            (
                lambda client: client.connect("cat@example.com", "password"),
                {"status": "success"},
                "/connect.session_dump",
            ),
            (
                lambda client: client.sync(
                    "session",
                    date(2026, 6, 6),
                    date(2026, 6, 7),
                ),
                {"metrics": {"date": "2026-06-06"}},
                "/sync.metrics",
            ),
            (
                lambda client: client.activities("session"),
                {
                    "activities": [
                        {"activityId": 1, "activityName": "Run", "activityType": None}
                    ]
                },
                "activityType",
            ),
            (
                lambda client: client.daily_health("session", date(2026, 6, 7)),
                {"steps": "many"},
                "steps",
            ),
        ]
        for operation, response, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(GarminResponseError, expected):
                    operation(self.client(FakeTransport(response)))

    def test_configuration_and_input_validation(self):
        with self.assertRaises(GarminConfigurationError):
            GarminClient("", "secret")
        with self.assertRaises(GarminConfigurationError):
            GarminClient("http://worker", "")
        with self.assertRaises(ValueError):
            self.client(FakeTransport()).sync(
                "session",
                date(2026, 6, 8),
                date(2026, 6, 7),
            )
        with self.assertRaises(ValueError):
            self.client(FakeTransport()).activities("session", limit=0)

    def test_transport_errors_propagate_without_leaking_credentials(self):
        errors = [
            GarminTransportError("Garmin worker timed out after 5 seconds"),
            GarminHTTPError(401, "Unauthorized"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as raised:
                    self.client(FakeTransport(error)).daily_health(
                        "sensitive-session",
                        date(2026, 6, 7),
                    )
                self.assertNotIn("sensitive-session", str(raised.exception))
                self.assertNotIn("worker-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
