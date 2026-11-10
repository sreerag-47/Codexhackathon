# Civic Pulse

A civic reporting prototype for road damage, public structures, sanitation and utilities. Citizens submit an editable description (typed or browser-dictated), a confirmed issue location and an optional photo. Reviewers inspect the original evidence and record status updates. No reports are sent to authorities.

## Run locally

Requires Python 3.12+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000; interactive API documentation is at `/docs`. Data starts empty. Set `CIVIC_ADMIN_TOKEN` to a long random secret before starting, then enter that token in the existing dashboard to use reviewer actions. The token is held in the page, not saved by the app. Changing the visual role alone grants no access.

```powershell
$env:CIVIC_ADMIN_TOKEN = '<your-long-random-secret>'
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## Reporting pipeline

1. The citizen types a description, or explicitly starts browser dictation and reviews the transcript. The backend stores the transcript and its input source; it does not receive audio or provide server transcription.
2. The citizen chooses a map pin/manual coordinates, or explicitly requests device location, and confirms storage/display consent. The backend validates finite coordinates, ranges, accuracy and input length.
3. An optional JPEG/PNG/WebP is checked by decoded image content, limited to 8 MiB and 20 megapixels. The total multipart request is bounded before parsing, including chunked requests. Original bytes and a SHA-256 digest are retained outside static hosting.
4. Text rules supply a tentative category and review priority. If explicitly enabled and consented, the existing Gemini integration can assess visible conditions. `not_requested`, `unavailable`, `failed` and `completed` states distinguish actual image analysis from fallback. No photo result means `visible_condition=not_assessed`.
5. SQLite atomically stores each report as its own numbered complaint (`CP-000001`, etc.) and appends its own event history. Complaints are never automatically merged. Same-category reports with matching description keywords in the last 30 days are counted separately; possible nearby matches within 150 metres use latitude-aware Haversine distance. These are heuristic suggestions, not proof of the same defect or distinct reporters.
6. The response returns a report ID, complaint number and private tracking token. The citizen can retrieve their evidence and status history with the token, including lookup by complaint number. Receipts stay in the tab session by default; optional local recovery and receipt export/import support later access.
7. A reviewer records a note and changes the individual complaint's local status. OPEN -> DISPATCHED -> RESOLVED; DISPATCHED and RESOLVED can return to OPEN. Resolution records a required note and an optional repair photo. A reporter can request reopening; only a reviewer can approve or reject that request. The existing DISPATCHED enum means local assignment/in review, not delivery to an authority. Drafting a memo does not change status or contact anyone.

**Image triage is tentative. A photo cannot establish structural safety.** The system retains observations and uncertainty, always requires human review and never treats report count as measured damage or verified unique citizens. Model text is untrusted evidence and must be checked by a reviewer.

## API

| Method | Route | Purpose/access |
| --- | --- | --- |
| GET | `/api/health` | Service/database check |
| GET | `/api/capabilities` | Limits, categories and available analysis |
| POST | `/api/reports` | Multipart report; returns private receipt |
| GET | `/api/reports/{id}` | Original report, current status and events; tracking or reviewer bearer token |
| GET | `/api/reports/{id}/photo` | Original photo; same bearer authorization |
| GET | `/api/tickets` | Individual complaint map/queue; public responses omit descriptions, contacts and photos |
| GET | `/api/tickets/{id}` | Individual complaint summary |
| GET | `/api/tickets/{id}/reports` | Reviewer-only individual evidence |
| PATCH | `/api/tickets/{id}/status` | Reviewer-only local status with required note |
| POST | `/api/tickets/{id}/dispatch` | Reviewer-only draft, no transmission or status change |
| GET | `/api/stats` | Counts by status, category and review priority |
| GET | `/api/complaints/{number}` | Lookup by complaint number; tracking or reviewer bearer token |
| GET | `/api/reports/{id}/similar` | Heuristic similarity counts/candidates; report authorization required |
| POST | `/api/reports/{id}/similar-confirmations` | Record possibly-related/different feedback; complaints stay separate |
| GET | `/api/reports/{id}/followups` | Resolution, reopen and similarity feedback history; report authorization required |
| POST | `/api/reports/{id}/resolution` | Reviewer-only resolution with required note and optional photo |
| GET | `/api/reports/{id}/resolutions/{resolution_id}/photo` | Repair photo; same report authorization |
| POST | `/api/reports/{id}/reopen-requests` | Request review of a resolved complaint; report authorization required |
| PATCH | `/api/reports/{id}/reopen-requests/{request_id}` | Reviewer-only approval/rejection with required note |
| GET | `/api/legacy-tickets` | Reviewer-only archived pre-numbering aggregate data |

Submission example (Bash line continuation; in PowerShell use `curl.exe` on one line):

```bash
curl http://127.0.0.1:8000/api/reports \
  -H "Idempotency-Key: <random-UUID-for-this-submission>" \
  -F "transcript_text=Broken bridge railing beside the public walkway" \
  -F "latitude=10.0625" -F "longitude=76.5312" \
  -F "location_consent=true" -F "location_source=manual" \
  -F "input_source=typed" -F "file=@photo.jpg"
