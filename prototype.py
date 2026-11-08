"""Command-line prototype of the Dean's Office queue (Assignment 3).
The same functions will be called from the Telegram bot in Stage 2.
Usage examples are in README.txt. All SQL uses ? placeholders (no string concatenation)."""
import argparse, hashlib, hmac, os, sqlite3, sys
from pathlib import Path
from datetime import datetime, date

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "queue.sqlite"))

QDATE = None   # the queue date the program works with

def connect():
    global QDATE
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    QDATE = os.environ.get("QUEUE_DATE") or active_date(db)
    return db

def active_date(db):
    """Today, unless an older queue is still active in the database (then we continue that queue)."""
    t = date.today().isoformat()
    old = db.execute("SELECT MAX(queue_date) FROM tickets WHERE status IN ('waiting','called')").fetchone()[0]
    return old if old and old < t else t

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def hash_pin(pin, salt_hex, rounds=200_000):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), rounds).hex()

def log(db, staff_id, ticket_id, action):
    db.execute("INSERT INTO audit_log(staff_id, ticket_id, action, created_at) VALUES (?,?,?,?)",
               (staff_id, ticket_id, action, now()))
    db.commit()

def check_staff(db, tg_id, pin):
    """R12: only approved staff with the right PIN can use staff functions."""
    columns = {r[1] for r in db.execute("PRAGMA table_info(staff)")}
    rounds_field = "pin_rounds" if "pin_rounds" in columns else "200000 AS pin_rounds"
    row = db.execute(f"SELECT staff_id, role, pin_salt, pin_hash, {rounds_field} FROM staff "
                     "WHERE telegram_id = ? AND is_active = 1", (tg_id,)).fetchone()
    if row is None or not hmac.compare_digest(hash_pin(pin, row["pin_salt"], row["pin_rounds"]), row["pin_hash"]):
        log(db, row["staff_id"] if row else None, None, "denied")
        sys.exit("Access denied: staff functions are only for approved staff.")
    return row

def service_by_prefix(db, prefix):
    s = db.execute("SELECT * FROM services WHERE prefix = ?", (prefix.upper(),)).fetchone()
    if s is None:
        sys.exit("Unknown service. Use C, R, A or O.")
    return s

# ---------------- student side ----------------
def register(db, tg, name, group):
    name, group = name.strip(), group.strip()
    try:
        db.execute("INSERT INTO students(telegram_id, full_name, group_name, registered_at) VALUES (?,?,?,?)",
                   (tg, name, group, now()))
        db.commit()
    except sqlite3.IntegrityError as e:
        sys.exit(f"Registration refused: {e}")
    print(f"Registered: {name} ({group}). You can now take a ticket.")

def get_student(db, tg):
    s = db.execute("SELECT * FROM students WHERE telegram_id = ?", (tg,)).fetchone()
    if s is None:
        sys.exit("Please register first.")
    return s

def show_services(db):                                   # READ
    rows = db.execute("""SELECT s.name, s.prefix, s.avg_minutes,
                                COUNT(t.ticket_id) AS waiting
                         FROM services s
                         LEFT JOIN tickets t ON t.service_id = s.service_id
                              AND t.queue_date = ? AND t.status = 'waiting'
                         GROUP BY s.service_id ORDER BY s.service_id""", (today(),)).fetchall()
    print("Choose a service to take a ticket:")
    for r in rows:
        print(f"  [{r['prefix']}] {r['name']:<14} ({r['waiting']} waiting)")

def today():
    return QDATE

def take(db, tg, prefix):                                # WRITE
    st = get_student(db, tg)
    srv = service_by_prefix(db, prefix)
    n = db.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM tickets WHERE service_id = ? AND queue_date = ?",
                   (srv["service_id"], today())).fetchone()[0]
    try:
        db.execute("""INSERT INTO tickets(student_id, service_id, queue_date, number, status, created_at)
                      VALUES (?,?,?,?, 'waiting', ?)""", (st["student_id"], srv["service_id"], today(), n, now()))
        db.commit()
    except sqlite3.IntegrityError as e:
        if "DAILY_LIMIT" in str(e):
            sys.exit("Daily ticket limit reached (3 tickets, including cancelled).")
        sys.exit("You already have an active ticket, or the ticket number is taken (R8).")
    print("Ticket created.")
    show_my(db, tg)

def show_my(db, tg):                                     # READ
    st = get_student(db, tg)
    t = db.execute("""SELECT t.*, s.prefix, s.name, s.avg_minutes FROM tickets t
                      JOIN services s ON s.service_id = t.service_id
                      WHERE t.student_id = ? AND t.status IN ('waiting','called')""",
                   (st["student_id"],)).fetchone()
    if t is None:
        print("You have no active ticket."); return
    if t["status"] == "called":
        print(f"Your ticket {t['prefix']}-{t['number']}: it is your turn, please come to the office."); return
    ahead = db.execute("""SELECT COUNT(*) FROM tickets WHERE service_id = ? AND queue_date = ?
                          AND status = 'waiting' AND number < ?""",
                       (t["service_id"], t["queue_date"], t["number"])).fetchone()[0]
    serving = db.execute("""SELECT number FROM tickets WHERE service_id = ? AND queue_date = ?
                            AND status = 'called'""", (t["service_id"], t["queue_date"])).fetchone()
    if ahead <= 3:
        print("Your turn is near.")
    print(f"Your ticket:    {t['prefix']}-{t['number']} ({t['name']})")
    print(f"Position:       {ahead + 1}")
    print(f"People ahead:   {ahead}")
    print(f"Estimated wait: about {ahead * t['avg_minutes']} minutes")
    print(f"Now serving:    {t['prefix']}-{serving['number']}" if serving else "Now serving:    nobody yet")

