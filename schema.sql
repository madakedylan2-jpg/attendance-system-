-- Barcode Attendance Tracking System — Database Schema
-- Pure software edition: no hardware scanner tables/fields required.
-- SQLite (works out of the box; can be ported to MySQL/Postgres unchanged in spirit).

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS attendance_records;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS settings;

-- Administrators and Lecturers (role-based access control)
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    email         TEXT,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'lecturer')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Student bio-data table (Table 1 in the original document)
CREATE TABLE students (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_number    TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    email         TEXT,
    department    TEXT,
    barcode_value TEXT NOT NULL UNIQUE,   -- Code128 payload rendered/scanned entirely in software
    password_hash TEXT NOT NULL DEFAULT '',  -- student self-service login; defaults to reg_number, hashed, at creation
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Courses, each owned by a lecturer
CREATE TABLE courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    lecturer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Student <-> Course enrollment
CREATE TABLE enrollments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'enrolled' CHECK (status IN ('enrolled', 'dropped', 'completed')),
    UNIQUE (student_id, course_id)
);

-- A class session/lecture instance that attendance is taken against
CREATE TABLE sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    session_date TEXT NOT NULL,
    start_time   TEXT NOT NULL,
    end_time     TEXT,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Attendance records — the core of the system
CREATE TABLE attendance_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'present' CHECK (status IN ('present', 'late', 'absent')),
    method      TEXT NOT NULL DEFAULT 'camera_scan' CHECK (method IN ('camera_scan', 'manual_entry', 'manual_adjustment')),
    reason      TEXT,                       -- required for manual adjustments (audit trail)
    marked_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (student_id, session_id)          -- duplicate-scan prevention at the DB level
);

-- Simple audit trail for manual adjustments / sensitive actions.
-- No FK on the actor: three different roles (admin/lecturer via `users`,
-- student via `students`) can trigger audited actions, and those two
-- tables don't share an id space — a FK to one table would break or
-- misattribute entries from the other. actor_role + actor_name keep the
-- trail human-readable regardless of which table the actor came from.
CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_role TEXT,
    actor_id   INTEGER,
    actor_name TEXT,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Admin-configurable system settings (key/value), e.g. institution name,
-- the "late" threshold in minutes, and the low-attendance warning percent.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_attendance_student ON attendance_records (student_id);
CREATE INDEX idx_attendance_session ON attendance_records (session_id);
CREATE INDEX idx_attendance_timestamp ON attendance_records (timestamp);
CREATE INDEX idx_enrollment_student_course ON enrollments (student_id, course_id);

-- Internal messaging: admin/lecturers -> lecturers/students, no email
-- server required. One `messages` row per send action; fanned out to one
-- `message_recipients` row per actual recipient (even for broadcasts),
-- so read/unread state and inbox queries stay simple regardless of
-- whether the message was targeted or broadcast.
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_role TEXT NOT NULL CHECK (sender_role IN ('admin','lecturer')),
    sender_id   INTEGER NOT NULL,
    sender_name TEXT NOT NULL,
    target_desc TEXT NOT NULL,   -- human-readable label, e.g. "All students" or "CS201 students"
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE message_recipients (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id     INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('student','lecturer')),
    recipient_id   INTEGER NOT NULL,
    read_at        TEXT
);

CREATE INDEX idx_msg_recipient ON message_recipients (recipient_type, recipient_id, read_at);
