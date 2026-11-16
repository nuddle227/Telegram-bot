# -*- coding: utf-8 -*-
"""Simulated people counter for the corridor near the dean's office (Project Stage 2, Part D3).

It plays the role of the real device (an ESP32 board with two door-frame sensors). Every few seconds it
reports how many people ENTERED and LEFT the corridor. It never sends names, photos or Telegram IDs.

How the numbers travel (the four IoT layers):
    1. sensor     - this script (generates "entered / left")
    2. network    - HTTP POST with an API key to the gateway that runs inside bot.py (127.0.0.1:8765)
    3. processing - the gateway checks the data, calculates "people inside" and writes it into queue.sqlite
    4. application- the Telegram bot shows it to students and staff and sends notifications

If the bot is not running, the script writes straight into the database (fallback), so the demo still works.

Examples (run in a second PowerShell window, in the same folder as bot.py):
    python sensor_sim.py                 # normal day: about 7 people in the corridor
    python sensor_sim.py --mode rush     # a crowd: 15-20 people  -> staff get the "crowded" alert
    python sensor_sim.py --mode quiet    # almost empty           -> students get "the corridor is free"
    python sensor_sim.py --demo          # quiet -> crowd -> quiet automatically (best for the defense)
    python sensor_sim.py --mode auto     # follows the real time of day (busy at 10-12 and 14-16)
    python sensor_sim.py --history 7     # fill the last 7 working days with test data (for charts), then stop
    python sensor_sim.py --reset         # delete all readings of this simulated sensor, then stop
Stop with Ctrl+C. All data produced here is SIMULATED test data (sensor id SIM-01), not real people.
"""
import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("DB_PATH", str(BASE / "queue.sqlite"))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8765/api/v1/readings")
API_KEY = os.environ.get("SENSOR_API_KEY", "demo-sensor-key")     # must be the same as SENSOR_API_KEY in bot.py
FMT = "%Y-%m-%d %H:%M:%S"
MAX_STEP = 20                                                      # the gateway rejects bigger numbers

MODES = {"normal": 7, "rush": 17, "quiet": 2}                      # target number of people for each mode
DEMO_PHASES = [("тихо", 2, 14), ("наплыв", 17, 22), ("тихо", 2, 24)]   # (name, target, readings)
# average number of people in the corridor by hour of a working day (used by --mode auto and --history)
HOUR_PROFILE = {9: 3, 10: 8, 11: 11, 12: 9, 13: 4, 14: 9, 15: 11, 16: 7, 17: 3}
DAY_FACTOR = {0: 1.15, 1: 1.0, 2: 1.0, 3: 1.05, 4: 0.8}            # Monday is busy, Friday is calm

DDL = """CREATE TABLE IF NOT EXISTS sensor_readings (
            reading_id INTEGER PRIMARY KEY, sensor_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
            people_in INTEGER NOT NULL CHECK (people_in >= 0), people_out INTEGER NOT NULL CHECK (people_out >= 0),
            in_corridor INTEGER NOT NULL CHECK (in_corridor >= 0))"""


