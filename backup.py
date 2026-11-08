"""Makes a backup of queue.sqlite: a file copy (SQLite backup API) and an SQL dump."""
import os, sqlite3
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
SRC = os.environ.get("DB_PATH", str(BASE / "queue.sqlite"))
if not Path(SRC).is_file():
    raise SystemExit("Database not found. Run python make_test_data.py first.")
BACKUP_DIR = BASE / "backup"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
con = sqlite3.connect(SRC)

copy = sqlite3.connect(BACKUP_DIR / f"queue_{stamp}.sqlite")
con.backup(copy)
copy.close()
print(f"Backup done: {BACKUP_DIR / f'queue_{stamp}.sqlite'}")

with (BACKUP_DIR / f"queue_{stamp}.sql").open("w", encoding="utf-8") as f:
    for line in con.iterdump():
        f.write(line + "\n")
print(f"Backup done: {BACKUP_DIR / f'queue_{stamp}.sql'}")
