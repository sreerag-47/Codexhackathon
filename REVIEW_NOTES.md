# Reporting pipeline review notes

The review branch is `improve/reporting-pipeline`. This change prepares a local civic-reporting prototype for review; it does not deploy the app, contact authorities or enable external AI. The existing visual layout is retained.

## Implemented behavior

- Every accepted report has an independent complaint number, private tracking token, original evidence and event history. Idempotent retries return the same receipt; concurrent distinct submissions remain separate.
- Input validation covers consent, finite coordinates, input lengths, actual image decoding, 8 MiB photo limits and 20-megapixel decoded images. The multipart body is bounded before parsing. Original photo bytes and a SHA-256 digest are retained outside public static hosting.
- Public complaint summaries omit descriptions, contacts and photos. Tracking-token or reviewer authorization is required for private report/photo/follow-up access. The role selector alone grants no reviewer access.
- Optional image analysis is disabled by default and requires per-report consent. Explicit not-requested, unavailable, failed and completed states distinguish real image assessment from text-only fallback. All triage remains tentative and requires human review.
- Similarity counts same-category keyword matches from the previous 30 days and suggests candidates within 150 metres. Feedback records possibly-related/different opinions without merging complaints. Counts do not establish distinct people, physical severity or a verified common defect.
- Reviewers can resolve a complaint with a required note and optional repair photo. Reporters can request reopening; reviewers approve or reject with a note. Resolution, reopen decisions and status updates remain specific to that complaint.
- Optional browser recovery stores drafts, photos, pending submission keys and receipts locally for seven days after their last save. Drafts are restored explicitly and require renewed consent. Receipt export/import and complaint-number lookup support later tracking. No offline background submission or cloud synchronization is included.
- Startup preserves old durable report IDs, evidence and tracking tokens while allocating independent complaint numbers. Previous shared history is labeled as shared. Pre-numbering aggregates are archived outside the active queue and remain reviewer-accessible. Grouped demo seeding/reset is retired.

## Code tour

1. `main.py`: application lifecycle, ticket persistence/read interfaces, public summaries, statistics and optional AI client configuration.
2. `reporting.py`: input/image validation, bounded requests, complaint numbering, legacy migration, idempotency, evidence authorization, similarity suggestions and status history.
3. `followup.py`: resolution evidence, reopen requests/decisions and similarity feedback.
4. `static/index.html`: existing form/map/reviewer UI connected to the reporting API, explicit consent and private receipts.
5. `static/recovery.js`: queued local storage, expiry and receipt validation. `static/complaints.js`: recovery controls and individual complaint follow-up UI.
6. `tests/`: 22 backend tests and five recovery-state tests. `scripts/check_frontend.mjs` parses the inline and external application scripts.
7. `.github/workflows/checks.yml`: Python 3.12/Node 22 checks on Windows and Ubuntu for pushes and pull requests. No deployment job is included.

Pillow is the added runtime image-validation dependency. The pre-existing Gemini integration is optional through `requirements-ai.txt`. Ruff and httpx are development dependencies. No extra hosted service, geocoding provider, custom model, mobile APK or dashboard framework was added.

## Validation

Rechecked on Windows on 2026-11-10 with Python 3.13.14 and Node 22.19.0:

- All 22 backend tests passed. Coverage includes authorization, validation/upload limits, original photo association, retries/conflicts, concurrent independent complaints, migration, private complaint-number lookup, similarity/feedback, resolution evidence, concurrent reopen requests/decisions, model fallbacks with mocks, and failure cleanup.
- All five recovery-state tests passed: photo/key retention, seven-day expiry, invalid import rejection, clear-vs-autosave ordering and recovery after storage failure.
- Ruff 0.17.4 and frontend JavaScript syntax checks passed. Ruff was available in the ignored local tooling directory rather than the default Python environment.
- Whitespace checks passed. A focused scan of changed/new text files found no high-confidence credential patterns; generated artifacts, test databases, caches, uploaded evidence and local tool installations remain ignored.
- No Python server process was found during the pre-push process check. This validation did not start a persistent app server or call a live AI provider.

Earlier local browser checks verified a typed report, private evidence access, reviewer assignment, original sample-photo viewing, and text-draft save/reload/explicit restore followed by submission as `CP-000001`. The browser photo-recovery check was interrupted by a computer disconnect and remains unverified. Backend/recovery-state tests do not replace that browser check.

The current shared Python environment has unrelated OpenTelemetry package-version conflicts reported by `pip check`; it was not modified. A fresh virtual environment install and Linux/Python 3.12 execution were not performed locally. The workflow checks those supported CI configurations after publication; consult the exact commit's Actions run rather than treating local results as a CI pass.

Also not verified: real microphone input, device-location accuracy, live image-provider quality/latency, mobile/accessibility coverage, interrupted/repeated browser follow-up flows and production traffic.

## Remaining review decisions

- This remains a prototype with one shared reviewer token and no user accounts, rate limiting or production retention policy. Decide identity/access, abuse controls and backups before public operation.
- Protect receipt exports and local recovery data on shared devices. Original images can contain private EXIF metadata. Public summaries still expose issue locations; external CDN/map dependencies remain.
- Back up both database and evidence files before migration. Previous aggregate-only data cannot be reconstructed into reliable individual evidence. An abrupt process crash between writing a file and committing SQLite can leave an orphan file.
- Validate the browser photo restore and follow-up controls before relying on recovery in a field pilot. Similarity heuristics need human review and do not authorize merging complaints.
- Review provider cost, consent and data handling before enabling AI. No external authority submission, WhatsApp/SMS integration or deployment is included.
