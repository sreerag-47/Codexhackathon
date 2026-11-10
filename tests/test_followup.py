import json
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_reporting

import main
import reporting


class FollowupTests(unittest.TestCase):
    setUp = test_reporting.ReportingTests.setUp
    tearDown = test_reporting.ReportingTests.tearDown
    fields = test_reporting.ReportingTests.fields
    submit = test_reporting.ReportingTests.submit
    photo = test_reporting.ReportingTests.photo

    def owner(self, receipt):
        return {"Authorization": "Bearer " + receipt["tracking_token"]}

    def assign(self, receipt):
        result = self.client.patch(
            f"/api/tickets/{receipt['ticket']['ticket_id']}/status",
            headers=self.admin,
            json={
                "status": "DISPATCHED",
                "note": "Reviewed this individual complaint.",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)

    def resolve(self, receipt):
        self.assign(receipt)
        return self.client.post(
            f"/api/reports/{receipt['report']['report_id']}/resolution",
            headers=self.admin,
            data={"note": "Repair completed; original and after evidence retained."},
            files={"file": ("repair.png", self.photo(), "image/png")},
        )

    def test_similarity_never_merges_or_exposes_private_evidence(self):
        receipts = [
            self.submit(
                transcript_text="Pothole outside the bus stop",
                reporter_phone="private-number",
            ).json()
            for _ in range(4)
        ]
        self.submit(transcript_text="Pothole far away", latitude="11")
        self.submit(transcript_text="Garbage outside the bus stop")
        current = receipts[0]
        url = f"/api/reports/{current['report']['report_id']}/similar"
        self.assertEqual(self.client.get(url).status_code, 404)
        data = self.client.get(url, headers=self.owner(current)).json()
        self.assertEqual(data["same_reason_count_30d"], 4)
        self.assertEqual(data["nearby_similar_count_30d"], 3)
        self.assertTrue(data["many_similar_complaints"])
        self.assertNotIn("private-number", json.dumps(data))
        self.assertNotIn("transcript_text", json.dumps(data))
        self.assertEqual(len({r["ticket"]["ticket_id"] for r in receipts}), 4)
        target = receipts[1]
        self.assertEqual(
            self.client.get(
                f"/api/reports/{target['report']['report_id']}",
                headers=self.owner(current),
            ).status_code,
            404,
        )
        confirmed = self.client.post(
            f"/api/reports/{current['report']['report_id']}/similar-confirmations",
            headers=self.owner(current),
            json={
                "complaint_number": target["report"]["complaint_number"],
                "assessment": "possibly_related",
            },
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(all(t.active_report_count == 1 for t in main.get_all_tickets()))

    def test_similarity_confirmation_validation_and_history(self):
        first, second = self.submit().json(), self.submit().json()
        base = f"/api/reports/{first['report']['report_id']}"
        body = {
            "complaint_number": second["report"]["complaint_number"],
            "assessment": "possibly_related",
        }
        self.assertEqual(
            self.client.post(base + "/similar-confirmations", json=body).status_code,
            404,
        )
        for assessment in ("possibly_related", "possibly_related", "different"):
            self.assertEqual(
                self.client.post(
                    base + "/similar-confirmations",
                    headers=self.owner(first),
                    json={**body, "assessment": assessment},
                ).status_code,
                200,
            )
        records = self.client.get(base + "/followups", headers=self.owner(first)).json()
        self.assertEqual(
            [f["assessment"] for f in records["similarity_feedback"]],
            ["possibly_related", "different"],
        )
        self.assertEqual(
            self.client.post(
                base + "/similar-confirmations",
                headers=self.owner(first),
                json={**body, "complaint_number": first["report"]["complaint_number"]},
            ).status_code,
            422,
        )

    def test_resolution_photo_and_status_are_private_and_individual(self):
        receipt, other = self.submit().json(), self.submit().json()
        base = f"/api/reports/{receipt['report']['report_id']}"
        self.assertEqual(
            self.client.post(
                base + "/resolution",
                headers=self.owner(receipt),
                data={"note": "Claimed repair"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                base + "/resolution",
                headers=self.admin,
                data={"note": "Claimed repair"},
            ).status_code,
            409,
        )
        result = self.resolve(receipt)
        self.assertEqual(result.status_code, 201, result.text)
        url = result.json()["photo"]["url"]
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(url, headers=self.owner(other)).status_code, 404
        )
        self.assertEqual(
            self.client.get(url, headers=self.owner(receipt)).content, self.photo()
        )
        self.assertEqual(main.get_ticket(other["ticket"]["ticket_id"]).status, "OPEN")
        self.assertEqual(
            self.client.post(
                base + "/resolution",
                headers=self.admin,
                data={"note": "Repeated closure"},
            ).status_code,
            409,
        )
        data = self.client.get(base + "/followups", headers=self.owner(receipt)).json()
        self.assertEqual(len(data["resolutions"]), 1)

    def test_reopen_requires_owner_and_reviewer_decision(self):
        receipt = self.submit().json()
        base = f"/api/reports/{receipt['report']['report_id']}"
        self.assertEqual(
            self.client.post(
                base + "/reopen-requests",
                headers=self.owner(receipt),
                json={"note": "Still damaged"},
            ).status_code,
            409,
        )
        self.resolve(receipt)
        self.assertEqual(
            self.client.post(
                base + "/reopen-requests", json={"note": "Still damaged"}
            ).status_code,
            404,
        )
        result = self.client.post(
            base + "/reopen-requests",
            headers=self.owner(receipt),
            json={"note": "The railing is still broken after the claimed repair."},
        )
        self.assertEqual(result.status_code, 201)
        self.assertEqual(
            main.get_ticket(receipt["ticket"]["ticket_id"]).status, "RESOLVED"
        )
        decision_url = base + "/reopen-requests/" + result.json()["request_id"]
        self.assertEqual(
            self.client.post(
                base + "/reopen-requests",
                headers=self.owner(receipt),
                json={"note": "Another request"},
            ).status_code,
            409,
        )
        body = {
            "decision": "APPROVED",
            "note": "Reviewed the citizen follow-up; inspection needed.",
        }
        self.assertEqual(
            self.client.patch(
                decision_url, headers=self.owner(receipt), json=body
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(decision_url, headers=self.admin, json=body).json()[
                "status"
            ],
            "OPEN",
        )
        self.assertEqual(
            self.client.patch(decision_url, headers=self.admin, json=body).status_code,
            409,
        )
        data = self.client.get(base + "/followups", headers=self.owner(receipt)).json()
        self.assertEqual(len(data["resolutions"]), 1)
        self.assertEqual(data["reopen_requests"][0]["decision"], "APPROVED")

    def test_concurrent_reopen_and_rejection(self):
        receipt = self.submit().json()
        self.resolve(receipt)
        base = f"/api/reports/{receipt['report']['report_id']}"

        def send(_):
            return self.client.post(
                base + "/reopen-requests",
                headers=self.owner(receipt),
                json={"note": "Damage remains visible."},
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, range(4)))
        self.assertEqual(sorted(r.status_code for r in responses), [201, 409, 409, 409])
        request_id = next(
            r.json()["request_id"] for r in responses if r.status_code == 201
        )
        result = self.client.patch(
            base + "/reopen-requests/" + request_id,
            headers=self.admin,
            json={
                "decision": "REJECTED",
                "note": "Checked current evidence; no reopening justified.",
            },
        )
        self.assertEqual(result.json()["status"], "RESOLVED")

    def test_lookup_by_number_still_requires_tracking_token(self):
        receipt = self.submit().json()
        url = "/api/complaints/" + receipt["report"]["complaint_number"]
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(url, headers=self.owner(receipt)).json()["report_id"],
            receipt["report"]["report_id"],
        )

    def test_legacy_shared_reports_migrate_without_losing_evidence_or_tokens(self):
        first, second = self.submit().json(), self.submit().json()
        old_id = "cluster-legacy-shared"
        with reporting.database(main.DB_PATH) as conn:
            old = main.get_ticket(first["ticket"]["ticket_id"]).model_copy(
                update={"ticket_id": old_id}
            )
            main.save_ticket(old, connection=conn)
            for receipt in (first, second):
                report = dict(receipt["report"])
                report.pop("complaint_number")
                report["ticket_id"] = old_id
                conn.execute(
                    "UPDATE reports SET ticket_id=?, payload=? WHERE report_id=?",
                    (old_id, json.dumps(report), report["report_id"]),
                )
        reporting.separate_legacy_reports(main)
        recovered = [
            self.client.get(
                f"/api/reports/{r['report']['report_id']}", headers=self.owner(r)
            ).json()
            for r in (first, second)
        ]
        self.assertNotEqual(recovered[0]["ticket_id"], recovered[1]["ticket_id"])
        self.assertTrue(all(r["legacy_ticket_id"] == old_id for r in recovered))
        self.assertEqual(
            recovered[0]["transcript_text"], first["report"]["transcript_text"]
        )
        reporting.separate_legacy_reports(
            main
        )  # rerunning migration changes no report identities
        again = self.client.get(
            f"/api/reports/{first['report']['report_id']}", headers=self.owner(first)
        ).json()
        self.assertEqual(again["complaint_number"], recovered[0]["complaint_number"])
