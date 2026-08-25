"""
Barcode Automated Attendance Tracking System — SOFTWARE-ONLY EDITION
======================================================================
"""
import io
import os
from functools import wraps
from datetime import datetime, date

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, jsonify, send_file, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import (
    get_db, init_db, now_iso, DB_PATH,
    get_setting, get_all_settings, set_setting,
    ensure_messaging_tables, ensure_reset_requests_table,
)
from reports import build_pdf_report, build_excel_report, build_id_cards_pdf

app = Flask(__name__)
app.secret_key = os.environ.get("ATTENDANCE_SECRET_KEY", "dev-secret-change-me")

try:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _migration_conn = get_db()
    ensure_messaging_tables(_migration_conn)
    ensure_reset_requests_table(_migration_conn)
    _migration_conn.close()
except Exception as _migration_error:
    print(f"[startup migration] skipped, will retry on first use: {_migration_error}")


def _ensure_reset_table_ready():
    try:
        _conn = get_db()
        ensure_reset_requests_table(_conn)
        _conn.close()
    except Exception as _e:
        print(f"[lazy migration] {_e}")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "role" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "role" not in session:
                return redirect(url_for("login", next=request.path))
            if session.get("role") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def log_action(action, detail=""):
    db = get_db()
    db.execute(
        "INSERT INTO audit_log (actor_role, actor_id, actor_name, action, detail) VALUES (?,?,?,?,?)",
        (session.get("role"), session.get("user_id") or session.get("student_id"),
         session.get("full_name"), action, detail),
    )
    db.commit()
    db.close()


@app.context_processor
def inject_user():
    return {
        "current_user": {
            "id": session.get("user_id") or session.get("student_id"),
            "name": session.get("full_name"),
            "role": session.get("role"),
        },
        "institution_name": get_setting("institution_name", "Attendance Ledger"),
    }


@app.context_processor
def inject_pending_reset_count():
    if session.get("role") != "admin":
        return {}
    db = get_db()
    count = db.execute(
        "SELECT COUNT(*) AS c FROM password_reset_requests WHERE status='pending'"
    ).fetchone()["c"]
    db.close()
    return {"pending_reset_count": count}


@app.route("/login", methods=["GET", "POST"])
def login():
    role_hint = request.args.get("role", "")
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            db.close()
            session.clear()
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['full_name']}.", "success")
            log_action("login", username)
            dest = request.args.get("next") or (
                url_for("admin_dashboard") if user["role"] == "admin" else url_for("lecturer_dashboard")
            )
            return redirect(dest)

        student = db.execute(
            "SELECT * FROM students WHERE reg_number = ? AND status = 'active'", (username,)
        ).fetchone()
        db.close()
        if student and student["password_hash"] and check_password_hash(student["password_hash"], password):
            session.clear()
            session["student_id"] = student["id"]
            session["full_name"] = student["full_name"]
            session["role"] = "student"
            flash(f"Welcome back, {student['full_name']}.", "success")
            log_action("login", username)
            return redirect(request.args.get("next") or url_for("student_dashboard"))

        flash("Invalid username or password.", "error")
        role_hint = request.form.get("role_hint", role_hint)
    return render_template("login.html", role_hint=role_hint)


@app.route("/admin-recovery", methods=["GET", "POST"])
def admin_recovery():
    recovery_key_set = os.environ.get("ADMIN_RECOVERY_KEY")
    if request.method == "POST":
        if not recovery_key_set:
            flash("Recovery is not configured on this server. Set ADMIN_RECOVERY_KEY in your hosting environment variables first.", "error")
            return render_template("admin_recovery.html")
        entered_key = request.form.get("recovery_key", "")
        new_password = request.form.get("new_password", "")
        username = request.form.get("username", "admin").strip()
        if entered_key != recovery_key_set:
            flash("Incorrect recovery key.", "error")
            return render_template("admin_recovery.html")
        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
            return render_template("admin_recovery.html")
        db = get_db()
        admin_row = db.execute(
            "SELECT id FROM users WHERE username=? AND role='admin'", (username,)
        ).fetchone()
        if not admin_row:
            db.close()
            flash(f"No admin account found with username '{username}'.", "error")
            return render_template("admin_recovery.html")
        db.execute(
            "UPDATE users SET password_hash=?, is_active=1 WHERE id=?",
            (generate_password_hash(new_password), admin_row["id"]),
        )
        db.commit()
        db.close()
        log_action("password_reset", f"admin-recovery: password reset for admin username={username}")
        flash(f"Password for '{username}' has been reset. You can now log in.", "success")
        return redirect(url_for("login"))
    return render_template("admin_recovery.html")


@app.route("/request-password-reset", methods=["GET", "POST"])
def request_password_reset():
    role_hint = request.args.get("role", "").strip()
    if role_hint not in ("student", "lecturer"):
        role_hint = request.form.get("role_hint", "").strip()
    _ensure_reset_table_ready()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        message = request.form.get("message", "").strip()
        role_hint = request.form.get("role_hint", role_hint).strip()
        if role_hint not in ("student", "lecturer"):
            flash("Please choose whether you're a student or a lecturer.", "error")
            return render_template("request_password_reset.html", role_hint=role_hint)
        if not username:
            flash("Please enter your username / registration number.", "error")
            return render_template("request_password_reset.html", role_hint=role_hint)

        db = get_db()
        requester_id = None
        full_name = None
        if role_hint == "student":
            row = db.execute(
                "SELECT id, full_name FROM students WHERE LOWER(TRIM(reg_number)) = LOWER(TRIM(?))", (username,)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT id, full_name FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) AND role = 'lecturer'", (username,)
            ).fetchone()
        if row:
            requester_id = row["id"]
            full_name = row["full_name"]
        db.execute(
            """INSERT INTO password_reset_requests
               (requester_type, requester_id, username, full_name, message)
               VALUES (?, ?, ?, ?, ?)""",
            (role_hint, requester_id, username, full_name, message or None),
        )
        db.commit()
        db.close()
        flash(
            "Your request has been sent to the administrator. You'll be able to "
            "log in once your password is reset — check back or wait to be contacted.",
            "success",
        )
        return redirect(url_for("login", role=role_hint))
    return render_template("request_password_reset.html", role_hint=role_hint)


