# -*- coding: utf-8 -*-
"""Simulated corridor people counter compatible with the final Telegram bot.

Writes to sensor_readings, which bot.py reads in corridor_now().
Run alongside the bot: python sensor_sim.py
For a one-reading smoke test: python sensor_sim.py --once
"""

import argparse
import os
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "queue.sqlite"))
EVERY = 3  # seconds between readings
SENSOR_ID = "simulated-corridor-01"


def simulate(once=False):
    if not Path(DB_PATH).is_file():
        raise SystemExit("Database not found. Run 'python make_test_data.py' first.")

    with sqlite3.connect(DB_PATH, timeout=10) as db:
        # Mirrors bot.py's ensure_schema(), so the simulator also works if started first.
        db.execute("""CREATE TABLE IF NOT EXISTS sensor_readings (
            reading_id INTEGER PRIMARY KEY,
            sensor_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            people_in INTEGER NOT NULL CHECK (people_in >= 0),
            people_out INTEGER NOT NULL CHECK (people_out >= 0),
            in_corridor INTEGER NOT NULL CHECK (in_corridor >= 0))""")
        last = db.execute("""SELECT in_corridor FROM sensor_readings
                             ORDER BY recorded_at DESC, reading_id DESC LIMIT 1""").fetchone()
        now_inside = last[0] if last else 5
        print("Sensor simulation started. Press Ctrl+C to stop.")
        try:
            while True:
                p_in = random.choice([0, 1, 1, 2])
                p_out = min(random.choice([0, 1, 1, 2]), now_inside + p_in)
                now_inside += p_in - p_out
                db.execute("""INSERT INTO sensor_readings
                              (sensor_id, recorded_at, people_in, people_out, in_corridor)
                              VALUES (?, ?, ?, ?, ?)""",
                           (SENSOR_ID, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            p_in, p_out, now_inside))
                db.commit()
                print(f"in={p_in} out={p_out} now={now_inside}")
                if once:
                    break
                time.sleep(EVERY)
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulated corridor counter")
    parser.add_argument("--once", action="store_true", help="write one reading and exit")
    simulate(once=parser.parse_args().once)
