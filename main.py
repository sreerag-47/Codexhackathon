from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

_client = None

def get_genai_client() -> genai.Client | None:
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        _client = genai.Client(api_key=api_key)
        return _client
    return None

Category = Literal["Road Hazard", "Sanitation Breaches", "Grid Infrastructure"]
Urgency = Literal["High", "Medium"]
Status = Literal["OPEN", "DISPATCHED", "RESOLVED"]

METADATA_SYSTEM_INSTRUCTION = (
    'Exclusively return a minified JSON object containing keys: '
    '{"category":"Road Hazard"|"Sanitation Breaches"|"Grid Infrastructure",'
    '"urgency":"High"|"Medium"}. '
    'Do not add conversational context or markdown wrappers.'
)

DISPATCH_SYSTEM_INSTRUCTION = (
    "Return one concise municipal work-order memo only. Use this format: "
    "OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. Location: Point [X, Y]. "
    "Urgency: [Level]. Action Required: [Synthesized structural summary of all citizen reports]."
)

app = FastAPI(title="Civic Pulse Spatial Grievance Matrix")
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.on_event("startup")
def startup_event():
    init_db()
    if not get_all_tickets():
        seed_golden_state()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)


class CitizenPayload(BaseModel):
    reporter_phone: str = Field(default="+919876543210")
    transcript_text: str
    latitude: float
    longitude: float
    category_override: str | None = None


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


class DispatchResponse(BaseModel):
    ticket_id: str
    generated_dispatch_memo: str
    status: Status


class DemoScript(BaseModel):
    seeded_claims: int
    starting_cluster: str
    microphone_prompt: str
    map_instruction: str
    expected_result: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DB_PATH = "civic_pulse.db"

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