@app.route("/check-reset-status", methods=["GET", "POST"])
def check_reset_status():
    role_hint = request.args.get("role", "").strip()
    result = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        role_hint = request.form.get("role_hint", role_hint).strip()
        _ensure_reset_table_ready()
        db = get_db()
        row = db.execute(
            """SELECT * FROM password_reset_requests
               WHERE requester_type = ? AND LOWER(TRIM(username)) = LOWER(TRIM(?))
               ORDER BY created_at DESC LIMIT 1""",
            (role_hint, username),
        ).fetchone()
        db.close()
        if not row:
            result = {"found": False}
        elif row["status"] == "resolved":
            result = {"found": True, "status": "resolved", "resolved_at": row["resolved_at"]}
        else:
            result = {"found": True, "status": "pending", "created_at": row["created_at"]}
    return render_template("check_reset_status.html", role_hint=role_hint, result=result)


@app.route("/logout")
def logout():
    log_action("logout", session.get("full_name", ""))
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
def index():
    if "role" not in session:
        return redirect(url_for("select_role"))
    if session["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    if session["role"] == "student":
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("lecturer_dashboard"))


@app.route("/select-role")
def select_role():
    if "role" in session:
        return redirect(url_for("index"))
    return render_template("select_role.html")


@app.route("/admin")
@role_required("admin")
def admin_dashboard():
    db = get_db()
    total_students = db.execute("SELECT COUNT(*) c FROM students WHERE status='active'").fetchone()["c"]
    total_courses = db.execute("SELECT COUNT(*) c FROM courses").fetchone()["c"]
    today = date.today().isoformat()
    today_present = db.execute(
        """SELECT COUNT(DISTINCT student_id) c FROM attendance_records ar
           JOIN sessions s ON s.id = ar.session_id
           WHERE s.session_date = ? AND ar.status IN ('present','late')""",
        (today,),
    ).fetchone()["c"]
    recent = db.execute(
        """SELECT ar.timestamp, st.full_name, st.reg_number, c.name AS course_name, ar.status, ar.method
           FROM attendance_records ar
           JOIN students st ON st.id = ar.student_id
           JOIN sessions s ON s.id = ar.session_id
           JOIN courses c ON c.id = s.course_id
           ORDER BY ar.timestamp DESC LIMIT 10"""
    ).fetchall()
    trend = db.execute(
        """SELECT s.session_date AS day, COUNT(*) AS cnt
           FROM attendance_records ar JOIN sessions s ON s.id = ar.session_id
           WHERE ar.status IN ('present','late')
           GROUP BY s.session_date ORDER BY s.session_date DESC LIMIT 7"""
    ).fetchall()
    low_threshold = float(get_setting("low_attendance_percent", "75"))
    at_risk = db.execute(
        """SELECT st.id, st.full_name, st.reg_number,
                  COUNT(DISTINCT s.id) AS total_sessions,
                  COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.session_id END) AS attended,
                  ROUND(100.0 * COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.session_id END)
                        / NULLIF(COUNT(DISTINCT s.id), 0), 1) AS pct
           FROM students st
           JOIN enrollments e ON e.student_id = st.id AND e.status = 'enrolled'
           JOIN courses c ON c.id = e.course_id
           JOIN sessions s ON s.course_id = c.id
           LEFT JOIN attendance_records ar ON ar.student_id = st.id AND ar.session_id = s.id
           WHERE st.status = 'active'
           GROUP BY st.id
           HAVING total_sessions >= 3 AND pct < ?
           ORDER BY pct ASC
           LIMIT 10""",
        (low_threshold,),
    ).fetchall()
    db.close()
    return render_template(
        "admin_dashboard.html",
        total_students=total_students,
        total_courses=total_courses,
        today_present=today_present,
        recent=recent,
        trend=[dict(row) for row in reversed(trend)],
        at_risk=at_risk,
        low_threshold=low_threshold,
    )


@app.route("/lecturer")
@role_required("lecturer", "admin")
def lecturer_dashboard():
    db = get_db()
    lecturer_id = session["user_id"]
    courses = db.execute(
        "SELECT * FROM courses WHERE lecturer_id = ? ORDER BY name", (lecturer_id,)
    ).fetchall()
    db.close()
    return render_template("lecturer_dashboard.html", courses=courses)


def _unread_message_count():
    role = session.get("role")
    if role == "lecturer":
        rid = session.get("user_id")
        rtype = "lecturer"
    elif role == "student":
        rid = session.get("student_id")
        rtype = "student"
    else:
        return 0
    db = get_db()
    n = db.execute(
        "SELECT COUNT(*) c FROM message_recipients WHERE recipient_type=? AND recipient_id=? AND read_at IS NULL",
        (rtype, rid),
    ).fetchone()["c"]
    db.close()
    return n


@app.context_processor
def inject_unread_count():
    return {"unread_message_count": _unread_message_count()}


