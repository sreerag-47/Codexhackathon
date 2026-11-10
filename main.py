from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from reporting import install

_client = None


def get_genai_client():
    global _client
    if os.getenv("CIVIC_ENABLE_AI") != "1":
        return None
    if _client is not None:
        return _client
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=15000)
        )
        return _client
    return None


Category = Literal[
    "Road Hazard",
    "Sanitation Breaches",
    "Grid Infrastructure",
    "Public Structure",
    "Other",
]
Urgency = Literal["High", "Medium"]
Status = Literal["OPEN", "DISPATCHED", "RESOLVED"]

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Civic Pulse Reporting API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin for origin in os.getenv("CIVIC_CORS_ORIGINS", "").split(",") if origin
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)


class Ticket(BaseModel):
    ticket_id: str
    title: str
    category: Category
    composite_severity: str
    active_report_count: int
    ai_impact_synthesis: str
    representative_lat: float
    representative_lon: float
    status: Status = "OPEN"
    urgency: Urgency = "Medium"
    generated_dispatch_memo: str = "PENDING_APPROVAL"
    complaint_texts: list[str]
    reporter_phones: list[str]
    complaint_images: list[str] = Field(default_factory=list)
    updated_at: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DB_PATH = os.getenv("CIVIC_DB_PATH", str(BASE_DIR / "civic_pulse.db"))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            composite_severity TEXT,
            active_report_count INTEGER,
            ai_impact_synthesis TEXT,
            representative_lat REAL,
            representative_lon REAL,
            status TEXT,
            urgency TEXT,
            generated_dispatch_memo TEXT,
            complaint_texts TEXT,
            reporter_phones TEXT,
            complaint_images TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    from reporting import init_reports

    init_reports(DB_PATH)
    from reporting import separate_legacy_reports

    separate_legacy_reports(sys.modules[__name__])


