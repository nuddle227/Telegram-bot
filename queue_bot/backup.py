"""Makes a backup of queue.sqlite: a file copy (SQLite backup API) and an SQL dump."""
import os, sqlite3
from datetime import datetime

SRC = os.environ.get("DB_PATH", "queue.sqlite")
os.makedirs("backup", exist_ok=True)
stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
con = sqlite3.connect(SRC)

copy = sqlite3.connect(f"backup/queue_{stamp}.sqlite")
con.backup(copy)
copy.close()
print(f"Backup done: backup/queue_{stamp}.sqlite")

with open(f"backup/queue_{stamp}.sql", "w") as f:
    for line in con.iterdump():
        f.write(line + "\n")
print(f"Backup done: backup/queue_{stamp}.sql")