```

Required: `transcript_text` (5-5000 characters), `latitude`, `longitude`, `location_consent=true`. Optional: `reporter_phone`, `category_override`, `input_source` (`typed`/`voice_transcript`), `location_source` (`manual`/`map`/`device`), `location_accuracy_m`, `analysis_consent`, `file`.

Use a cryptographically random `Idempotency-Key` (e.g. UUID4) per submission. An exact retry returns the same receipt without increasing counts; changed content returns 409. **This key is also the private tracking token when supplied: treat it as a secret, never log it or use predictable keys.** Without a key, the server generates a random tracking token. Tokens/keys are hashed in SQLite. The UI retains a key after a network error for an unchanged retry. When local recovery is enabled, saving the draft preserves its pending key and photo for explicit restoration after a refresh. Without a saved draft, refreshing during an uncertain request can lose the key; check your receipt before submitting again.

```bash
curl -H "Authorization: Bearer <tracking-token>" http://127.0.0.1:8000/api/reports/<report-id>
curl -X PATCH -H "Authorization: Bearer <reviewer-token>" \
  -H "Content-Type: application/json" \
  -d '{"status":"DISPATCHED","note":"Reviewed the submitted evidence; local inspection requested."}' \
  http://127.0.0.1:8000/api/tickets/<ticket-id>/status
```

## Optional local recovery

Enable the page's local recovery option only on a trusted device. It stores draft description, contact/location fields, selected photo, pending submission key and tracking receipts in that browser's IndexedDB. Records expire seven days after their last save and are removed when next read. Autosave runs while enabled; draft restoration is explicit, clears consent checkboxes and never submits a complaint by itself. Browser storage can fail or be cleared, so it is not a backup guarantee.

Export/import tracking receipts to keep access across sessions or devices. Exported JSON contains private tracking tokens: protect it like a password and do not commit it to Git. Importing receipts does not submit reports. Turning off recovery removes persistent records; the page's clear-local-data action also clears current-session receipts, but neither deletes server complaints.

## Similarity and follow-up

Similarity uses a small keyword list, category, age and distance. It can miss related issues or suggest unrelated ones; three or more other nearby matches show a reviewer-attention indicator. Counts are complaints, not unique people, and do not raise claimed damage severity. Authorized users can record "possibly related" or "different" feedback without merging records or changing status. Candidate summaries do not expose another reporter's private evidence.

Resolution evidence and reopen requests belong to one complaint. Reopening requests remain pending until a reviewer decides; they do not reopen the complaint automatically. A repair photo is optional, and neither a photo nor a status change proves structural safety.

## Optional image analysis

No paid service runs by default. Install `requirements-ai.txt`, configure a provider key and set `CIVIC_ENABLE_AI=1` only when you choose to enable external processing. Each photo submission must additionally set `analysis_consent=true`. Only photo bytes and the report description go to the model; phone/location fields are not explicitly sent (they can still be present in photo metadata or description). Provider pricing and data handling must be reviewed before real use. Live provider behavior has not been verified in this change.

| Environment variable | Default |
| --- | --- |
| `CIVIC_DB_PATH` | `civic_pulse.db` beside main.py |
| `CIVIC_EVIDENCE_DIR` | `data/evidence` beside main.py |
| `CIVIC_ADMIN_TOKEN` | unset; reviewer access denied |
| `CIVIC_ENABLE_AI` | off |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | unset |
| `GEMINI_MODEL` | `gemini-2.5-flash` (existing integration) |
| `CIVIC_CORS_ORIGINS` | empty; same-origin browser use |

Grouped demo seeding/reset is retired. `/api/demo/script` and `/api/demo/reset` return 410 even if `CIVIC_DEMO_MODE` is set. For experiments, submit individual complaints to a separate database and evidence directory.

Back up the database and evidence directory before upgrading. Startup migrates older durable reports to independent complaint numbers while retaining report IDs, tracking-token hashes, original evidence and clearly labeled previous shared history. Pre-numbering aggregate tickets are archived and excluded from the active queue; reviewers can inspect them through `/api/legacy-tickets`. Aggregate-only legacy data is not presented as reliable individual evidence. The old `/api/grievances/submit` and `/api/tickets/{id}/resolve` mutation routes return 410 with migration guidance. Public `/static/uploads/...` access is disabled.

## Checks

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check main.py reporting.py followup.py tests
python -m unittest discover -s tests -v
node scripts/check_frontend.mjs
node --test tests/recovery.test.cjs
```

GitHub Actions runs these checks on Windows and Ubuntu after push/PR. No deployment step is included.

## Practical limits

This remains a local prototype: one shared reviewer token, synchronous bounded model requests, local SQLite/files and no rate limiting, retention workflow or user accounts. It is not ready for a public service. Before public operation, decide identity/access, abuse controls, evidence retention and backups (database plus evidence directory). Photo bytes may contain private EXIF metadata; only the holder of a report token or reviewer token can retrieve them. File cleanup occurs on handled database failures; an abrupt process crash between writing a photo and committing SQLite may leave an orphan file. The dashboard still uses its existing external CDN assets/map tiles. Frontend redesign is deferred.

See [REVIEW_NOTES.md](REVIEW_NOTES.md) for the implementation tour, validation results and remaining review decisions.
