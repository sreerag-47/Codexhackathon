import io
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import main
import reporting


class ReportingTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / "artifacts" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=temp_root)
        self.env = patch.dict(
            os.environ,
            {
                "CIVIC_ADMIN_TOKEN": "test-reviewer",
                "CIVIC_ENABLE_AI": "0",
                "CIVIC_DEMO_MODE": "0",
            },
        )
        self.env.start()
        self.old_db = main.DB_PATH
        main.DB_PATH = str(Path(self.tmp.name) / "reports.db")
        # install captures the evidence directory, so patch Path via a fresh app module reload is unnecessary:
        # use a separate app per test to isolate files as well as SQLite.
        from fastapi import FastAPI

        self.app = FastAPI()
        with patch.dict(
            os.environ, {"CIVIC_EVIDENCE_DIR": str(Path(self.tmp.name) / "evidence")}
        ):
            reporting.install(self.app, main)
        main.init_db()
        self.client = TestClient(self.app)
        self.legacy = TestClient(main.app)
        self.admin = {"Authorization": "Bearer test-reviewer"}

    def tearDown(self):
        self.client.close()
        self.legacy.close()
        main.DB_PATH = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def fields(self, **changes):
        return {
            "transcript_text": "Broken road near the bus stop",
            "latitude": "10.0625",
            "longitude": "76.5312",
            "location_consent": "true",
            **changes,
        }

    def submit(self, **changes):
        return self.client.post("/api/reports", data=self.fields(**changes))

    def photo(self):
        out = io.BytesIO()
        Image.new("RGB", (20, 20), "gray").save(out, "PNG")
        return out.getvalue()

    def test_persistence_and_private_tracking(self):
        response = self.submit(input_source="voice_transcript", reporter_phone="12345")
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json()
        report = receipt["report"]
        self.assertEqual(report["input_source"], "voice_transcript")
        url = "/api/reports/" + report["report_id"]
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(url, headers={"Authorization": "Bearer wrong"}).status_code,
            404,
        )
        tracked = self.client.get(
            url, headers={"Authorization": "Bearer " + receipt["tracking_token"]}
        )
        self.assertEqual(tracked.json()["status"], "OPEN")
        self.assertEqual(len(tracked.json()["events"]), 1)
        self.assertEqual(tracked.headers["cache-control"], "no-store")
        with TestClient(self.app) as restarted:
            self.assertEqual(
                restarted.get(url, headers=self.admin).json()["transcript_text"],
                report["transcript_text"],
            )
        public = self.legacy.get("/api/tickets").json()[0]
        self.assertEqual(public["reporter_phones"], [])
        self.assertEqual(public["complaint_texts"], [])

    def test_location_and_content_validation(self):
        for changes in (
            {"latitude": "91"},
            {"longitude": "-181"},
            {"latitude": "nan"},
            {"latitude": "inf"},
            {"transcript_text": "    "},
            {"transcript_text": "a" * 5001},
            {"location_consent": "false"},
            {"input_source": "fake"},
            {"location_source": "gps-guess"},
            {"location_accuracy_m": "-1"},
            {"category_override": "Nonsense"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.submit(**changes).status_code, 422)
        self.assertEqual(main.get_all_tickets(), [])

    def test_photo_validation_and_original_evidence(self):
        raw = self.photo()
        response = self.client.post(
            "/api/reports",
            data=self.fields(),
            files={"file": ("x.exe", raw, "text/plain")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json()
        photo = receipt["report"]["photo"]
        self.assertEqual(photo["mime_type"], "image/png")
        self.assertEqual(photo["sha256"], reporting.digest(raw))
        self.assertEqual(self.client.get(photo["url"]).status_code, 404)
        fetched = self.client.get(
            photo["url"],
            headers={"Authorization": "Bearer " + receipt["tracking_token"]},
        )
        self.assertEqual(fetched.content, raw)
        for content in (b"<script>alert(1)</script>", raw[:30], b""):
            response = self.client.post(
                "/api/reports",
                data=self.fields(),
                files={"file": ("photo.png", content, "image/png")},
            )
            self.assertEqual(response.status_code, 415)

    def test_upload_limits(self):
        response = self.client.post(
            "/api/reports",
            data=self.fields(),
            files={"file": ("big.png", b"x" * (reporting.LIMIT + 1), "image/png")},
        )
        self.assertEqual(response.status_code, 413)
        response = self.client.post(
            "/api/reports", content=b"x" * (reporting.LIMIT + 65537)
        )
        self.assertEqual(response.status_code, 413)

    def test_idempotent_retry_and_conflicting_reuse(self):
        headers = {"Idempotency-Key": "a-random-client-key-123456789"}
        first = self.client.post("/api/reports", data=self.fields(), headers=headers)
        second = self.client.post("/api/reports", data=self.fields(), headers=headers)
        self.assertEqual(first.json(), second.json())
        changed = self.client.post(
            "/api/reports",
            data=self.fields(transcript_text="A different pothole"),
            headers=headers,
        )
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(main.get_all_tickets()[0].active_report_count, 1)

    def test_atomic_concurrent_independent_complaints(self):
        def send(index):
            return self.client.post(
                "/api/reports",
                data=self.fields(transcript_text=f"Road damaged near bus stop {index}"),
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(send, range(12)))
        self.assertTrue(
            all(r.status_code == 201 for r in results), [r.text for r in results]
        )
        self.assertEqual(len(main.get_all_tickets()), 12)
        self.assertTrue(all(t.active_report_count == 1 for t in main.get_all_tickets()))
        numbers = {r.json()["report"]["complaint_number"] for r in results}
        self.assertEqual(len(numbers), 12)

    def test_concurrent_retries_create_one_report(self):
        def send(_):
            return self.client.post(
                "/api/reports",
                data=self.fields(),
                headers={"Idempotency-Key": "same-random-retry-key-123456789"},
            )

        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(send, range(5)))
        self.assertTrue(all(r.status_code == 201 for r in responses))
        self.assertEqual(len({r.json()["report"]["report_id"] for r in responses}), 1)
        self.assertEqual(main.get_all_tickets()[0].active_report_count, 1)

    def test_individual_photo_association_is_not_shifted(self):
        first = self.submit().json()["report"]
        second = self.client.post(
            "/api/reports",
            data=self.fields(),
            files={"file": ("photo.png", self.photo(), "image/png")},
        ).json()["report"]
        self.assertNotEqual(first["ticket_id"], second["ticket_id"])
        reports = self.client.get(
            f"/api/tickets/{first['ticket_id']}/reports", headers=self.admin
        ).json()
        self.assertIsNone(reports[0]["photo"])
        second_reports = self.client.get(
            f"/api/tickets/{second['ticket_id']}/reports", headers=self.admin
        ).json()
        self.assertEqual(
            second_reports[0]["photo"]["url"],
            f"/api/reports/{second['report_id']}/photo",
        )

    def test_application_startup_and_explicit_demo_mode(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get("/api/health").json()["open_tickets"], 0)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn("/api/reports", client.get("/openapi.json").json()["paths"])
        with (
            patch.dict(os.environ, {"CIVIC_DEMO_MODE": "1"}),
            TestClient(main.app) as client,
        ):
            self.assertEqual(len(client.get("/api/tickets").json()), 0)
            self.assertEqual(
                client.post("/api/demo/reset", headers=self.admin).status_code, 410
            )

    def test_category_radius_and_resolved_clusters(self):
        first = self.submit().json()["ticket"]["ticket_id"]
        self.submit(latitude="10.0635")  # about 111 m away
        self.submit(transcript_text="Broken bridge railing")
        self.assertEqual(len(main.get_all_tickets()), 3)
        self.assertEqual(reporting.distance_m(0, 0, 0, 0), 0)
        self.assertEqual(
            self.client.patch(
                f"/api/tickets/{first}/status",
                json={
                    "status": "DISPATCHED",
                    "note": "Reviewed location and assigned local follow-up.",
                },
                headers=self.admin,
            ).status_code,
            200,
        )
        self.submit()
        self.assertEqual(len(main.get_all_tickets()), 4)

    def test_status_review_and_event_trail(self):
        receipt = self.submit().json()
        ticket = receipt["ticket"]["ticket_id"]
        url = f"/api/tickets/{ticket}/status"
        self.assertEqual(
            self.client.patch(
                url, json={"status": "DISPATCHED", "note": "Reviewed"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                url, json={"status": "RESOLVED", "note": "Reviewed"}, headers=self.admin
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.patch(
                url, json={"status": "DISPATCHED", "note": "     "}, headers=self.admin
            ).status_code,
            422,
        )
        draft = self.client.post(
            f"/api/tickets/{ticket}/dispatch", headers=self.admin
        ).json()
        self.assertTrue(draft["is_draft"])
        self.assertEqual(main.get_ticket(ticket).status, "OPEN")
        for status in ("DISPATCHED", "RESOLVED", "OPEN"):
            result = self.client.patch(
                url,
                json={"status": status, "note": "Reviewer checked original evidence."},
                headers=self.admin,
            )
            self.assertEqual(result.status_code, 200, result.text)
        tracked = self.client.get(
            "/api/reports/" + receipt["report"]["report_id"], headers=self.admin
        ).json()
        self.assertEqual(len(tracked["events"]), 4)

    def test_model_opt_out_unavailable_and_failure(self):
        data = reporting.ReportInput(**self.fields(), analysis_consent=False)
        with patch.object(main, "get_genai_client") as get_client:
            result = reporting.analyze(data, b"photo", {"mime_type": "image/png"}, main)
            get_client.assert_not_called()
            self.assertEqual(result["state"], "not_requested")
        data.analysis_consent = True
        with patch.object(main, "get_genai_client", return_value=None):
            self.assertEqual(
                reporting.analyze(data, b"photo", {}, main)["state"], "unavailable"
            )
        with patch.object(
            main, "get_genai_client", side_effect=RuntimeError("offline")
        ):
            result = reporting.analyze(data, b"photo", {}, main)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["visible_condition"], "not_assessed")

    def test_model_output_is_validated(self):
        data = reporting.ReportInput(**self.fields(), analysis_consent=True)
        types = SimpleNamespace(
            Part=SimpleNamespace(from_bytes=lambda **kw: kw),
            GenerateContentConfig=lambda **kw: kw,
        )
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "category": "Public Structure",
                    "urgency": "High",
                    "visible_condition": "unclear",
                    "observations": "Railing appears bent.",
                    "uncertainty": "Cannot determine depth or safety.",
                }
            )
        )
        client = SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kw: response)
        )
        with (
            patch.dict("sys.modules", {"google.genai": SimpleNamespace(types=types)}),
            patch.object(main, "get_genai_client", return_value=client),
        ):
            result = reporting.analyze(data, b"photo", {"mime_type": "image/png"}, main)
            self.assertEqual(result["state"], "completed")
            self.assertTrue(result["human_review_required"])
            response.text = '{"category":"ALIENS"}'
            self.assertEqual(
                reporting.analyze(data, b"photo", {"mime_type": "image/png"}, main)[
                    "state"
                ],
                "failed",
            )

    def test_database_failure_cleans_up_evidence(self):
        with patch.object(
            main, "save_ticket", side_effect=sqlite3.OperationalError("disk full")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.client.post(
                    "/api/reports",
                    data=self.fields(),
                    files={"file": ("x.png", self.photo(), "image/png")},
                )
        self.assertEqual(main.get_all_tickets(), [])
        self.assertEqual(list((Path(self.tmp.name) / "evidence").iterdir()), [])

    def test_legacy_routes_and_demo_safeguards(self):
        self.assertEqual(self.client.post("/api/grievances/submit").status_code, 410)
        self.assertEqual(
            self.client.get("/static/uploads/private.png").status_code, 404
        )
        self.assertEqual(self.legacy.get("/api/tickets/user/12345").status_code, 403)
        self.assertEqual(
            self.legacy.post("/api/demo/reset", headers=self.admin).status_code, 410
        )
        self.submit()
        with patch.dict(os.environ, {"CIVIC_DEMO_MODE": "1"}):
            self.assertEqual(
                self.legacy.post("/api/demo/reset", headers=self.admin).status_code, 410
            )


if __name__ == "__main__":
    unittest.main()
