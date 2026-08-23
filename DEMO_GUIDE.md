# Demo Guide — Barcode Attendance Tracking System

A one-page cheat sheet for presenting this to supervisors. Read it once
before you present, then keep it open on a second screen.

## 1. Before you present (do this once, the night before)

```bash
pip install -r requirements.txt
python app.py                 # first run creates the DB + default admin
```
Stop the app (Ctrl+C), then load it up with realistic demo data:
```bash
python seed_demo_data.py
```
This creates ~35 students, 2 lecturers, 3 courses, and 5 weeks of
attendance history — so your dashboard, trend chart, and at-risk list
are full instead of empty when you demo.

Restart the app and confirm `http://localhost:5000` loads correctly.
If you'll demo the camera scanner on a phone, start `ngrok http 5000`
in a second terminal now and confirm the `https://...ngrok-free.dev`
URL works before the actual presentation — don't discover a network
issue live.

## 2. Login credentials (from the seed data)

| Role     | Username / Reg No.      | Password      |
|----------|--------------------------|---------------|
| Admin    | `admin`                  | `admin123`    |
| Lecturer | `j.moyo`                 | `lecturer123` |
| Lecturer | `t.ncube`                 | `lecturer123` |
| Student  | any reg number from `/students` (e.g. `R261001`) | same as reg number |

## 3. Suggested walkthrough order (~8–10 min)

1. **Login as admin.** Point out the dashboard immediately: live stat
   cards, the 7-day trend chart, and the **at-risk students** panel —
   this shows the system doing analysis, not just data entry.
2. **Open a course → schedule/open a session → Take Attendance.**
   Show the camera scanning a barcode (or QR) live. Point out the
   **live countdown chip** ("On time until 10:14...") and the big
   confirmation card that flashes the student's name — mention this is
   a deliberate anti-proxy measure, not just cosmetic.
3. **Type a barcode manually** to show the no-camera fallback still works.
4. **Try scanning the same student twice** — show the duplicate-scan
   rejection. This demonstrates the data-integrity rule directly.
5. **Log out, log in as a student.** Show their personal dashboard:
   per-course attendance %, the warning banner if they're under the
   threshold, and their **QR/barcode card** — explain a student without
   a physical ID can still be scanned from their phone screen.
6. **Back as admin: Students → Print ID cards (PDF).** Show the batch
   PDF with real scannable Code128 barcodes — a nice physical artifact
   to hand an evaluator.
7. **Reports.** Export a PDF and/or Excel report with a filter applied
   (by course or date range).
8. **Settings page.** Show the configurable late-threshold and
   low-attendance-% — proves the "late" and "at-risk" logic isn't
   hardcoded.
9. **Backup page.** Mention the one-click DB backup/download exists for
   data-safety/continuity — supervisors like hearing this was considered.

Optional: open `architecture_diagram.png` on a slide before the live
demo — a 20-second "here's how the pieces fit together" beats diving
straight into the UI cold.

## 4. Questions supervisors commonly ask (be ready)

- **"What stops Student A scanning for Student B?"** → The confirmation
  card shows the matched name/reg number immediately and loudly on
  every scan, so a mismatch is visually obvious to whoever is running
  the session; duplicate scans per session are also blocked at the
  database level (UNIQUE constraint on student+session).
- **"What if there's no internet / no camera?"** → Manual barcode/ID
  entry is always available as a fallback (see `manual-scan-form` in
  `take_attendance.html`), and the whole app runs locally over SQLite —
  no external service is required except when using the camera over a
  network IP (which needs HTTPS, e.g. via ngrok, or `localhost`).
- **"How is attendance data protected?"** → Passwords are hashed
  (`werkzeug.security`), role-based access control gates every route,
  and manual adjustments require a reason and are written to the audit
  log (`audit_log` table).
- **"Can this scale to MySQL/Postgres later?"** → Yes — the SQL used is
  standard and the schema (`schema.sql`) has no SQLite-only features
  beyond the file-based connection itself.

## 5. Known limitations (say these out loud — it builds trust)

Naming limitations yourself, before an evaluator finds them, generally reads
better than pretending the system is flawless.

- **No biometric verification.** The camera reads a barcode/QR, not a face —
  someone holding a valid student's phone/card can be scanned as that
  student. This system relies on a supervised session (a lecturer running
  the scan), same as a paper sign-in sheet or a swipe-card system.
- **Camera requires HTTPS or localhost.** A browser security rule, not a
  bug — covered by using `localhost` on the same machine, or a tunnel like
  ngrok / a real TLS certificate in production.
- **Single-server SQLite.** Fine for one classroom/department; a
  multi-campus rollout would want to migrate to MySQL/Postgres (the schema
  is portable, see `schema.sql`).
- **No email/SMS notifications currently.** The at-risk list is
  dashboard-only, not auto-emailed to students or lecturers.
- **No offline mode.** Needs the Flask server reachable on the network the
  device is using.

## 6. If something breaks live

- Camera won't open → fall back to manual entry, keep talking, fix it
  after. Don't debug live in front of supervisors.
- Forgot a password → admin can reset any user's password isn't built
  in yet; instead log in fresh as `admin` (never blocked) and continue
  the walkthrough from there.
