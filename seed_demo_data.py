"""
seed_demo_data.py
------------------------------------------------------------------
Populates the database with realistic demo data so the dashboard,
charts, at-risk list, and reports all have something meaningful to
show during a supervisor demo/defense — instead of empty tables.

Safe to run on a fresh DB. Re-running just adds more attendance
history on top (it won't duplicate students/courses/lecturers
because of the UNIQUE constraints — it skips ones that already exist).

Usage:
    python seed_demo_data.py
------------------------------------------------------------------
"""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from database import get_db, init_db, now_iso

random.seed(42)

LECTURERS = [
    ("j.moyo", "Dr. J. Moyo", "j.moyo@college.ac.zw"),
    ("t.ncube", "Mrs. T. Ncube", "t.ncube@college.ac.zw"),
]

COURSES = [
    ("CS201", "Data Structures & Algorithms", 0),
    ("CS305", "Database Systems", 0),
    ("IT210", "Computer Networks", 1),
]

FIRST_NAMES = ["Tendai", "Rutendo", "Farai", "Chipo", "Tafadzwa", "Nyasha", "Simba",
               "Rumbidzai", "Tapiwa", "Vimbai", "Kudakwashe", "Ropafadzo", "Tanaka",
               "Munashe", "Panashe", "Tinotenda", "Chiedza", "Anesu", "Blessing", "Praise"]
LAST_NAMES = ["Moyo", "Ncube", "Dube", "Sibanda", "Mhlanga", "Chirwa", "Gumbo",
              "Mutasa", "Chikanga", "Marufu", "Zulu", "Nyathi", "Mavhunga", "Chikwava"]


def seed():
    init_db(reset=False)  # creates schema only if the DB doesn't exist yet
    db = get_db()

    # --- Lecturers -----------------------------------------------------
    lecturer_ids = []
    for username, full_name, email in LECTURERS:
        row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            lecturer_ids.append(row["id"])
            continue
        cur = db.execute(
            """INSERT INTO users (username, password_hash, full_name, email, role)
               VALUES (?, ?, ?, ?, 'lecturer')""",
            (username, generate_password_hash("lecturer123"), full_name, email),
        )
        lecturer_ids.append(cur.lastrowid)
    db.commit()

    # --- Courses ---------------------------------------------------------
    course_ids = []
    for code, name, lecturer_idx in COURSES:
        row = db.execute("SELECT id FROM courses WHERE code=?", (code,)).fetchone()
        if row:
            course_ids.append(row["id"])
            continue
        cur = db.execute(
            "INSERT INTO courses (code, name, lecturer_id) VALUES (?, ?, ?)",
            (code, name, lecturer_ids[lecturer_idx]),
        )
        course_ids.append(cur.lastrowid)
    db.commit()

    # --- Students ----------------------------------------------------
    existing_count = db.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
    student_ids = [r["id"] for r in db.execute("SELECT id FROM students").fetchall()]
    target_total = 35
    reg_start = 1001
    while existing_count < target_total:
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        reg_number = f"R{date.today().year % 100}{reg_start:04d}"
        reg_start += 1
        barcode_value = f"STU-{reg_number}"
        existing = db.execute("SELECT id FROM students WHERE reg_number=?", (reg_number,)).fetchone()
        if existing:
            continue
        cur = db.execute(
            """INSERT INTO students (reg_number, full_name, email, department, barcode_value, password_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (reg_number, f"{fn} {ln}", f"{reg_number.lower()}@student.college.ac.zw",
             "Computer Science", barcode_value, generate_password_hash(reg_number)),
        )
        student_ids.append(cur.lastrowid)
        existing_count += 1
    db.commit()

    # --- Enrollments: each student in 1-3 of the demo courses ------------
    for sid in student_ids:
        for cid in random.sample(course_ids, k=random.randint(1, len(course_ids))):
            existing = db.execute(
                "SELECT id FROM enrollments WHERE student_id=? AND course_id=?", (sid, cid)
            ).fetchone()
            if not existing:
                db.execute(
                    "INSERT INTO enrollments (student_id, course_id, status) VALUES (?,?, 'enrolled')",
                    (sid, cid),
                )
    db.commit()

    # --- Sessions over the past 5 weeks (2 sessions/week/course) + attendance ---
    admin_row = db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    creator_id = admin_row["id"] if admin_row else lecturer_ids[0]
    today = date.today()

    for cid in course_ids:
        enrolled = [r["student_id"] for r in db.execute(
            "SELECT student_id FROM enrollments WHERE course_id=? AND status='enrolled'", (cid,)
        ).fetchall()]
        if not enrolled:
            continue
        # Give each student in this course a stable "reliability" score so
        # attendance patterns look realistic (some always show up, a few don't).
        reliability = {sid: random.betavariate(5, 2) for sid in enrolled}

        for week_ago in range(5, 0, -1):
            session_date = today - timedelta(days=week_ago * 7 + random.choice([0, 2]))
            if session_date > today:
                continue
            start_time = random.choice(["08:00", "10:00", "13:00", "15:00"])
            existing_session = db.execute(
                "SELECT id FROM sessions WHERE course_id=? AND session_date=? AND start_time=?",
                (cid, session_date.isoformat(), start_time),
            ).fetchone()
            if existing_session:
                sess_id = existing_session["id"]
            else:
                cur = db.execute(
                    """INSERT INTO sessions (course_id, session_date, start_time, end_time, created_by)
                       VALUES (?, ?, ?, ?, ?)""",
                    (cid, session_date.isoformat(), start_time, "", creator_id),
                )
                sess_id = cur.lastrowid

            for sid in enrolled:
                already = db.execute(
                    "SELECT id FROM attendance_records WHERE student_id=? AND session_id=?",
                    (sid, sess_id),
                ).fetchone()
                if already:
                    continue
                roll = random.random()
                if roll < reliability[sid]:
                    status = "present" if random.random() > 0.15 else "late"
                    ts = f"{session_date.isoformat()} {start_time}:00"
                    method = random.choice(["camera_scan", "camera_scan", "manual_entry"])
                    db.execute(
                        """INSERT INTO attendance_records
                           (student_id, session_id, status, method, marked_by, timestamp)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (sid, sess_id, status, method, creator_id, ts),
                    )
                # else: absent -> simply no record (matches how the real app treats absences)
    db.commit()
    db.close()
    print(f"Seed complete: {len(course_ids)} courses, {len(student_ids)} students, "
          f"{len(lecturer_ids)} lecturers, ~5 weeks of sessions/attendance.")
    print("Lecturer logins: j.moyo / lecturer123,  t.ncube / lecturer123")
    print("Student logins: <reg_number> / <reg_number>  e.g. the ones printed in /students")


if __name__ == "__main__":
    seed()
