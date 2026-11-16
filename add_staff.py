# -*- coding: utf-8 -*-
"""Add (or update) a staff member in queue.sqlite so that YOUR Telegram account can use the staff panel.
Run:  python add_staff.py      (the script asks for everything)
Your Telegram ID: send /myid to the bot. The PIN is stored only as a salted PBKDF2 hash (as in Assignment 3)."""
import getpass, hashlib, os, sqlite3

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.sqlite"))

def hash_pin(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), 200_000).hex()

tg = int(input("Telegram ID сотрудника (число, узнать: /myid в боте): ").strip())
name = input("Имя сотрудника (например: Test Operator): ").strip()
role = ""
while role not in ("operator", "administrator"):
    role = input("Роль (operator / administrator): ").strip().lower()
pin = ""
while len(pin) < 4:
    pin = getpass.getpass("PIN (минимум 4 символа, при вводе не отображается): ").strip()
prefixes = []
if role == "operator":
    prefixes = [c for c in input("Услуги оператора через пробел (C R A O), например: C R: ").upper().split() if c in "CRAO"]

salt = os.urandom(16).hex()
db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA foreign_keys = ON")
db.execute("""INSERT INTO staff(telegram_id, full_name, role, pin_salt, pin_hash, is_active) VALUES (?,?,?,?,?,1)
              ON CONFLICT(telegram_id) DO UPDATE SET full_name = excluded.full_name, role = excluded.role,
              pin_salt = excluded.pin_salt, pin_hash = excluded.pin_hash, is_active = 1""",
           (tg, name, role, salt, hash_pin(pin, salt)))
sid = db.execute("SELECT staff_id FROM staff WHERE telegram_id = ?", (tg,)).fetchone()[0]
db.execute("DELETE FROM staff_services WHERE staff_id = ?", (sid,))
for p in prefixes:
    db.execute("INSERT INTO staff_services(staff_id, service_id) SELECT ?, service_id FROM services WHERE prefix = ?", (sid, p))
db.commit()
db.close()
print(f"Готово: {name} ({role}), Telegram ID {tg}" + (f", услуги: {' '.join(prefixes)}" if prefixes else ""))
