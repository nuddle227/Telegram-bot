# -*- coding: utf-8 -*-
"""Add or update a Telegram staff account for bot.py (same PBKDF2 scheme).
Run: python add_staff.py; obtain your Telegram ID with /myid in the bot.
Staff PINs are stored only as salted PBKDF2-HMAC-SHA256 hashes.
"""

import getpass
import hashlib
import os
import sqlite3
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "queue.sqlite"))
PIN_ROUNDS = 600_000  # Matches bot.py PIN_ROUNDS; store the rounds with the hash.


def hash_pin(pin, salt_hex, rounds=PIN_ROUNDS):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), rounds).hex()


def main():
    if not Path(DB_PATH).is_file():
        raise SystemExit("Database not found. Run 'python make_test_data.py' first.")

    tg = int(input("Telegram ID сотрудника (число, узнать: /myid в боте): ").strip())
    name = input("Имя сотрудника (например: Test Operator): ").strip()
    if not name:
        raise SystemExit("Имя не должно быть пустым.")
    role = ""
    while role not in ("operator", "administrator"):
        role = input("Роль (operator / administrator): ").strip().lower()
    pin = ""
    while len(pin) < 4:
        pin = getpass.getpass("PIN (минимум 4 символа, при вводе не отображается): ").strip()
    prefixes = []
    if role == "operator":
        prefixes = sorted(set(input("Услуги оператора через пробел (C R A O), например: C R: ").upper().split()) & set("CRAO"))
        if not prefixes:
            raise SystemExit("Для оператора нужно выбрать хотя бы одну услугу.")

    salt = os.urandom(16).hex()
    with sqlite3.connect(DB_PATH, timeout=10) as db:
        db.execute("PRAGMA foreign_keys = ON")
        # Older Assignment 3 databases do not have this field until bot.py starts.
        columns = {row[1] for row in db.execute("PRAGMA table_info(staff)")}
        if not columns:
            raise SystemExit("Database has no staff table. Run 'python make_test_data.py'.")
        if "pin_rounds" not in columns:
            db.execute("ALTER TABLE staff ADD COLUMN pin_rounds INTEGER NOT NULL DEFAULT 200000")

        db.execute("""INSERT INTO staff(telegram_id, full_name, role, pin_salt, pin_hash, pin_rounds, is_active)
                      VALUES (?, ?, ?, ?, ?, ?, 1)
                      ON CONFLICT(telegram_id) DO UPDATE SET
                        full_name = excluded.full_name, role = excluded.role,
                        pin_salt = excluded.pin_salt, pin_hash = excluded.pin_hash,
                        pin_rounds = excluded.pin_rounds, is_active = 1""",
                   (tg, name, role, salt, hash_pin(pin, salt), PIN_ROUNDS))
        sid = db.execute("SELECT staff_id FROM staff WHERE telegram_id = ?", (tg,)).fetchone()[0]
        db.execute("DELETE FROM staff_services WHERE staff_id = ?", (sid,))
        for prefix in prefixes:
            db.execute("""INSERT INTO staff_services(staff_id, service_id)
                          SELECT ?, service_id FROM services WHERE prefix = ?""", (sid, prefix))

    print(f"Готово: {name} ({role}), Telegram ID {tg}" +
          (f", услуги: {' '.join(prefixes)}" if prefixes else ""))


if __name__ == "__main__":
    main()
