# Civic Pulse Spatial Grievance Matrix

A hackathon-ready FastAPI + Tailwind demo for clustering citizen grievances by category and tight coordinate proximity. The app boots with a deterministic four-report Road Hazard cluster near `10.0625, 76.5312`, lets judges dictate a new complaint, pin it on the inline SVG map, and watch the dashboard aggregate the fifth claim in real time.

## Features

- FastAPI backend in `main.py` with in-memory ticket clustering.
- Strict `0.0007` latitude/longitude micro-delta spatial matcher.
- `POST /api/grievances/submit` to merge or create grievance clusters.
- `POST /api/tickets/{ticket_id}/dispatch` to generate municipal dispatch memos.
- Gemini / Google GenAI metadata extraction when `GOOGLE_API_KEY` or `GEMINI_API_KEY` is present.
- Local deterministic metadata and dispatch fallbacks so demos still work without networked LLM access.
- Tailwind dark-mode split-screen dashboard with speech recognition and an interactive SVG coordinate canvas.
- `POST /api/demo/reset` to restore the golden presentation state instantly.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

## Demo Loop

1. Start the server and open the dashboard.
2. Confirm the right panel shows `[Road Hazard] 4 Active Claims Combined`.
3. Click **Dictate** and speak a pothole complaint, or use the prefilled text.
4. Click near the hot SVG map node centered around `10.0625, 76.5312`.
5. Submit the grievance and confirm the card increments from 4 to 5 without refreshing.
6. Click **Generate Dispatch** to produce the official work-order memo.

## API Quick Reference

- `GET /api/health` — basic service health and open-ticket count.
- `GET /api/tickets` — current aggregate dashboard state.
- `POST /api/grievances/submit` — submit an incoming citizen report.
- `POST /api/tickets/{ticket_id}/dispatch` — synthesize and store a dispatch memo.
- `GET /api/demo/script` — deterministic pitch flow details.
- `POST /api/demo/reset` — restore seeded golden state.


## Vercel Frontend Deployment

The static dashboard is configured to call the Render backend at `https://voxpop-ixt9.onrender.com`.

1. Import this repository into Vercel.
2. Set **Framework Preset** to `Other`.
3. Set **Root Directory** to `static`.
4. Leave **Build Command** empty.
5. Set **Output Directory** to `.`.
6. Deploy, then open the generated Vercel URL and confirm the dashboard loads tickets from the Render backend.

If the Render service is sleeping on the free tier, open `https://voxpop-ixt9.onrender.com/api/health` once before the live demo to wake it up.

## Demo Hardening Notes

- Gemini calls fall back to deterministic local logic if the API key is missing, the network fails, rate limits occur, or malformed JSON is returned.
- Ticket API responses mask reporter phone numbers before serialization.
- Repeated submissions from the same phone to the same ticket within 10 seconds are suppressed to prevent accidental double-click inflation.
- Set `DISPATCH_API_KEY` on the backend to require callers to send `X-Dispatch-Key` before generating dispatch memos.
