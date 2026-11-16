PRAGMA foreign_keys = ON;

CREATE TABLE services (
    service_id   INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    prefix       TEXT    NOT NULL UNIQUE CHECK (length(prefix) = 1),
    avg_minutes  INTEGER NOT NULL CHECK (avg_minutes > 0)
);

CREATE TABLE students (
    student_id    INTEGER PRIMARY KEY,
    telegram_id   INTEGER NOT NULL UNIQUE,
    full_name     TEXT    NOT NULL CHECK (length(full_name) BETWEEN 2 AND 60),
    group_name    TEXT    NOT NULL CHECK (length(group_name) BETWEEN 3 AND 12),
    registered_at TEXT    NOT NULL
);

CREATE TABLE staff (
    staff_id    INTEGER PRIMARY KEY,
    telegram_id INTEGER NOT NULL UNIQUE,
    full_name   TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('operator', 'administrator')),
    pin_salt    TEXT    NOT NULL,
    pin_hash    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE staff_services (
    staff_id   INTEGER NOT NULL REFERENCES staff(staff_id) ON DELETE CASCADE,
    service_id INTEGER NOT NULL REFERENCES services(service_id),
    PRIMARY KEY (staff_id, service_id)
);

CREATE TABLE tickets (
    ticket_id   INTEGER PRIMARY KEY,
    student_id  INTEGER REFERENCES students(student_id) ON DELETE SET NULL,
    service_id  INTEGER NOT NULL REFERENCES services(service_id),
    staff_id    INTEGER REFERENCES staff(staff_id) ON DELETE SET NULL,
    queue_date  TEXT    NOT NULL,
    number      INTEGER NOT NULL CHECK (number > 0),
    status      TEXT    NOT NULL DEFAULT 'waiting'
                CHECK (status IN ('waiting', 'called', 'done', 'skipped', 'cancelled')),
    created_at  TEXT    NOT NULL,
    called_at   TEXT,
    finished_at TEXT,
    notified    INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0, 1)),
    UNIQUE (service_id, queue_date, number),
    CHECK (status <> 'done' OR finished_at IS NOT NULL),
    CHECK (called_at IS NULL OR called_at >= created_at)
);

-- R8: one active ticket per student, enforced by the database itself
CREATE UNIQUE INDEX one_active_ticket
    ON tickets(student_id) WHERE status IN ('waiting', 'called');

CREATE TABLE audit_log (
    log_id     INTEGER PRIMARY KEY,
    staff_id   INTEGER REFERENCES staff(staff_id) ON DELETE SET NULL,
    ticket_id  INTEGER REFERENCES tickets(ticket_id) ON DELETE SET NULL,
    action     TEXT NOT NULL CHECK (action IN ('call', 'skip', 'complete', 'denied')),
    created_at TEXT NOT NULL
);

-- Smart corridor (Project Stage 2, Part D). Only counts and time: no personal data.
CREATE TABLE sensor_readings (
    reading_id  INTEGER PRIMARY KEY,
    sensor_id   TEXT    NOT NULL,
    recorded_at TEXT    NOT NULL,
    people_in   INTEGER NOT NULL CHECK (people_in  >= 0),
    people_out  INTEGER NOT NULL CHECK (people_out >= 0),
    in_corridor INTEGER NOT NULL CHECK (in_corridor >= 0)
);
CREATE INDEX idx_sensor_time ON sensor_readings(recorded_at);

-- Rule "tell me when the corridor is free": one row per subscribed student, deleted together with the student.
CREATE TABLE corridor_subs (
    student_id INTEGER PRIMARY KEY REFERENCES students(student_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