@app.route("/messages/compose", methods=["GET", "POST"])
@role_required("admin", "lecturer")
def messages_compose():
    db = get_db()
    role = session["role"]
    sender_id = session["user_id"]
    sender_name = session["full_name"]

    if role == "admin":
        lecturers = db.execute(
            "SELECT id, full_name FROM users WHERE role='lecturer' AND is_active=1 ORDER BY full_name"
        ).fetchall()
        students = db.execute(
            "SELECT id, full_name, reg_number FROM students WHERE status='active' ORDER BY full_name"
        ).fetchall()
        courses = []
    else:
        lecturers = []
        courses = db.execute(
            "SELECT id, code, name FROM courses WHERE lecturer_id=? ORDER BY name", (sender_id,)
        ).fetchall()
        students = db.execute(
            """SELECT DISTINCT st.id, st.full_name, st.reg_number
               FROM students st
               JOIN enrollments e ON e.student_id = st.id AND e.status='enrolled'
               JOIN courses c ON c.id = e.course_id
               WHERE c.lecturer_id = ? AND st.status='active'
               ORDER BY st.full_name""",
            (sender_id,),
        ).fetchall()

    if request.method == "POST":
        target_type = request.form.get("target_type", "")
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()

        if not subject or not body:
            flash("Subject and message body are required.", "error")
            db.close()
            return redirect(url_for("messages_compose"))

        recipients = []
        target_desc = ""

        if role == "admin":
            if target_type == "lecturer":
                lid = request.form.get("lecturer_id", type=int)
                row = db.execute("SELECT full_name FROM users WHERE id=? AND role='lecturer'", (lid,)).fetchone()
                if not row:
                    abort(400)
                recipients = [("lecturer", lid)]
                target_desc = f"Lecturer: {row['full_name']}"
            elif target_type == "student":
                sid = request.form.get("student_id", type=int)
                row = db.execute("SELECT full_name, reg_number FROM students WHERE id=?", (sid,)).fetchone()
                if not row:
                    abort(400)
                recipients = [("student", sid)]
                target_desc = f"Student: {row['full_name']} ({row['reg_number']})"
            elif target_type == "all_lecturers":
                rows = db.execute("SELECT id FROM users WHERE role='lecturer' AND is_active=1").fetchall()
                recipients = [("lecturer", r["id"]) for r in rows]
                target_desc = "All lecturers"
            elif target_type == "all_students":
                rows = db.execute("SELECT id FROM students WHERE status='active'").fetchall()
                recipients = [("student", r["id"]) for r in rows]
                target_desc = "All students"
            else:
                abort(400)
        else:
            if target_type == "student":
                sid = request.form.get("student_id", type=int)
                owns = db.execute(
                    """SELECT st.full_name, st.reg_number FROM students st
                       JOIN enrollments e ON e.student_id = st.id AND e.status='enrolled'
                       JOIN courses c ON c.id = e.course_id
                       WHERE st.id=? AND c.lecturer_id=?""",
                    (sid, sender_id),
                ).fetchone()
                if not owns:
                    abort(403)
                recipients = [("student", sid)]
                target_desc = f"Student: {owns['full_name']} ({owns['reg_number']})"
            elif target_type == "course_students":
                cid = request.form.get("course_id", type=int)
                course = db.execute(
                    "SELECT code, name FROM courses WHERE id=? AND lecturer_id=?", (cid, sender_id)
                ).fetchone()
                if not course:
                    abort(403)
                rows = db.execute(
                    """SELECT student_id FROM enrollments
                       WHERE course_id=? AND status='enrolled'""",
                    (cid,),
                ).fetchall()
                recipients = [("student", r["student_id"]) for r in rows]
                target_desc = f"{course['code']} students"
            else:
                abort(400)

        if not recipients:
            flash("No recipients matched — nothing was sent.", "error")
            db.close()
            return redirect(url_for("messages_compose"))

        cur = db.execute(
            """INSERT INTO messages (sender_role, sender_id, sender_name, target_desc, subject, body)
               VALUES (?,?,?,?,?,?)""",
            (role, sender_id, sender_name, target_desc, subject, body),
        )
        message_id = cur.lastrowid
        db.executemany(
            "INSERT INTO message_recipients (message_id, recipient_type, recipient_id) VALUES (?,?,?)",
            [(message_id, rtype, rid) for rtype, rid in recipients],
        )
        db.commit()
        log_action("message_sent", f"target={target_desc} recipients={len(recipients)}")
        db.close()
        flash(f"Message sent to {target_desc.lower()} ({len(recipients)} recipient(s)).", "success")
        return redirect(url_for("messages_sent"))

    db.close()
    return render_template(
        "messages_compose.html", role=role, lecturers=lecturers, students=students, courses=courses
    )


@app.route("/messages/sent")
@role_required("admin", "lecturer")
def messages_sent():
    db = get_db()
    rows = db.execute(
        """SELECT m.*, COUNT(mr.id) AS recipient_count,
                  SUM(CASE WHEN mr.read_at IS NOT NULL THEN 1 ELSE 0 END) AS read_count
           FROM messages m LEFT JOIN message_recipients mr ON mr.message_id = m.id
           WHERE m.sender_role=? AND m.sender_id=?
           GROUP BY m.id ORDER BY m.created_at DESC""",
        (session["role"], session["user_id"]),
    ).fetchall()
    db.close()
    return render_template("messages_sent.html", messages=rows)


@app.route("/messages/inbox")
@role_required("lecturer", "student")
def messages_inbox():
    db = get_db()
    if session["role"] == "lecturer":
        rid, rtype = session["user_id"], "lecturer"
    else:
        rid, rtype = session["student_id"], "student"
    rows = db.execute(
        """SELECT m.id, m.sender_name, m.sender_role, m.subject, m.body, m.created_at,
                  mr.id AS recipient_row_id, mr.read_at
           FROM message_recipients mr JOIN messages m ON m.id = mr.message_id
           WHERE mr.recipient_type=? AND mr.recipient_id=?
           ORDER BY m.created_at DESC""",
        (rtype, rid),
    ).fetchall()
    db.close()
    return render_template("messages_inbox.html", messages=rows)


@app.route("/messages/read/<int:recipient_row_id>", methods=["POST"])
@role_required("lecturer", "student")
def messages_mark_read(recipient_row_id):
    if session["role"] == "lecturer":
        rid, rtype = session["user_id"], "lecturer"
    else:
        rid, rtype = session["student_id"], "student"
    db = get_db()
    row = db.execute(
        "SELECT id FROM message_recipients WHERE id=? AND recipient_type=? AND recipient_id=?",
        (recipient_row_id, rtype, rid),
    ).fetchone()
    if not row:
        db.close()
        abort(404)
    db.execute(
        "UPDATE message_recipients SET read_at=datetime('now') WHERE id=? AND read_at IS NULL",
        (recipient_row_id,),
    )
    db.commit()
    db.close()
    return redirect(url_for("messages_inbox"))


