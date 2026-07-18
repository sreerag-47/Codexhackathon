from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel, Field

Category = Literal["Road Hazard", "Sanitation Breaches", "Grid Infrastructure"]
Urgency = Literal["High", "Medium"]
Status = Literal["OPEN", "DISPATCHED"]

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CitizenPayload(BaseModel):
    reporter_phone: str = Field(default="+919876543210")
    transcript_text: str
    latitude: float
    longitude: float


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
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    return json.loads(match.group(0) if match else raw)


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


def extract_metadata(text: str) -> dict[str, str]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return normalize_metadata(infer_metadata_locally(text))
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=f"{METADATA_SYSTEM_INSTRUCTION}\nCitizen report: {text}",
    )
    parsed = parse_minified_json(response.text or "{}")
    return normalize_metadata({
        "category": parsed.get("category", "Road Hazard"),
        "urgency": parsed.get("urgency", "Medium"),
    })


def synthesize_summary(ticket: Ticket) -> str:
    joined = " ".join(ticket.complaint_texts)
    if ticket.active_report_count <= 2:
        return f"{ticket.active_report_count} citizen report(s) logged for this localized {ticket.category.lower()} cluster. Monitoring continues for escalation signals."
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=(
                "Summarize these clustered municipal complaints in exactly two executive sentences, "
                f"highlighting collective societal impact: {joined}"
            ),
        )
        if response.text:
            return response.text.strip()
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


TICKETS: list[Ticket] = []


def seed_golden_state() -> None:
    TICKETS.clear()
    reports = [
        "There is a massive pothole right outside the main gate, vehicles are swerving wildly.",
        "Two scooters nearly crashed while avoiding the same road depression near the entrance.",
        "The pothole is growing after rain and buses are braking suddenly at the gate.",
        "Cars keep crossing into oncoming traffic to avoid the broken road surface.",
    ]
    ticket = Ticket(
        ticket_id="cluster-uuid-8801",
        title=title_for("Road Hazard"),
        category="Road Hazard",
        composite_severity="Critical",
        active_report_count=4,
        ai_impact_synthesis="Four unique citizens have verified a dangerous road depression near the primary gate. Aggregated inputs report frequent evasive maneuvers into opposing traffic lanes, indicating severe collision risks.",
        representative_lat=10.0625,
        representative_lon=76.5312,
        urgency="High",
        complaint_texts=reports,
        reporter_phones=[f"+91987654321{i}" for i in range(4)],
        updated_at=now_iso(),
    )
    TICKETS.append(ticket)


seed_golden_state()


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "open_tickets": len([ticket for ticket in TICKETS if ticket.status == "OPEN"])}


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
    return TICKETS


@app.get("/api/tickets")
def list_tickets() -> list[Ticket]:
    return TICKETS


@app.post("/api/grievances/submit", response_model=Ticket)
def submit_grievance(payload: CitizenPayload) -> Ticket:
    metadata = extract_metadata(payload.transcript_text)
    category = metadata["category"]
    urgency = metadata["urgency"]
    for ticket in TICKETS:
        if (
            ticket.status == "OPEN"
            and ticket.category == category
            and check_spatial_match(payload.latitude, payload.longitude, ticket.representative_lat, ticket.representative_lon)
        ):
            ticket.complaint_texts.append(payload.transcript_text)
            ticket.reporter_phones.append(payload.reporter_phone)
            ticket.urgency = "High" if "High" in [ticket.urgency, urgency] else "Medium"
            return refresh_ticket(ticket)
    ticket = Ticket(
        ticket_id=f"cluster-{uuid.uuid4().hex[:8]}",
        title=title_for(category),
        category=category,  # type: ignore[arg-type]
        composite_severity=severity_for(1, urgency),
        active_report_count=1,
        ai_impact_synthesis="1 citizen report logged for this new localized grievance cluster. Awaiting corroborating reports.",
        representative_lat=payload.latitude,
        representative_lon=payload.longitude,
        urgency=urgency,  # type: ignore[arg-type]
        complaint_texts=[payload.transcript_text],
        reporter_phones=[payload.reporter_phone],
        updated_at=now_iso(),
    )
    TICKETS.append(ticket)
    return ticket


@app.post("/api/tickets/{ticket_id}/dispatch", response_model=DispatchResponse)
def dispatch_ticket(ticket_id: str) -> DispatchResponse:
    ticket = next((item for item in TICKETS if item.ticket_id == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    prompt = (
        f"{DISPATCH_SYSTEM_INSTRUCTION}\nLocation: Point [{ticket.representative_lat}, {ticket.representative_lon}]. "
        f"Urgency: {ticket.urgency}. Citizen reports: {' | '.join(ticket.complaint_texts)}"
    )
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt)
        memo = (response.text or "").strip()
    else:
        memo = (
            "OFFICIAL DISPATCH ORDER - DEPT OF PUBLIC WORKS. "
            f"Location: Point [{ticket.representative_lat}, {ticket.representative_lon}]. Urgency: {ticket.urgency}. "
            f"Action Required: {ticket.ai_impact_synthesis}"
        )
    ticket.generated_dispatch_memo = memo
    ticket.status = "DISPATCHED"
    return DispatchResponse(ticket_id=ticket.ticket_id, generated_dispatch_memo=memo, status=ticket.status)


app.mount("/static", StaticFiles(directory="static"), name="static")
