# -*- coding: utf-8 -*-
"""Ready-made analysis queries for the corridor sensor data (for the defense and for Part C).

Run from the project folder:
    python sensor_queries.py hourly      # average people in the corridor by hour
    python sensor_queries.py peak        # the 5 busiest minutes
    python sensor_queries.py crowded     # share of readings with 12 or more people, by hour
    python sensor_queries.py daily       # average and maximum by day
    python sensor_queries.py latest      # the last 5 readings (shows that there is no personal data)
    python sensor_queries.py subs        # who waits for the "corridor is free" message (student_id only)
    python sensor_queries.py sql "SELECT ..."   # any SELECT query you write yourself
The database is opened read-only, so nothing can be changed by mistake.
"""
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "queue.sqlite"))

QUERIES = {
    "hourly": ("Average number of people by hour (all days)",
               "SELECT strftime('%H', recorded_at) AS hour, ROUND(AVG(in_corridor), 1) AS avg_people, COUNT(*) AS readings "
               "FROM sensor_readings GROUP BY hour ORDER BY hour"),
    "peak": ("The 5 busiest readings",
             "SELECT recorded_at, in_corridor FROM sensor_readings ORDER BY in_corridor DESC, recorded_at DESC LIMIT 5"),
    "crowded": ("Share of readings with 12 or more people, by hour",
                "SELECT strftime('%H', recorded_at) AS hour, ROUND(100.0 * SUM(in_corridor >= 12) / COUNT(*), 1) AS crowded_percent, "
                "COUNT(*) AS readings FROM sensor_readings GROUP BY hour ORDER BY hour"),
    "daily": ("Average and maximum by day",
              "SELECT date(recorded_at) AS day, ROUND(AVG(in_corridor), 1) AS avg_people, MAX(in_corridor) AS max_people, "
              "COUNT(*) AS readings FROM sensor_readings GROUP BY day ORDER BY day"),
    "latest": ("The last 5 readings",
               "SELECT reading_id, sensor_id, recorded_at, people_in, people_out, in_corridor FROM sensor_readings "
               "ORDER BY recorded_at DESC, reading_id DESC LIMIT 5"),
    "subs": ("Students waiting for the 'corridor is free' message (only an id and a time)",
             "SELECT student_id, created_at FROM corridor_subs"),
}


def show(title, sql):
    db = sqlite3.connect("file:" + Path(DB_PATH).as_posix() + "?mode=ro", uri=True)
    try:
        cur = db.execute(sql)
        rows = cur.fetchall()
        head = [d[0] for d in cur.description]
    finally:
        db.close()
    print(f"\n{title}\n{sql}\n")
    if not rows:
        print("(no rows)")
        return
    widths = [max(len(str(x)) for x in [h] + [r[i] for r in rows]) for i, h in enumerate(head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(r, widths)))
    print(f"\n{len(rows)} row(s)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in list(QUERIES) + ["sql"]:
        sys.exit(__doc__)
    if not os.path.exists(DB_PATH):
        sys.exit(f"Database not found: {DB_PATH}")
    if sys.argv[1] == "sql":
        if len(sys.argv) < 3 or not sys.argv[2].lstrip().lower().startswith("select"):
            sys.exit('Give one SELECT query, for example: python sensor_queries.py sql "SELECT COUNT(*) FROM sensor_readings"')
        show("Your query", sys.argv[2])
    else:
        show(*QUERIES[sys.argv[1]])


if __name__ == "__main__":
    try:
        main()
    except sqlite3.Error as e:
        hint = " (start bot.py once: it creates the new tables)" if "no such table" in str(e) else ""
        sys.exit(f"SQL error: {e}{hint}")