@app.route("/students")
@role_required("admin")
def students_list():
    db = get_db()
    q = request.args.get("q", "").strip()
    if q:
        rows = db.execute(
            """SELECT * FROM students
               WHERE reg_number LIKE ? OR full_name LIKE ? OR department LIKE ?
               ORDER BY full_name""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM students ORDER BY full_name").fetchall()
    db.close()
    return render_template("students.html", students=rows, q=q)


@app.route("/students/id-cards")
@role_required("admin")
def students_id_cards():
    db = get_db()
    q = request.args.get("q", "").strip()
    if q:
        rows = db.execute(
            """SELECT * FROM students
               WHERE status='active' AND (reg_number LIKE ? OR full_name LIKE ? OR department LIKE ?)
               ORDER BY full_name""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM students WHERE status='active' ORDER BY full_name").fetchall()
    institution_name = get_setting("institution_name", "Institution")
    db.close()
    if not rows:
        flash("No active students to generate cards for.", "error")
        return redirect(url_for("students_list"))
    pdf_buf = build_id_cards_pdf([dict(r) for r in rows], institution_name)
    log_action("id_cards_generated", f"count={len(rows)}")
    return send_file(
        pdf_buf, as_attachment=True,
        download_name="student_id_cards.pdf",
        mimetype="application/pdf",
    )


def _generate_barcode_value(reg_number: str) -> str:
    return f"STU-{reg_number.strip().upper()}"


def _generate_reg_number():
    """
    Auto-generates the next unique registration number, e.g. STU2026-0001,
    STU2026-0002, ... A persistent counter (stored in the settings table)
    guarantees numbers never repeat even if a student is later deleted.
    """
    year = date.today().year
    seq_key = f"next_student_seq_{year}"
    seq = int(get_setting(seq_key, "1"))
    reg_number = f"STU{year}-{seq:04d}"
    set_setting(seq_key, str(seq + 1))
    return reg_number


@app.route("/students/bulk-import", methods=["GET", "POST"])
@role_required("admin")
def students_bulk_import():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("Please choose a CSV file to upload.", "error")
            return redirect(url_for("students_bulk_import"))
        try:
            content = file.stream.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("Could not read that file — please save it as a plain CSV (UTF-8) and try again.", "error")
            return redirect(url_for("students_bulk_import"))

        import csv as _csv
        import io as _io
        reader = _csv.DictReader(_io.StringIO(content))
        fieldnames = [ (f or "").strip().lower() for f in (reader.fieldnames or []) ]
        if "reg_number" not in fieldnames or "full_name" not in fieldnames:
            flash("CSV must have at least 'reg_number' and 'full_name' columns.", "error")
            return redirect(url_for("students_bulk_import"))

        db = get_db()
        created, skipped = 0, []
        for i, raw_row in enumerate(reader, start=2):
            row = { (k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() }
            reg_number = row.get("reg_number", "")
            full_name = row.get("full_name", "")
            if not reg_number or not full_name:
                skipped.append(f"Row {i}: missing reg_number or full_name")
                continue
            email = row.get("email", "")
            department = row.get("department", "")
            barcode_value = _generate_barcode_value(reg_number)
            try:
                db.execute(
                    """INSERT INTO students (reg_number, full_name, email, department, barcode_value, password_hash)
                       VALUES (?,?,?,?,?,?)""",
                    (reg_number, full_name, email, department, barcode_value, generate_password_hash(reg_number)),
                )
                created += 1
            except Exception as e:
                skipped.append(f"Row {i} ({reg_number}): {e}")
        db.commit()
        db.close()
        log_action("students_bulk_import", f"created={created} skipped={len(skipped)}")

        if created:
            flash(f"Imported {created} student(s) successfully.", "success")
        if skipped:
            preview = "; ".join(skipped[:5])
            more = f" (+{len(skipped)-5} more)" if len(skipped) > 5 else ""
            flash(f"Skipped {len(skipped)} row(s): {preview}{more}", "error")
        return redirect(url_for("students_list"))

    return render_template("students_bulk_import.html")


@app.route("/students/bulk-import/template")
@role_required("admin")
def students_bulk_import_template():
    csv_content = "reg_number,full_name,email,department\nR2024001,Jane Doe,jane@example.com,Computer Science\n"
    return app.response_class(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=student_import_template.csv"},
    )


@app.route("/students/add", methods=["GET", "POST"])
@role_required("admin")
def student_add():
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form.get("email", "").strip()
        department = request.form.get("department", "").strip()
        if not full_name:
            flash("Full name is required.", "error")
            return render_template("student_add.html")

        db = get_db()
        for _attempt in range(3):
            reg_number = _generate_reg_number()
            barcode_value = _generate_barcode_value(reg_number)
            default_password_hash = generate_password_hash(reg_number.strip())
            try:
                db.execute(
                    """INSERT INTO students (reg_number, full_name, email, department, barcode_value, password_hash)
                       VALUES (?,?,?,?,?,?)""",
                    (reg_number, full_name, email, department, barcode_value, default_password_hash),
                )
                db.commit()
                flash(f"Student '{full_name}' registered as {reg_number}, with barcode {barcode_value}. "
                      f"Portal login: {reg_number} / {reg_number} (they should change this).", "success")
                log_action("student_add", reg_number)
                break
            except Exception as e:
                if _attempt == 2:
                    flash(f"Could not create student: {e}", "error")
        db.close()
        return redirect(url_for("students_list"))
    return render_template("student_add.html")


@app.route("/students/<int:student_id>")
@role_required("admin")
def student_detail(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not student:
        abort(404)
    history = db.execute(
        """SELECT ar.timestamp, ar.status, ar.method, c.name AS course_name
           FROM attendance_records ar
           JOIN sessions s ON s.id = ar.session_id
           JOIN courses c ON c.id = s.course_id
           WHERE ar.student_id=? ORDER BY ar.timestamp DESC LIMIT 20""",
        (student_id,),
    ).fetchall()
    courses = db.execute(
        """SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id
           WHERE e.student_id=? AND e.status='enrolled'""",
        (student_id,),
    ).fetchall()
    all_courses = db.execute("SELECT * FROM courses ORDER BY name").fetchall()
    db.close()
    return render_template(
        "student_detail.html", student=student, history=history,
        courses=courses, all_courses=all_courses,
    )


@app.route("/students/<int:student_id>/enroll", methods=["POST"])
@role_required("admin")
def student_enroll(student_id):
    course_id = request.form["course_id"]
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?,?)",
            (student_id, course_id),
        )
        db.commit()
        flash("Enrollment updated.", "success")
    finally:
        db.close()
    return redirect(url_for("student_detail", student_id=student_id))


@app.route("/students/<int:student_id>/toggle_status", methods=["POST"])
@role_required("admin")
def student_toggle_status(student_id):
    db = get_db()
    student = db.execute("SELECT status FROM students WHERE id=?", (student_id,)).fetchone()
    new_status = "inactive" if student["status"] == "active" else "active"
    db.execute("UPDATE students SET status=? WHERE id=?", (new_status, student_id))
    db.commit()
    db.close()
    flash(f"Student status set to {new_status}.", "success")
    return redirect(url_for("student_detail", student_id=student_id))


@app.route("/students/<int:student_id>/reset_password", methods=["POST"])
@role_required("admin")
def student_reset_password(student_id):
    db = get_db()
    student = db.execute("SELECT reg_number, full_name FROM students WHERE id=?", (student_id,)).fetchone()
    if not student:
        db.close()
        abort(404)
    db.execute(
        "UPDATE students SET password_hash=? WHERE id=?",
        (generate_password_hash(student["reg_number"]), student_id),
    )
    db.commit()
    db.close()
    log_action("password_reset", f"admin reset password for student={student['reg_number']}")
    flash(
        f"{student['full_name']}'s password has been reset to their registration "
        f"number ({student['reg_number']}). They should change it from Profile "
        f"after logging in.",
        "success",
    )
    return redirect(url_for("student_detail", student_id=student_id))


@app.route("/student")
@role_required("student")
def student_dashboard():
    db = get_db()
    student_id = session["student_id"]
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not student:
        abort(404)

    low_attendance_threshold = float(get_setting("low_attendance_percent", "75"))

    per_course = db.execute(
        """SELECT c.id, c.code, c.name,
                  COUNT(DISTINCT s.id) AS sessions_held,
                  COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.session_id END) AS attended
           FROM enrollments e
           JOIN courses c ON c.id = e.course_id
           LEFT JOIN sessions s ON s.course_id = c.id
           LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.student_id = ?
           WHERE e.student_id = ? AND e.status = 'enrolled'
           GROUP BY c.id ORDER BY c.name""",
        (student_id, student_id),
    ).fetchall()

    course_stats = []
    for c in per_course:
        pct = round(100 * c["attended"] / c["sessions_held"]) if c["sessions_held"] else None
        course_stats.append({
            "id": c["id"], "code": c["code"], "name": c["name"],
            "sessions_held": c["sessions_held"], "attended": c["attended"],
            "pct": pct,
            "low": pct is not None and pct < low_attendance_threshold,
        })

    overall_held = sum(c["sessions_held"] for c in course_stats)
    overall_attended = sum(c["attended"] for c in course_stats)
    overall_pct = round(100 * overall_attended / overall_held) if overall_held else None

    history = db.execute(
        """SELECT ar.timestamp, ar.status, ar.method, c.name AS course_name, c.code AS course_code
           FROM attendance_records ar
           JOIN sessions s ON s.id = ar.session_id
           JOIN courses c ON c.id = s.course_id
           WHERE ar.student_id=? ORDER BY ar.timestamp DESC LIMIT 20""",
        (student_id,),
    ).fetchall()

    all_sessions_ordered = db.execute(
        """SELECT s.id AS session_id,
                  MAX(CASE WHEN ar.student_id = ? AND ar.status IN ('present','late') THEN 1 ELSE 0 END) AS attended
           FROM sessions s
           JOIN enrollments e ON e.course_id = s.course_id AND e.student_id = ? AND e.status='enrolled'
           LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.student_id = ?
           WHERE s.session_date <= date('now')
           GROUP BY s.id
           ORDER BY s.session_date DESC, s.start_time DESC""",
        (student_id, student_id, student_id),
    ).fetchall()
    streak = 0
    for row in all_sessions_ordered:
        if row["attended"]:
            streak += 1
        else:
            break

    db.close()
    return render_template(
        "student_dashboard.html", student=student, course_stats=course_stats,
        overall_pct=overall_pct, history=history, streak=streak,
        low_attendance_threshold=low_attendance_threshold,
    )


@app.route("/profile", methods=["GET", "POST"])
@role_required("admin", "lecturer", "student")
def profile():
    role = session["role"]
    db = get_db()
    if role == "student":
        actor = db.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
    else:
        actor = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not check_password_hash(actor["password_hash"], current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 4:
            flash("New password must be at least 4 characters.", "error")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
        else:
            new_hash = generate_password_hash(new_password)
            if role == "student":
                db.execute("UPDATE students SET password_hash=? WHERE id=?", (new_hash, actor["id"]))
            else:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, actor["id"]))
            db.commit()
            log_action("password_change", f"role={role}")
            flash("Password updated.", "success")
            db.close()
            return redirect(url_for("profile"))
    db.close()
    return render_template("profile.html", actor=actor, role=role)