def poisson(lam):
    """Random whole number with the average 'lam' (Knuth's method, no extra libraries)."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= random.random()
        if p <= limit:
            return k
        k += 1


def step(inside, target):
    """One reading: how many entered and left. People are pulled towards the target number."""
    gap = target - inside
    lam_in = max(0.15, 0.8 + 0.3 * gap)
    lam_out = max(0.15, 0.8 - 0.3 * gap)
    p_in = min(poisson(lam_in), MAX_STEP)
    p_out = min(poisson(lam_out), inside + p_in, MAX_STEP)
    return p_in, p_out


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute(DDL)
    return db


def send_http(sensor_id, p_in, p_out):
    """Network layer. Returns the number of people inside calculated by the gateway, or None if the bot is not running."""
    body = json.dumps({"sensor_id": sensor_id, "people_in": p_in, "people_out": p_out}).encode("utf-8")
    req = urllib.request.Request(GATEWAY_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Api-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))["in_corridor"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("Шлюз отклонил ключ (401). Ключ SENSOR_API_KEY у датчика и у бота должен быть одинаковым.")
        print(f"  ! шлюз ответил ошибкой {e.code}: {e.read().decode('utf-8', 'replace')[:120]}")
        return None
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def write_direct(sensor_id, p_in, p_out, inside):
    db = connect()
    try:
        db.execute("INSERT INTO sensor_readings(sensor_id, recorded_at, people_in, people_out, in_corridor) VALUES (?,?,?,?,?)",
                   (sensor_id, datetime.now().strftime(FMT), p_in, p_out, inside))
        db.commit()
    finally:
        db.close()


def last_inside(sensor_id):
    """Resume after a restart: the last number of people of this sensor today (0 if none)."""
    db = connect()
    try:
        r = db.execute("SELECT in_corridor FROM sensor_readings WHERE sensor_id = ? AND recorded_at >= ? "
                       "ORDER BY recorded_at DESC, reading_id DESC LIMIT 1",
                       (sensor_id, date.today().isoformat() + " 00:00:00")).fetchone()
    finally:
        db.close()
    return r[0] if r else 0


def auto_target(dt):
    base = HOUR_PROFILE.get(dt.hour, 1 if dt.weekday() < 5 else 0)
    return max(0, round(base * DAY_FACTOR.get(dt.weekday(), 0.5)))


def target_for(args, tick):
    """Returns (target number of people, label shown in the console)."""
    if args.demo:
        total = sum(n for _, _, n in DEMO_PHASES)
        k = tick % total
        for name, target, n in DEMO_PHASES:
            if k < n:
                return target, name
            k -= n
    if args.mode == "auto":
        return auto_target(datetime.now()), "по времени"
    return MODES[args.mode], args.mode


def run_live(args):
    inside = last_inside(args.id)
    way = "прямо в базу" if args.direct else "через бота (HTTP-шлюз), запасной путь — база"
    print(f"Датчик {args.id} запущен. Данные идут: {way}.")
    print("Остановить: Ctrl+C. Это тестовые (смоделированные) данные.\n")
    tick = 0
    while args.count is None or tick < args.count:
        target, label = target_for(args, tick)
        p_in, p_out = step(inside, target)
        got = None if args.direct else send_http(args.id, p_in, p_out)
        if got is not None:
            inside, via = got, "шлюз"
        else:
            inside = max(0, inside + p_in - p_out)
            write_direct(args.id, p_in, p_out, inside)
            via = "база" if args.direct else "база (бот не отвечает)"
        print(f"{datetime.now():%H:%M:%S}  [{label:<8}] вошло {p_in}, вышло {p_out} -> в коридоре {inside:>2}   ({via})", flush=True)
        tick += 1
        if args.count is None or tick < args.count:
            time.sleep(args.every)


def run_history(args):
    """Fill the last N working days with one reading per minute, 09:00-18:00 (simulated, for charts and tests)."""
    days = min(max(args.history, 1), 30)                 # the bot deletes readings older than 30 days
    rows = []
    d = date.today() - timedelta(days=days)
    while d < date.today():
        if d.weekday() < 5:
            factor = DAY_FACTOR[d.weekday()] * random.uniform(0.85, 1.15)
            inside = 0
            for minute in range(9 * 60, 18 * 60):
                hour = minute // 60
                target = max(0, round(HOUR_PROFILE.get(hour, 1) * factor))
                p_in, p_out = step(inside, target)
                inside = max(0, inside + p_in - p_out)
                stamp = datetime(d.year, d.month, d.day, hour, minute % 60, random.randint(0, 59))
                rows.append((args.id, stamp.strftime(FMT), p_in, p_out, inside))
        d += timedelta(days=1)
    start = (date.today() - timedelta(days=days)).isoformat() + " 00:00:00"
    db = connect()
    try:
        db.execute("DELETE FROM sensor_readings WHERE sensor_id = ? AND recorded_at >= ? AND recorded_at < ?",
                   (args.id, start, date.today().isoformat() + " 00:00:00"))
        db.executemany("INSERT INTO sensor_readings(sensor_id, recorded_at, people_in, people_out, in_corridor) VALUES (?,?,?,?,?)", rows)
        db.commit()
    finally:
        db.close()
    print(f"Готово: добавлено {len(rows)} смоделированных замеров за {days} дн. (датчик {args.id}).")


def run_reset(args):
    db = connect()
    try:
        n = db.execute("DELETE FROM sensor_readings WHERE sensor_id = ?", (args.id,)).rowcount
        db.commit()
    finally:
        db.close()
    print(f"Удалено замеров датчика {args.id}: {n}.")


def main():
    ap = argparse.ArgumentParser(description="Симулятор датчика, считающего людей в коридоре деканата")
    ap.add_argument("--mode", choices=["normal", "rush", "quiet", "auto"], default="normal", help="сколько людей в коридоре")
    ap.add_argument("--demo", action="store_true", help="тихо -> наплыв -> тихо по кругу (для защиты)")
    ap.add_argument("--every", type=float, default=3.0, help="пауза между замерами, секунд (по умолчанию 3)")
    ap.add_argument("--id", default="SIM-01", help="имя датчика (по умолчанию SIM-01)")
    ap.add_argument("--direct", action="store_true", help="писать сразу в базу, минуя бота")
    ap.add_argument("--count", type=int, default=None, help="остановиться после N замеров")
    ap.add_argument("--history", type=int, metavar="DAYS", help="заполнить последние DAYS рабочих дней тестовыми данными и выйти")
    ap.add_argument("--reset", action="store_true", help="удалить все замеры этого датчика и выйти")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        sys.exit(f"База данных не найдена: {DB_PATH}. Положите queue.sqlite рядом с sensor_sim.py.")
    try:
        if args.reset:
            run_reset(args)
        elif args.history:
            run_history(args)
        else:
            run_live(args)
    except KeyboardInterrupt:
        print("\nДатчик остановлен.")


if __name__ == "__main__":
    main()
