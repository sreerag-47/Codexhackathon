"""Independent complaint follow-up: evidence, reopen review, and similarity feedback."""

import json
import secrets
from typing import Literal

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from reporting import database, read_image, require_admin, similar_reports


class Note(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    note: str = Field(min_length=5, max_length=2000)


class ReopenDecision(Note):
    decision: Literal["APPROVED", "REJECTED"]


class SimilarityFeedback(BaseModel):
    complaint_number: str = Field(pattern=r"^CP-\d{6,}$")
    assessment: Literal["possibly_related", "different"]


def init_followup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS resolutions (
        resolution_id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
        created_at TEXT NOT NULL, note TEXT NOT NULL, photo TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS reopen_requests (
        request_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, created_at TEXT NOT NULL,
        note TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'PENDING',
        decision_note TEXT, decided_at TEXT)""")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS one_pending_reopen ON reopen_requests(report_id) WHERE decision='PENDING'"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS similarity_feedback (
        feedback_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, complaint_number TEXT NOT NULL,
        assessment TEXT NOT NULL, created_at TEXT NOT NULL)""")


def save_resolution(conn, report_id, note, photo, timestamp, resolution_id=None):
    resolution_id = resolution_id or "resolution-" + secrets.token_hex(16)
    conn.execute(
        "INSERT INTO resolutions VALUES (?, ?, ?, ?, ?)",
        (
            resolution_id,
            report_id,
            timestamp,
            note,
            json.dumps(photo) if photo else None,
        ),
    )
    return resolution_id


def event(conn, core, ticket, note):
    conn.execute(
        "INSERT INTO ticket_events VALUES (?, ?, ?, ?, ?)",
        (secrets.token_hex(16), ticket.ticket_id, core.now_iso(), ticket.status, note),
    )


def install_followup(app, core, evidence_dir, load_report):
    def ticket_in_transaction(conn, report):
        return next(
            t
            for t in core.get_all_tickets(connection=conn)
            if t.ticket_id == report["ticket_id"]
        )

    @app.get("/api/reports/{report_id}/followups")
    def followups(report_id: str, request: Request):
        load_report(report_id, request)
        with database(core.DB_PATH) as conn:
            resolutions = conn.execute(
                "SELECT resolution_id, created_at, note, photo FROM resolutions WHERE report_id=? ORDER BY rowid",
                (report_id,),
            ).fetchall()
            reopens = conn.execute(
                "SELECT request_id, created_at, note, decision, decision_note, decided_at FROM reopen_requests WHERE report_id=? ORDER BY rowid",
                (report_id,),
            ).fetchall()
            feedback = conn.execute(
                "SELECT complaint_number, assessment, created_at FROM similarity_feedback WHERE report_id=? ORDER BY rowid",
                (report_id,),
            ).fetchall()
        return {
            "resolutions": [
                {
                    "resolution_id": r[0],
                    "created_at": r[1],
                    "note": r[2],
                    "photo": json.loads(r[3]) if r[3] else None,
                }
                for r in resolutions
            ],
            "reopen_requests": [
                dict(
                    zip(
                        (
                            "request_id",
                            "created_at",
                            "note",
                            "decision",
                            "decision_note",
                            "decided_at",
                        ),
                        r,
                    )
                )
                for r in reopens
            ],
            "similarity_feedback": [
                dict(zip(("complaint_number", "assessment", "created_at"), r))
                for r in feedback
            ],
        }

    @app.post("/api/reports/{report_id}/similar-confirmations")
    def confirm_similar(report_id: str, data: SimilarityFeedback, request: Request):
        report = load_report(report_id, request)
        # Only offer/accept feedback about candidates this reporter is allowed to see.
        if data.complaint_number not in {
            c["complaint_number"] for c in similar_reports(report, core)["candidates"]
        }:
            raise HTTPException(
                422,
                "Select a currently suggested nearby complaint. Complaints remain separate.",
            )
        with database(core.DB_PATH) as conn:
            last = conn.execute(
                "SELECT assessment FROM similarity_feedback WHERE report_id=? AND complaint_number=? ORDER BY rowid DESC LIMIT 1",
                (report_id, data.complaint_number),
            ).fetchone()
            if not last or last[0] != data.assessment:
                conn.execute(
                    "INSERT INTO similarity_feedback VALUES (?, ?, ?, ?, ?)",
                    (
                        secrets.token_hex(16),
                        report_id,
                        data.complaint_number,
                        data.assessment,
                        core.now_iso(),
                    ),
                )
        return {
            "complaint_number": data.complaint_number,
            "assessment": data.assessment,
            "notice": "Reporter opinion recorded. No complaints merged and no claim of verified identity or damage.",
        }

    @app.post("/api/reports/{report_id}/resolution", status_code=201)
    def resolve_with_evidence(
        report_id: str,
        request: Request,
        note: str = Form(...),
        file: UploadFile | None = File(None),
    ):
        require_admin(request)
        report = load_report(report_id, request)
        note = note.strip()
        if not 5 <= len(note) <= 2000:
            raise HTTPException(422, "Resolution note must be 5 to 2000 characters.")
        raw, photo = read_image(file)
        resolution_id = "resolution-" + secrets.token_hex(16)
        path = evidence_dir / resolution_id
        if photo:
            photo["url"] = f"/api/reports/{report_id}/resolutions/{resolution_id}/photo"
        created = False
        try:
            with database(core.DB_PATH) as conn:
                conn.execute("BEGIN IMMEDIATE")
                ticket = ticket_in_transaction(conn, report)
                if ticket.status != "DISPATCHED":
                    raise HTTPException(
                        409, "Only an in-review complaint can be resolved."
                    )
                if raw is not None:
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as out:
                        created = True
                        out.write(raw)
                timestamp = core.now_iso()
                save_resolution(conn, report_id, note, photo, timestamp, resolution_id)
                ticket.status, ticket.updated_at, ticket.generated_dispatch_memo = (
                    "RESOLVED",
                    timestamp,
                    note,
                )
                core.save_ticket(ticket, connection=conn)
                event(conn, core, ticket, "Resolution evidence recorded: " + note)
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        return {
            "resolution_id": resolution_id,
            "status": "RESOLVED",
            "note": note,
            "photo": photo,
        }

    @app.get("/api/reports/{report_id}/resolutions/{resolution_id}/photo")
    def resolution_photo(report_id: str, resolution_id: str, request: Request):
        load_report(report_id, request)
        with database(core.DB_PATH) as conn:
            row = conn.execute(
                "SELECT photo FROM resolutions WHERE report_id=? AND resolution_id=?",
                (report_id, resolution_id),
            ).fetchone()
        path = evidence_dir / resolution_id
        if not row or not row[0] or not path.is_file():
            raise HTTPException(404, "Resolution photo not found")
        return FileResponse(
            path,
            media_type=json.loads(row[0])["mime_type"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/reports/{report_id}/reopen-requests", status_code=201)
    def request_reopen(report_id: str, data: Note, request: Request):
        report = load_report(report_id, request)
        with database(core.DB_PATH) as conn:
            conn.execute("BEGIN IMMEDIATE")
            ticket = ticket_in_transaction(conn, report)
            if ticket.status != "RESOLVED":
                raise HTTPException(
                    409, "Reopen requests are available only for resolved complaints."
                )
            if conn.execute(
                "SELECT 1 FROM reopen_requests WHERE report_id=? AND decision='PENDING'",
                (report_id,),
            ).fetchone():
                raise HTTPException(409, "A reopen request is already pending review.")
            request_id = "reopen-" + secrets.token_hex(16)
            conn.execute(
                "INSERT INTO reopen_requests(request_id, report_id, created_at, note) VALUES (?, ?, ?, ?)",
                (request_id, report_id, core.now_iso(), data.note),
            )
            event(conn, core, ticket, "Citizen requested reopening: " + data.note)
        return {"request_id": request_id, "decision": "PENDING", "status": "RESOLVED"}

    @app.patch("/api/reports/{report_id}/reopen-requests/{request_id}")
    def decide_reopen(
        report_id: str, request_id: str, data: ReopenDecision, request: Request
    ):
        require_admin(request)
        report = load_report(report_id, request)
        with database(core.DB_PATH) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT decision FROM reopen_requests WHERE report_id=? AND request_id=?",
                (report_id, request_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Reopen request not found")
            if row[0] != "PENDING":
                raise HTTPException(
                    409, "This reopen request has already been reviewed."
                )
            ticket = ticket_in_transaction(conn, report)
            if data.decision == "APPROVED":
                if ticket.status != "RESOLVED":
                    raise HTTPException(
                        409,
                        "Complaint is no longer resolved; review the current state.",
                    )
                ticket.status, ticket.updated_at = "OPEN", core.now_iso()
                ticket.generated_dispatch_memo = data.note
                core.save_ticket(ticket, connection=conn)
            conn.execute(
                "UPDATE reopen_requests SET decision=?, decision_note=?, decided_at=? WHERE request_id=?",
                (data.decision, data.note, core.now_iso(), request_id),
            )
            event(
                conn,
                core,
                ticket,
                f"Reopen request {data.decision.lower()}: {data.note}",
            )
        return {
            "request_id": request_id,
            "decision": data.decision,
            "status": ticket.status,
        }