@app.route("/courses", methods=["GET", "POST"])
@role_required("admin")
def courses_list():
    db = get_db()
    if request.method == "POST":
        code = request.form["code"].strip()
        name = request.form["name"].strip()
        lecturer_id = request.form.get("lecturer_id") or None
        try:
            db.execute(
                "INSERT INTO courses (code, name, lecturer_id) VALUES (?,?,?)",
                (code, name, lecturer_id),
            )
            db.commit()
            flash(f"Course {code} created.", "success")
        except Exception as e:
            flash(f"Could not create course: {e}", "error")
    courses = db.execute(
        """SELECT c.*, u.full_name AS lecturer_name FROM courses c
           LEFT JOIN users u ON u.id = c.lecturer_id ORDER BY c.name"""
    ).fetchall()
    lecturers = db.execute("SELECT * FROM users WHERE role='lecturer' ORDER BY full_name").fetchall()
    db.close()
    return render_template("courses.html", courses=courses, lecturers=lecturers)


@app.route("/courses/<int:course_id>/sessions", methods=["GET", "POST"])
@role_required("admin", "lecturer")
def course_sessions(course_id):
    db = get_db()
    course = db.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
    if not course:
        abort(404)
    if session.get("role") == "lecturer" and course["lecturer_id"] != session["user_id"]:
        abort(403)
    if request.method == "POST":
        session_date = request.form["session_date"]
        start_time = request.form["start_time"]
        end_time = request.form.get("end_time") or None
        db.execute(
            """INSERT INTO sessions (course_id, session_date, start_time, end_time, created_by)
               VALUES (?,?,?,?,?)""",
            (course_id, session_date, start_time, end_time, session["user_id"]),
        )
        db.commit()
        flash("Session scheduled.", "success")
    sessions_rows = db.execute(
        "SELECT * FROM sessions WHERE course_id=? ORDER BY session_date DESC, start_time DESC",
        (course_id,),
    ).fetchall()
    db.close()
    return render_template("sessions.html", course=course, sessions=sessions_rows)