def save_ticket(ticket: Ticket) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
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
    """, (
        ticket.ticket_id, ticket.title, ticket.category, ticket.composite_severity,
        ticket.active_report_count, ticket.ai_impact_synthesis, ticket.representative_lat,
        ticket.representative_lon, ticket.status, ticket.urgency, ticket.generated_dispatch_memo,
        json.dumps(ticket.complaint_texts), json.dumps(ticket.reporter_phones),
        json.dumps(ticket.complaint_images), ticket.updated_at
    ))
    conn.commit()
    conn.close()


def get_all_tickets(urgency: str | None = None) -> list[Ticket]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if urgency and urgency != "all":
        cursor.execute("SELECT * FROM tickets WHERE urgency = ?", (urgency,))
    else:
        cursor.execute("SELECT * FROM tickets")
    rows = cursor.fetchall()
    conn.close()
    
    tickets = []
    for row in rows:
        tickets.append(Ticket(
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
            updated_at=row[14]
        ))
    return tickets


def clear_tickets() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tickets")
    conn.commit()
    conn.close()


def check_spatial_match(lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
    # Strict geometric bounding box approximating a ~75-meter urban cluster radius
    return abs(lat1 - lat2) < 0.0007 and abs(lon1 - lon2) < 0.0007


def infer_metadata_locally(text: str) -> dict[str, str]:
    lowered = text.lower()
    if any(word in lowered for word in ["pothole", "road", "traffic", "swerving", "crack", "accident"]):
        category = "Road Hazard"
    elif any(word in lowered for word in ["garbage", "trash", "sewage", "waste", "sanitation", "drain"]):
        category = "Sanitation Breaches"
    elif any(word in lowered for word in ["power", "electric", "wire", "grid", "streetlight", "transformer"]):
        category = "Grid Infrastructure"
    else:
        category = "Road Hazard"
    urgency = "High" if any(word in lowered for word in ["massive", "danger", "wildly", "urgent", "fire", "collision", "blocked"]) else "Medium"
    return {"category": category, "urgency": urgency}


def parse_minified_json(raw: str) -> dict[str, str]:
    try:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        return json.loads(match.group(0) if match else raw)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


def normalize_metadata(metadata: dict[str, str]) -> dict[str, str]:
    category_aliases = {
        "Sanitation": "Sanitation Breaches",
        "Sanitation Breaches": "Sanitation Breaches",
        "Grid Utility": "Grid Infrastructure",
        "Grid Infrastructure": "Grid Infrastructure",
        "Road Hazard": "Road Hazard",
    }
    urgency_aliases = {"High": "High", "Medium": "Medium"}
    return {
        "category": category_aliases.get(metadata.get("category", ""), "Road Hazard"),
        "urgency": urgency_aliases.get(metadata.get("urgency", ""), "Medium"),
    }


def extract_metadata(text: str, image_bytes: bytes | None = None, mime_type: str | None = None) -> dict[str, str]:
    client = get_genai_client()
    if not client:
        return normalize_metadata(infer_metadata_locally(text))
    try:
        contents = [
            METADATA_SYSTEM_INSTRUCTION,
            f"Citizen report: {text}"
        ]
        if image_bytes and mime_type:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
            
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=contents,
        )
        parsed = parse_minified_json(response.text or "{}")
    except Exception:
        return normalize_metadata(infer_metadata_locally(text))
    return normalize_metadata({
        "category": parsed.get("category", "Road Hazard"),
        "urgency": parsed.get("urgency", "Medium"),
    })


def synthesize_summary(ticket: Ticket) -> str:
    joined = " ".join(ticket.complaint_texts)
    if ticket.active_report_count <= 2:
        return f"{ticket.active_report_count} citizen report(s) logged for this localized {ticket.category.lower()} cluster. Monitoring continues for escalation signals."
    client = get_genai_client()
    if client:
        try:
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=(
                    "Summarize these clustered municipal complaints in exactly two executive sentences, "
                    f"highlighting collective societal impact: {joined}"
                ),
            )
            if response.text:
                return response.text.strip()
        except Exception:
            pass
    return (
        f"{ticket.active_report_count} unique citizens have verified a dangerous {ticket.category.lower()} hotspot near "
        f"the selected coordinate. Aggregated reports indicate recurring public-safety impact requiring rapid municipal triage."
    )


def severity_for(count: int, urgency: str) -> str:
    if count >= 4 or urgency == "High":
        return "Critical"
    if count >= 2:
        return "Elevated"
    return "Watch"


def title_for(category: str) -> str:
    return {
        "Road Hazard": "Severe Structural Pothole Grid Near Main Entrance",
        "Sanitation Breaches": "Clustered Sanitation Breach Requiring Cleanup",
        "Grid Infrastructure": "Localized Grid Utility Fault Requiring Crew Review",
    }.get(category, "Municipal Grievance Cluster")


def refresh_ticket(ticket: Ticket) -> Ticket:
    ticket.active_report_count = len(ticket.complaint_texts)
    ticket.composite_severity = severity_for(ticket.active_report_count, ticket.urgency)
    ticket.ai_impact_synthesis = synthesize_summary(ticket)
    ticket.updated_at = now_iso()
    return ticket


def seed_golden_state() -> None:
    clear_tickets()
    reports = [
        "There is a massive pothole right outside the main gate, vehicles are swerving wildly.",
        "Two scooters nearly crashed while avoiding the same road depression near the entrance.",
        "The pothole is growing after rain and buses are braking suddenly at the gate.",
        "Cars keep crossing into oncoming traffic to avoid the broken road surface.",
    ]
    ticket1 = Ticket(
        ticket_id="cluster-uuid-8801",
        title=title_for("Road Hazard"),
        category="Road Hazard",
        composite_severity="Critical",
        active_report_count=4,
        ai_impact_synthesis="Four unique citizens have verified a dangerous road depression near the primary gate. Aggregated inputs report frequent evasive maneuvers into opposing traffic lanes, indicating severe collision risks.",
        representative_lat=10.0625,
        representative_lon=76.5312,
        status="OPEN",
        urgency="High",
        complaint_texts=reports,
        reporter_phones=[f"+91987654321{i}" for i in range(4)],
        complaint_images=[],
        updated_at=now_iso(),
    )
    save_ticket(ticket1)

    reports2 = [
        "Piles of uncollected garbage near the food court are attracting stray dogs.",
        "Overflowing dumpsters near the canteen have created a severe sanitation hazard.",
    ]
    ticket2 = Ticket(
        ticket_id="cluster-uuid-8802",
        title=title_for("Sanitation Breaches"),
        category="Sanitation Breaches",
        composite_severity="Elevated",
        active_report_count=2,
        ai_impact_synthesis="Multiple complaints received regarding overflowing waste containers near the dining quarters, causing safety and sanitation concerns.",
        representative_lat=10.0618,
        representative_lon=76.5305,
        status="DISPATCHED",
        urgency="Medium",
        generated_dispatch_memo="OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. Location: Point [10.0618, 76.5305]. Urgency: Medium. Action Required: Dispatched sanitation crew to clean up canteen area dumpsters.",
        complaint_texts=reports2,
        reporter_phones=["+919876543210", "+919876543219"],
        complaint_images=[],
        updated_at=now_iso(),
    )
    save_ticket(ticket2)

    reports3 = [
        "The streetlight at the main crossroad is flickering and going completely dark.",
    ]
    ticket3 = Ticket(
        ticket_id="cluster-uuid-8803",
        title=title_for("Grid Infrastructure"),
        category="Grid Infrastructure",
        composite_severity="Watch",
        active_report_count=1,
        ai_impact_synthesis="A single citizen reported a utility failure where a critical streetlight is dark. The grid utility team has addressed the bulb connection.",
        representative_lat=10.0631,
        representative_lon=76.5320,
        status="RESOLVED",
        urgency="Medium",
        generated_dispatch_memo="OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. Location: Point [10.0631, 76.5320]. Urgency: Medium. Action Required: Replaced flickering street lamp bulb.",
        complaint_texts=reports3,
        reporter_phones=["+919876543210"],
        complaint_images=[],
        updated_at=now_iso(),
    )
    save_ticket(ticket3)


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/health")
def health() -> dict[str, str | int]:
    tickets = get_all_tickets()
    return {"status": "ok", "open_tickets": len([ticket for ticket in tickets if ticket.status == "OPEN"])}


@app.get("/api/demo/script", response_model=DemoScript)
def demo_script() -> DemoScript:
    return DemoScript(
        seeded_claims=4,
        starting_cluster="[Road Hazard] 4 Active Claims Combined",
        microphone_prompt="There is a massive pothole outside the main gate and traffic is swerving into oncoming vehicles.",
        map_instruction="Click near the pre-seeded hot point at 10.0625, 76.5312 on the SVG grid.",
        expected_result="The Road Hazard cluster increments from 4 to 5 without a page refresh and keeps flashing red.",
    )


@app.post("/api/demo/reset")
def reset_demo() -> list[Ticket]:
    seed_golden_state()
    return get_all_tickets()


@app.get("/api/tickets")
def list_tickets(urgency: str | None = None, category: str | None = None, status: str | None = None) -> list[Ticket]:
    tickets = get_all_tickets(urgency)
    if category and category != "all":
        tickets = [t for t in tickets if t.category == category]
    if status and status != "all":
        tickets = [t for t in tickets if t.status == status]
    return tickets


@app.get("/api/tickets/user/{reporter_phone}")
def get_user_tickets(reporter_phone: str) -> list[Ticket]:
    """Return all tickets that contain the given reporter phone."""
    all_tickets = get_all_tickets()
    return [t for t in all_tickets if reporter_phone in t.reporter_phones]


@app.get("/api/tickets/{ticket_id}", response_model=Ticket)
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
    by_status = {s: sum(1 for t in tickets if t.status == s) for s in ["OPEN", "DISPATCHED", "RESOLVED"]}
    by_category = {}
    for t in tickets:
        by_category[t.category] = by_category.get(t.category, 0) + 1
    by_urgency = {u: sum(1 for t in tickets if t.urgency == u) for u in ["High", "Medium"]}
    total_reports = sum(t.active_report_count for t in tickets)
    return {
        "total_tickets": total,
        "total_citizen_reports": total_reports,
        "by_status": by_status,
        "by_category": by_category,
        "by_urgency": by_urgency,
    }


@app.post("/api/grievances/submit", response_model=Ticket)
def submit_grievance(
    reporter_phone: str = Form("+919876543210"),
    transcript_text: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    category_override: str | None = Form(None),
    file: UploadFile | None = File(None)
) -> Ticket:
    image_bytes = None
    mime_type = None
    image_url = None
    
    if file and file.filename:
        file_ext = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{file_ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        try:
            image_bytes = file.file.read()
            file.file.seek(0)
            mime_type = file.content_type
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            image_url = f"/static/uploads/{unique_filename}"
        except Exception as e:
            print(f"Error saving uploaded image: {e}")

    valid_categories = ["Road Hazard", "Sanitation Breaches", "Grid Infrastructure"]
    if category_override in valid_categories:
        category = category_override
        lowered = transcript_text.lower()
        urgency = "High" if any(word in lowered for word in ["massive", "danger", "wildly", "urgent", "fire", "collision", "blocked"]) else "Medium"
    else:
        metadata = extract_metadata(transcript_text, image_bytes, mime_type)
        category = metadata["category"]
        urgency = metadata["urgency"]
        
    tickets = get_all_tickets()
    for ticket in tickets:
        if (
            ticket.status == "OPEN"
            and ticket.category == category
            and check_spatial_match(latitude, longitude, ticket.representative_lat, ticket.representative_lon)
        ):
            ticket.complaint_texts.append(transcript_text)
            ticket.reporter_phones.append(reporter_phone)
            if image_url:
                ticket.complaint_images.append(image_url)
            ticket.urgency = "High" if "High" in [ticket.urgency, urgency] else "Medium"
            refreshed = refresh_ticket(ticket)
            save_ticket(refreshed)
            return refreshed
            
    ticket = Ticket(
        ticket_id=f"cluster-{uuid.uuid4().hex[:8]}",
        title=title_for(category),
        category=category,  # type: ignore[arg-type]
        composite_severity=severity_for(1, urgency),
        active_report_count=1,
        ai_impact_synthesis="1 citizen report logged for this new localized grievance cluster. Awaiting corroborating reports.",
        representative_lat=latitude,
        representative_lon=longitude,
        urgency=urgency,  # type: ignore[arg-type]
        complaint_texts=[transcript_text],
        reporter_phones=[reporter_phone],
        complaint_images=[image_url] if image_url else [],
        updated_at=now_iso(),
    )
    save_ticket(ticket)
    return ticket


@app.post("/api/tickets/{ticket_id}/dispatch", response_model=DispatchResponse)
def dispatch_ticket(ticket_id: str) -> DispatchResponse:
    tickets = get_all_tickets()
    ticket = next((item for item in tickets if item.ticket_id == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    prompt = (
        f"{DISPATCH_SYSTEM_INSTRUCTION}\nLocation: Point [{ticket.representative_lat}, {ticket.representative_lon}]. "
        f"Urgency: {ticket.urgency}. Citizen reports: {' | '.join(ticket.complaint_texts)}"
    )
    client = get_genai_client()
    if client:
        try:
            response = client.models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt)
            memo = (response.text or "").strip()
        except Exception:
            memo = (
                "OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS (FALLBACK). "
                f"Location: Point [{ticket.representative_lat}, {ticket.representative_lon}]. Urgency: {ticket.urgency}. "
                f"Action Required: {ticket.ai_impact_synthesis}"
            )
    else:
        memo = (
            "OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. "
            f"Location: Point [{ticket.representative_lat}, {ticket.representative_lon}]. Urgency: {ticket.urgency}. "
            f"Action Required: {ticket.ai_impact_synthesis}"
        )
    ticket.generated_dispatch_memo = memo
    ticket.status = "DISPATCHED"
    save_ticket(ticket)
    return DispatchResponse(ticket_id=ticket.ticket_id, generated_dispatch_memo=memo, status=ticket.status)


@app.post("/api/tickets/{ticket_id}/resolve", response_model=Ticket)
def resolve_ticket(ticket_id: str) -> Ticket:
    tickets = get_all_tickets()
    ticket = next((item for item in tickets if item.ticket_id == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.status = "RESOLVED"
    ticket.updated_at = now_iso()
    save_ticket(ticket)
    return ticket


app.mount("/static", StaticFiles(directory="static"), name="static")