def cancel(db, tg):                                      # WRITE
    st = get_student(db, tg)
    cur = db.execute("UPDATE tickets SET status = 'cancelled' WHERE student_id = ? AND status = 'waiting'",
                     (st["student_id"],))
    db.commit()
    print("Ticket cancelled." if cur.rowcount else "No waiting ticket to cancel.")

# ---------------- staff side ----------------
def can_serve(db, staff, srv):
    if staff["role"] == "administrator":
        return True
    return db.execute("SELECT 1 FROM staff_services WHERE staff_id = ? AND service_id = ?",
                      (staff["staff_id"], srv["service_id"])).fetchone() is not None

def show_queue(db, staff, prefix):                       # READ
    srv = service_by_prefix(db, prefix)
    if not can_serve(db, staff, srv):
        log(db, staff["staff_id"], None, "denied")
        sys.exit("Access denied: you do not work with this service.")
    rows = db.execute("""SELECT t.number, t.status, st.full_name, st.group_name FROM tickets t
                         LEFT JOIN students st ON st.student_id = t.student_id
                         WHERE t.service_id = ? AND t.queue_date = ? AND t.status IN ('waiting','called')
                         ORDER BY t.number""", (srv["service_id"], today())).fetchall()
    print(f"Staff panel: {srv['name']} ({srv['prefix']}), in queue: {len(rows)}")
    for r in rows:
        mark = "NOW SERVING" if r["status"] == "called" else "waiting"
        print(f"  {srv['prefix']}-{r['number']:<3} {(r['full_name'] or '—'):<24} {(r['group_name'] or '—'):<8} {mark}")

def staff_action(db, staff, prefix, action):             # WRITE
    srv = service_by_prefix(db, prefix)
    if not can_serve(db, staff, srv):
        log(db, staff["staff_id"], None, "denied")
        sys.exit("Access denied: you do not work with this service.")
    cur_t = db.execute("SELECT * FROM tickets WHERE service_id = ? AND queue_date = ? AND status = 'called'",
                       (srv["service_id"], today())).fetchone()
    if action == "call":
        if cur_t:
            sys.exit(f"{srv['prefix']}-{cur_t['number']} is still being served. Complete or skip it first.")
        nxt = db.execute("""SELECT ticket_id, number FROM tickets WHERE service_id = ? AND queue_date = ?
                            AND status = 'waiting' ORDER BY number LIMIT 1""",
                         (srv["service_id"], today())).fetchone()
        if nxt is None:
            sys.exit("The queue is empty.")
        db.execute("UPDATE tickets SET status = 'called', called_at = ?, staff_id = ? WHERE ticket_id = ?",
                   (now(), staff["staff_id"], nxt["ticket_id"]))
        log(db, staff["staff_id"], nxt["ticket_id"], "call")
        print(f"Called {srv['prefix']}-{nxt['number']}.")
    else:
        if cur_t is None:
            sys.exit("Nobody is being served now.")
        new = "done" if action == "complete" else "skipped"
        db.execute("UPDATE tickets SET status = ?, finished_at = ? WHERE ticket_id = ?",
                   (new, now() if new == "done" else None, cur_t["ticket_id"]))
        log(db, staff["staff_id"], cur_t["ticket_id"], action)
        print(f"{srv['prefix']}-{cur_t['number']} marked as {new}.")

def stats(db, staff):                                    # READ (R10)
    print("Visits per day (done tickets):")
    for r in db.execute("""SELECT queue_date, COUNT(*) AS n FROM tickets WHERE status = 'done'
                           GROUP BY queue_date ORDER BY queue_date DESC LIMIT 5"""):
        print(f"  {r['queue_date']}  {r['n']}")

# ---------------- command line ----------------
def main():
    p = argparse.ArgumentParser(description="Dean's Office queue prototype")
    p.add_argument("command", choices=["register", "services", "take", "my", "cancel",
                                       "queue", "call", "skip", "complete", "stats"])
    p.add_argument("--tg", type=int, help="Telegram ID of the user")
    p.add_argument("--name"); p.add_argument("--group")
    p.add_argument("--service", help="C, R, A or O")
    p.add_argument("--pin", help="staff PIN")
    a = p.parse_args()
    db = connect()
    c = a.command
    if c == "services": show_services(db)
    elif c == "register": register(db, a.tg, a.name, a.group)
    elif c == "take": take(db, a.tg, a.service)
    elif c == "my": show_my(db, a.tg)
    elif c == "cancel": cancel(db, a.tg)
    else:
        staff = check_staff(db, a.tg, a.pin or "")
        if c == "queue": show_queue(db, staff, a.service)
        elif c == "stats": stats(db, staff)
        else: staff_action(db, staff, a.service, c)

if __name__ == "__main__":
    main()