@app.route("/courses/<int:course_id>/summary")
@role_required("admin", "lecturer")
def course_attendance_summary(course_id):
    db = get_db()
    course = db.execute(
        "SELECT c.*, u.full_name AS lecturer_name FROM courses c LEFT JOIN users u ON u.id=c.lecturer_id WHERE c.id=?",
        (course_id,),
    ).fetchone()
    if not course:
        abort(404)
    if session.get("role") == "lecturer" and course["lecturer_id"] != session["user_id"]:
        abort(403)

    sessions_rows = db.execute(
        """SELECT s.id, s.session_date, s.start_time,
                  COUNT(DISTINCT e.student_id) AS enrolled,
                  COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.student_id END) AS attended
           FROM sessions s
           LEFT JOIN enrollments e ON e.course_id = s.course_id AND e.status='enrolled'
           LEFT JOIN attendance_records ar ON ar.session_id = s.id
           WHERE s.course_id = ?
           GROUP BY s.id ORDER BY s.session_date, s.start_time""",
        (course_id,),
    ).fetchall()

    per_student = db.execute(
        """SELECT st.id, st.full_name, st.reg_number,
                  COUNT(DISTINCT s.id) AS total_sessions,
                  COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.session_id END) AS attended,
                  ROUND(100.0 * COUNT(DISTINCT CASE WHEN ar.status IN ('present','late') THEN ar.session_id END)
                        / NULLIF(COUNT(DISTINCT s.id), 0), 1) AS pct
           FROM students st
           JOIN enrollments e ON e.student_id = st.id AND e.course_id = ? AND e.status='enrolled'
           LEFT JOIN sessions s ON s.course_id = ?
           LEFT JOIN attendance_records ar ON ar.student_id = st.id AND ar.session_id = s.id
           GROUP BY st.id ORDER BY st.full_name""",
        (course_id, course_id),
    ).fetchall()
    db.close()
    return render_template(
        "course_summary.html", course=course, sessions=sessions_rows, per_student=per_student
    )


@app.route("/attendance/take/<int:session_id>", methods=["GET"])
@role_required("admin", "lecturer")
def take_attendance(session_id):
    db = get_db()
    sess = db.execute(
        """SELECT s.*, c.name AS course_name, c.code AS course_code, c.lecturer_id
           FROM sessions s JOIN courses c ON c.id = s.course_id WHERE s.id=?""",
        (session_id,),
    ).fetchone()
    if not sess:
        abort(404)
    if session.get("role") == "lecturer" and sess["lecturer_id"] != session["user_id"]:
        abort(403)
    marked = db.execute(
        """SELECT ar.*, st.full_name, st.reg_number FROM attendance_records ar
           JOIN students st ON st.id = ar.student_id
           WHERE ar.session_id=? ORDER BY ar.timestamp DESC""",
        (session_id,),
    ).fetchall()
    db.close()
    return render_template("take_attendance.html", sess=sess, marked=marked,
                            late_threshold_minutes=get_setting("late_threshold_minutes", "15"))


def _process_attendance_scan(barcode_value, session_id, method):
    barcode_value = (barcode_value or "").strip()
    if not barcode_value or not session_id:
        return dict(ok=False, message="Missing barcode or session."), 400

    db = get_db()
    sess_row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not sess_row:
        db.close()
        return dict(ok=False, message="Invalid session."), 404

    student = db.execute(
        "SELECT * FROM students WHERE barcode_value=?", (barcode_value,)
    ).fetchone()

    if not student:
        db.close()
        return dict(ok=False, message=f"Invalid barcode: '{barcode_value}' not recognised."), 200

    if student["status"] != "active":
        db.close()
        return dict(ok=False, message=f"{student['full_name']}'s account is inactive."), 200

    existing = db.execute(
        "SELECT id FROM attendance_records WHERE student_id=? AND session_id=?",
        (student["id"], session_id),
    ).fetchone()
    if existing:
        db.close()
        return dict(
            ok=False,
            message=f"Duplicate scan: {student['full_name']} already marked present.",
            duplicate=True,
        ), 200

    now = now_iso()
    status = "present"
    try:
        start_dt = datetime.strptime(f"{sess_row['session_date']} {sess_row['start_time']}", "%Y-%m-%d %H:%M")
        scan_dt = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        late_minutes = float(get_setting("late_threshold_minutes", "15"))
        if (scan_dt - start_dt).total_seconds() > late_minutes * 60:
            status = "late"
    except Exception:
        pass

    db.execute(
        """INSERT INTO attendance_records (student_id, session_id, status, method, marked_by, timestamp)
           VALUES (?,?,?,?,?,?)""",
        (student["id"], session_id, status, method, session["user_id"], now),
    )
    db.commit()
    db.close()
    return dict(
        ok=True,
        message=f"{student['full_name']} ({student['reg_number']}) marked {status}.",
        student={"full_name": student["full_name"], "reg_number": student["reg_number"]},
        status=status,
        timestamp=now,
    ), 200