def save_ticket(ticket: Ticket, connection=None) -> None:
    conn = connection or sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tickets (
            ticket_id, title, category, composite_severity, active_report_count,
            ai_impact_synthesis, representative_lat, representative_lon, status,
            urgency, generated_dispatch_memo, complaint_texts, reporter_phones,
            complaint_images, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticket_id) DO UPDATE SET
            title=excluded.title,
            category=excluded.category,
            composite_severity=excluded.composite_severity,
            active_report_count=excluded.active_report_count,
            ai_impact_synthesis=excluded.ai_impact_synthesis,
            representative_lat=excluded.representative_lat,
            representative_lon=excluded.representative_lon,
            status=excluded.status,
            urgency=excluded.urgency,
            generated_dispatch_memo=excluded.generated_dispatch_memo,
            complaint_texts=excluded.complaint_texts,
            reporter_phones=excluded.reporter_phones,
            complaint_images=excluded.complaint_images,
            updated_at=excluded.updated_at
    """,
        (
            ticket.ticket_id,
            ticket.title,
            ticket.category,
            ticket.composite_severity,
            ticket.active_report_count,
            ticket.ai_impact_synthesis,
            ticket.representative_lat,
            ticket.representative_lon,
            ticket.status,
            ticket.urgency,
            ticket.generated_dispatch_memo,
            json.dumps(ticket.complaint_texts),
            json.dumps(ticket.reporter_phones),
            json.dumps(ticket.complaint_images),
            ticket.updated_at,
        ),
    )
    if connection is None:
        conn.commit()
        conn.close()


def get_all_tickets(urgency: str | None = None, connection=None) -> list[Ticket]:
    conn = connection or sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if urgency and urgency != "all":
        cursor.execute(
            "SELECT * FROM tickets WHERE ticket_id NOT IN (SELECT ticket_id FROM legacy_ticket_archive) AND urgency = ?",
            (urgency,),
        )
    else:
        cursor.execute(
            "SELECT * FROM tickets WHERE ticket_id NOT IN (SELECT ticket_id FROM legacy_ticket_archive)"
        )
    rows = cursor.fetchall()
    if connection is None:
        conn.close()

    tickets = []
    for row in rows:
        tickets.append(
            Ticket(
                ticket_id=row[0],
                title=row[1],
                category=row[2],
                composite_severity=row[3],
                active_report_count=row[4],
                ai_impact_synthesis=row[5],
                representative_lat=row[6],
                representative_lon=row[7],
                status=row[8],
                urgency=row[9],
                generated_dispatch_memo=row[10],
                complaint_texts=json.loads(row[11]),
                reporter_phones=json.loads(row[12]),
                complaint_images=json.loads(row[13]),
                updated_at=row[14],
            )
        )
    return tickets


def infer_metadata_locally(text: str) -> dict[str, str]:
    lowered = text.lower()
    if any(
        word in lowered
        for word in ["bridge", "building", "railing", "public structure", "wall"]
    ):
        category = "Public Structure"
    elif any(
        word in lowered
        for word in ["pothole", "road", "traffic", "swerving", "crack", "accident"]
    ):
        category = "Road Hazard"
    elif any(
        word in lowered
        for word in ["garbage", "trash", "sewage", "waste", "sanitation", "drain"]
    ):
        category = "Sanitation Breaches"
    elif any(
        word in lowered
        for word in ["power", "electric", "wire", "grid", "streetlight", "transformer"]
    ):
        category = "Grid Infrastructure"
    else:
        category = "Other"
    urgency = (
        "High"
        if any(
            word in lowered
            for word in [
                "massive",
                "danger",
                "wildly",
                "urgent",
                "fire",
                "collision",
                "blocked",
            ]
        )
        else "Medium"
    )
    return {"category": category, "urgency": urgency}


def synthesize_summary(ticket: Ticket) -> str:
    return (
        f"Individual complaint {ticket.ticket_id}. Category: {ticket.category}. "
        f"Tentative review priority: {ticket.urgency}. "
        "Unverified citizen evidence; similar complaints are shown separately and do not establish damage severity."
    )


def severity_for(count: int, urgency: str) -> str:
    return "Priority review" if urgency == "High" else "Routine review"


def title_for(category: str) -> str:
    return {
        "Road Hazard": "Reported road condition",
        "Sanitation Breaches": "Reported sanitation concern",
        "Grid Infrastructure": "Reported utility issue",
        "Public Structure": "Reported public structure damage",
        "Other": "Public place concern",
    }.get(category, "Municipal Grievance Cluster")


def refresh_ticket(ticket: Ticket) -> Ticket:
    ticket.active_report_count = len(ticket.complaint_texts)
    ticket.composite_severity = severity_for(ticket.active_report_count, ticket.urgency)
    ticket.ai_impact_synthesis = synthesize_summary(ticket)
    ticket.updated_at = now_iso()
    return ticket


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str | int]:
    tickets = get_all_tickets()
    return {
        "status": "ok",
        "open_tickets": len([ticket for ticket in tickets if ticket.status == "OPEN"]),
    }


@app.get("/api/demo/script")
@app.post("/api/demo/reset")
def retired_grouped_demo():
    raise HTTPException(
        410,
        "Grouped demos are retired. Submit individual complaints in a separate test database.",
    )


@app.get("/api/tickets")
def list_tickets(
    request: Request,
    urgency: Urgency | Literal["all"] | None = None,
    category: Category | Literal["all"] | None = None,
    status: Status | Literal["all"] | None = None,
):
    from reporting import public_ticket

    tickets = get_all_tickets(urgency)
    if category and category != "all":
        tickets = [t for t in tickets if t.category == category]
    if status and status != "all":
        tickets = [t for t in tickets if t.status == status]
    return [public_ticket(ticket, request) for ticket in tickets]


@app.get("/api/tickets/user/{reporter_phone}")
def get_user_tickets(reporter_phone: str, request: Request) -> list[Ticket]:
    from reporting import require_admin

    require_admin(request)
    """Return all tickets that contain the given reporter phone."""
    all_tickets = get_all_tickets()
    return [t for t in all_tickets if reporter_phone in t.reporter_phones]


@app.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: str, request: Request):
    from reporting import public_ticket

    return public_ticket(get_ticket(ticket_id), request)


def get_ticket(ticket_id: str) -> Ticket:
    tickets = get_all_tickets()
    ticket = next((t for t in tickets if t.ticket_id == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.get("/api/stats")
def get_stats() -> dict:
    """Return high-level statistics for the authority dashboard."""
    tickets = get_all_tickets()
    total = len(tickets)
    by_status = {
        s: sum(1 for t in tickets if t.status == s)
        for s in ["OPEN", "DISPATCHED", "RESOLVED"]
    }
    by_category = {}
    for t in tickets:
        by_category[t.category] = by_category.get(t.category, 0) + 1
    by_urgency = {
        u: sum(1 for t in tickets if t.urgency == u) for u in ["High", "Medium"]
    }
    total_reports = sum(t.active_report_count for t in tickets)
    return {
        "total_tickets": total,
        "total_citizen_reports": total_reports,
        "by_status": by_status,
        "by_category": by_category,
        "by_urgency": by_urgency,
    }


install(app, sys.modules[__name__])
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
