# Barcode Attendance Tracking System — Software-Only Edition

This is a working build of the system described in *"Automated Student
Attendance Tracking System Using Barcode Scanning Technology"*, with every
piece of dedicated hardware removed and replaced with software.

## What changed from the original hardware design

| Document's hardware component | Software-only replacement here |
|---|---|
| Dedicated barcode scanner (illuminator + sensor/converter + decoder) | The device's existing **camera** (laptop webcam / phone camera), decoded in the browser using the ZXing JavaScript library |
| Barcode printed on a physical plastic ID card | Barcode **generated and rendered on-screen** (JsBarcode, Code128), printable to PDF/paper from any browser — no card printer needed |
| A PC wired to the scanner at a fixed attendance station | **Any device with a browser** — laptop, tablet, or phone — can be an attendance station |
| XAMPP / local PHP-MySQL server | **Self-contained Python/Flask app** with a built-in SQLite database file — no separate database server to install |

Every functional requirement from the document is still implemented:
student registration with a unique barcode, real-time attendance capture
with duplicate/invalid-barcode detection, role-based dashboards for admins,
lecturers, and students, manual attendance adjustment with a mandatory
reason and audit trail, and PDF/Excel report export with filters.

A camera isn't required either — typing the barcode/registration number
into the manual-entry field works identically, so the system runs on
software alone even with no camera present.

## Three roles, three dashboards

- **Admin** — full system access: students, courses, users, reports,
  audit trail, settings, backups.
- **Lecturer** — their own courses, session scheduling, attendance
  capture, and reports scoped to their courses.
- **Student** — a self-service dashboard showing their own attendance
  history and per-course percentage, with a flag if they've dropped
  below the configured minimum. Students log in with their registration
  number as both username and password on first login (from *Profile*,
  everyone — admin, lecturer, or student — can change their own
  password).

## Project layout

```
attendance_system/
├── app.py              Flask application: routes, auth, RBAC
├── database.py         SQLite connection + init helper
├── reports.py          PDF (reportlab) and Excel (openpyxl) report builders
├── schema.sql           Database schema
├── requirements.txt
├── templates/           Jinja2 HTML templates
└── static/
    ├── css/style.css    Design system
    └── js/
        ├── scanner.js   Camera + manual barcode capture (the "software scanner")
        └── barcode.js   Renders each student's barcode client-side
```

## Setup

```bash
cd attendance_system
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Open **http://localhost:5000**. On first run the database is created
automatically and a default administrator account is seeded:

```
username: admin
password: admin123
```

**Change this password immediately** (create a new admin user from
*User Accounts* and disable the default one), especially before using this
outside a local test environment.

### Windows setup note (photo-upload scanning)

The "Scan with Upload" feature decodes barcodes server-side using
`pyzbar`. On Windows, `pyzbar` needs the **Visual C++ 2013 Redistributable
(x64)** installed on the machine, or you'll hit an error like:

```
Couldn't find module '...\pyzbar\libzbar-64.dll' (or one of its dependencies).
```

Fix: download and run `vcredist_x64.exe` from Microsoft's **Visual Studio
2013 (VC++ 12.0)** redistributable page, then restart your terminal before
running the app again. This is a one-time install per machine — do it on
every Windows machine (lab PCs, lecturer laptops, etc.) this app gets
deployed to, not just the one you're developing on. Live camera scanning
doesn't need this — only the photo-upload path does.

## Using the system

1. **Sign in as admin.**
2. **Users → Create account** to add lecturer logins.
3. **Students → Register student** — enter a registration number and name;
   a barcode is generated automatically. Open the student's page to view
   or print their barcode.
4. **Courses & Sessions → Add a course**, assign a lecturer, then open the
   course and **schedule a session** (date + time).
5. **Take attendance** from a session: click **Start camera** and show a
   student's barcode to the camera, or type the barcode into the manual
   field and press Enter. Both paths validate the barcode, block duplicate
   scans for that session, and timestamp the record.
6. **Reports** → filter by course/date range → download PDF or Excel.

Lecturers see only their own assigned courses and can take attendance,
manually adjust a record (with a required reason, logged to the audit
trail), and generate reports scoped to their courses.

## Admin tools

- **Audit Trail** (`/audit`) — every login, manual adjustment, student
  registration, password change, settings change, and backup download,
  filterable by action.
- **Settings** (`/settings`) — institution name, the "late" threshold in
  minutes (used live by attendance scanning), and the low-attendance
  warning percentage (used live by each student's dashboard).
- **Backup** (`/backup`) — downloads a timestamped copy of the live
  SQLite database file. To restore: stop the app, replace
  `instance/attendance.db` with the downloaded file (renamed back to
  `attendance.db`), restart.

## Notes on the database

SQLite is used so the whole system runs with zero external services. The
schema (`schema.sql`) is plain SQL and maps directly onto MySQL or
PostgreSQL if you outgrow SQLite — the document's original design assumed
MySQL, and the table shapes here (`students`, `attendance_records`, etc.)
mirror that design.

## Security notes

- Passwords are hashed with Werkzeug's `generate_password_hash`
  (PBKDF2), never stored in plain text.
- Access is gated by session-based role checks (`admin` / `lecturer`).
- Manual attendance adjustments require a reason and are written to
  `audit_log`.
- For real deployment: put this behind HTTPS, set `ATTENDANCE_SECRET_KEY`
  to a strong random value via environment variable, and disable
  `debug=True` in `app.py`.