@app.route("/api/attendance/scan", methods=["POST"])
@role_required("admin", "lecturer")
def api_attendance_scan():
    data = request.get_json(force=True) if request.is_json else request.form
    barcode_value = (data.get("barcode_value") or "").strip()
    session_id = data.get("session_id")
    method = data.get("method", "manual_entry")
    payload, code = _process_attendance_scan(barcode_value, session_id, method)
    return jsonify(**payload), code


@app.route("/api/attendance/scan_photo", methods=["POST"])
@role_required("admin", "lecturer")
def api_attendance_scan_photo():
    session_id = request.form.get("session_id")
    if "photo" not in request.files:
        return jsonify(ok=False, message="No photo uploaded."), 400

    file = request.files["photo"]
    try:
        from PIL import Image
        import zxingcpp
        import io

        image = Image.open(io.BytesIO(file.read())).convert("RGB")
        results = zxingcpp.read_barcodes(image)
    except ImportError:
        return jsonify(
            ok=False,
            message="Server-side photo decoding isn't installed. Run: pip install zxing-cpp Pillow",
        ), 500
    except Exception as e:
        return jsonify(ok=False, message=f"Couldn't read that image: {e}"), 400

    if not results:
        return jsonify(
            ok=False,
            message="Couldn't find a barcode in that photo — try better lighting/focus, "
                    "make sure the barcode fills more of the frame, or use manual entry below.",
        ), 200

    barcode_value = results[0].text
    payload, code = _process_attendance_scan(barcode_value, session_id, "camera_scan")
    return jsonify(**payload), code


@app.route("/attendance/<int:record_id>/adjust", methods=["POST"])
@role_required("admin", "lecturer")
def attendance_manual_adjust(record_id):
    new_status = request.form["status"]
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required for manual attendance adjustments.", "error")
        return redirect(request.referrer or url_for("admin_dashboard"))
    db = get_db()
    db.execute(
        "UPDATE attendance_records SET status=?, method='manual_adjustment', reason=?, marked_by=? WHERE id=?",
        (new_status, reason, session["user_id"], record_id),
    )
    db.commit()
    row = db.execute("SELECT session_id FROM attendance_records WHERE id=?", (record_id,)).fetchone()
    db.close()
    log_action("manual_adjustment", f"record={record_id} status={new_status} reason={reason}")
    flash("Attendance adjusted and logged in the audit trail.", "success")
    return redirect(url_for("take_attendance", session_id=row["session_id"]))


@app.route("/attendance/all")
@role_required("admin")
def attendance_all():
    db = get_db()
    rows = db.execute(
        """SELECT ar.timestamp, ar.status, ar.method, st.full_name, st.reg_number,
                  c.name AS course_name, s.session_date
           FROM attendance_records ar
           JOIN students st ON st.id = ar.student_id
           JOIN sessions s ON s.id = ar.session_id
           JOIN courses c ON c.id = s.course_id
           ORDER BY ar.timestamp DESC LIMIT 200"""
    ).fetchall()
    db.close()
    return render_template("attendance_list.html", rows=rows)


@app.route("/reports", methods=["GET"])
@role_required("admin", "lecturer")
def reports():
    db = get_db()
    if session.get("role") == "lecturer":
        courses = db.execute(
            "SELECT * FROM courses WHERE lecturer_id=? ORDER BY name", (session["user_id"],)
        ).fetchall()
    else:
        courses = db.execute("SELECT * FROM courses ORDER BY name").fetchall()
    db.close()
    return render_template("reports.html", courses=courses)


def _report_rows(course_id, start_date, end_date):
    db = get_db()
    query = """
        SELECT st.reg_number, st.full_name, c.code AS course_code, c.name AS course_name,
               s.session_date, ar.status, ar.timestamp
        FROM attendance_records ar
        JOIN students st ON st.id = ar.student_id
        JOIN sessions s ON s.id = ar.session_id
        JOIN courses c ON c.id = s.course_id
        WHERE 1=1
    """
    params = []
    if course_id:
        query += " AND c.id = ?"
        params.append(course_id)
    if start_date:
        query += " AND s.session_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND s.session_date <= ?"
        params.append(end_date)
    query += " ORDER BY s.session_date DESC, st.full_name"
    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.route("/reports/generate")
@role_required("admin", "lecturer")
def reports_generate():
    course_id = request.args.get("course_id") or None
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    fmt = request.args.get("format", "pdf")

    rows = _report_rows(course_id, start_date, end_date)

    if fmt == "excel":
        buf = build_excel_report(rows)
        return send_file(
            buf, as_attachment=True, download_name="attendance_report.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        buf = build_pdf_report(rows, start_date, end_date)
        return send_file(
            buf, as_attachment=True, download_name="attendance_report.pdf",
            mimetype="application/pdf",
        )


@app.route("/users", methods=["GET", "POST"])
@role_required("admin")
def users_list():
    db = get_db()
    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        email = request.form.get("email", "").strip()
        role = request.form["role"]
        password = request.form["password"]
        try:
            db.execute(
                """INSERT INTO users (username, password_hash, full_name, email, role)
                   VALUES (?,?,?,?,?)""",
                (username, generate_password_hash(password), full_name, email, role),
            )
            db.commit()
            flash(f"User '{username}' created.", "success")
        except Exception as e:
            flash(f"Could not create user: {e}", "error")
    rows = db.execute("SELECT * FROM users ORDER BY role, full_name").fetchall()
    db.close()
    return render_template("users.html", users=rows)


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@role_required("admin")
def user_toggle(user_id):
    db = get_db()
    row = db.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
    db.execute("UPDATE users SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, user_id))
    db.commit()
    db.close()
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/reset_password", methods=["POST"])
@role_required("admin")
def user_reset_password(user_id):
    import secrets
    db = get_db()
    target = db.execute("SELECT username, full_name FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        db.close()
        abort(404)
    temp_password = secrets.token_urlsafe(6)
    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(temp_password), user_id),
    )
    db.commit()
    db.close()
    log_action("password_reset", f"admin reset password for user={target['username']}")
    flash(
        f"Temporary password for {target['full_name']} ({target['username']}): "
        f"{temp_password} — share this with them securely. They should change it "
        f"from Profile after logging in.",
        "success",
    )
    return redirect(url_for("user_detail", user_id=user_id))


@app.route("/lecturers")
@role_required("admin")
def lecturers_list():
    db = get_db()
    rows = db.execute(
        """SELECT u.*,
                  COUNT(DISTINCT c.id) AS course_count,
                  COUNT(DISTINCT s.id) AS session_count
           FROM users u
           LEFT JOIN courses c ON c.lecturer_id = u.id
           LEFT JOIN sessions s ON s.course_id = c.id
           WHERE u.role = 'lecturer'
           GROUP BY u.id
           ORDER BY u.full_name"""
    ).fetchall()
    db.close()
    return render_template("lecturers.html", lecturers=rows)


@app.route("/users/<int:user_id>")
@role_required("admin")
def user_detail(user_id):
    db = get_db()
    staff = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not staff:
        db.close()
        abort(404)
    courses = db.execute("SELECT * FROM courses WHERE lecturer_id=? ORDER BY name", (user_id,)).fetchall()
    stats = db.execute(
        """SELECT COUNT(DISTINCT s.id) AS sessions_run,
                  COUNT(ar.id) AS attendance_records
           FROM sessions s
           JOIN courses c ON c.id = s.course_id
           LEFT JOIN attendance_records ar ON ar.session_id = s.id
           WHERE c.lecturer_id = ?""",
        (user_id,),
    ).fetchone()
    recent_sessions = db.execute(
        """SELECT s.session_date, s.start_time, c.code AS course_code,
                  COUNT(ar.id) AS attended
           FROM sessions s
           JOIN courses c ON c.id = s.course_id
           LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.status IN ('present','late')
           WHERE c.lecturer_id = ?
           GROUP BY s.id
           ORDER BY s.session_date DESC, s.start_time DESC
           LIMIT 10""",
        (user_id,),
    ).fetchall()
    adjustments = db.execute(
        """SELECT ar.timestamp, ar.status, ar.reason, st.full_name, st.reg_number, c.name AS course_name
           FROM attendance_records ar
           JOIN students st ON st.id = ar.student_id
           JOIN sessions s ON s.id = ar.session_id
           JOIN courses c ON c.id = s.course_id
           WHERE ar.marked_by = ? AND ar.method = 'manual_adjustment'
           ORDER BY ar.timestamp DESC
           LIMIT 20""",
        (user_id,),
    ).fetchall()
    db.close()
    return render_template(
        "user_detail.html", staff=staff, courses=courses, stats=stats,
        recent_sessions=recent_sessions, adjustments=adjustments,
    )


@app.route("/password-reset-requests")
@role_required("admin")
def password_reset_requests_view():
    _ensure_reset_table_ready()
    db = get_db()
    pending = db.execute(
        "SELECT * FROM password_reset_requests WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    resolved = db.execute(
        "SELECT * FROM password_reset_requests WHERE status='resolved' ORDER BY resolved_at DESC LIMIT 50"
    ).fetchall()
    db.close()
    return render_template("password_reset_requests.html", pending=pending, resolved=resolved)


@app.route("/password-reset-requests/<int:request_id>/resolve", methods=["POST"])
@role_required("admin")
def password_reset_request_resolve(request_id):
    _ensure_reset_table_ready()
    db = get_db()
    req = db.execute("SELECT * FROM password_reset_requests WHERE id=?", (request_id,)).fetchone()
    if not req:
        db.close()
        abort(404)
    db.execute(
        "UPDATE password_reset_requests SET status='resolved', resolved_at=datetime('now'), resolved_by=? WHERE id=?",
        (session.get("full_name"), request_id),
    )
    db.commit()
    db.close()
    log_action("password_reset_request_resolved", f"{req['requester_type']}={req['username']}")
    flash("Marked as resolved.", "success")
    return redirect(url_for("password_reset_requests_view"))


@app.route("/audit")
@role_required("admin")
def audit_log_view():
    db = get_db()
    action_filter = request.args.get("action", "").strip()
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if action_filter:
        query += " AND action = ?"
        params.append(action_filter)
    query += " ORDER BY created_at DESC LIMIT 300"
    rows = db.execute(query, params).fetchall()
    actions = db.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
    db.close()
    return render_template("audit_log.html", rows=rows, actions=actions, action_filter=action_filter)


@app.route("/settings", methods=["GET", "POST"])
@role_required("admin")
def settings_page():
    if request.method == "POST":
        for key in ("institution_name", "late_threshold_minutes", "low_attendance_percent"):
            value = request.form.get(key, "").strip()
            if value:
                set_setting(key, value)
        log_action("settings_update", "; ".join(f"{k}" for k in request.form.keys()))
        flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=get_all_settings())


@app.route("/backup")
@role_required("admin")
def backup_page():
    db_exists = os.path.exists(DB_PATH)
    db_size_kb = round(os.path.getsize(DB_PATH) / 1024, 1) if db_exists else 0
    return render_template("backup.html", db_size_kb=db_size_kb, db_exists=db_exists)


@app.route("/backup/download")
@role_required("admin")
def backup_download():
    if not os.path.exists(DB_PATH):
        flash("No database file found yet.", "error")
        return redirect(url_for("backup_page"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(DB_PATH, "rb") as f:
        buf = io.BytesIO(f.read())
    buf.seek(0)
    log_action("backup_download", f"file=attendance_backup_{timestamp}.db")
    return send_file(
        buf, as_attachment=True,
        download_name=f"attendance_backup_{timestamp}.db",
        mimetype="application/octet-stream",
    )


@app.cli.command("init-db")
def init_db_command():
    init_db(reset=True)
    print("Initialized the database.")


if __name__ == "__main__":
    first_time = init_db(reset=False)
    if first_time:
        db = get_db()
        db.execute(
            """INSERT INTO users (username, password_hash, full_name, email, role)
               VALUES (?,?,?,?,?)""",
            ("admin", generate_password_hash("admin123"), "System Administrator", "admin@example.com", "admin"),
        )
        db.commit()
        db.close()
        print("First run: created default admin login -> username: admin / password: admin123")
    app.run(debug=True, host="0.0.0.0", port=5000)
