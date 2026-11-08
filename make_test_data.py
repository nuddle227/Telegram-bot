"""Creates queue.sqlite from schema.sql and fills it with test data (all names are invented)."""
import os, random, sqlite3, hashlib, secrets
from pathlib import Path
from datetime import date, datetime, timedelta

BASE = Path(__file__).resolve().parent
DB = os.environ.get("DB_PATH", str(BASE / "queue.sqlite"))
PIN_ROUNDS = 600_000  # matches the final bot; legacy hashes keep their own pin_rounds
random.seed(2619)

def hash_pin(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), PIN_ROUNDS).hex()

first_f = ["Aigerim","Dana","Madina","Aruzhan","Aliya","Zhansaya","Assel","Dinara","Kamila","Nazerke","Saltanat","Aizhan","Symbat","Gulnaz","Alina","Karina","Milana","Yelena","Anna","Sofiya"]
first_m = ["Aldiyar","Nurtore","Dias","Alikhan","Yerlan","Bauyrzhan","Arman","Temirlan","Ruslan","Nursultan","Daniyar","Miras","Islam","Timur","Maksim","Artem","Kanat","Rakhat","Adilet","Sanzhar"]
last = ["Akhmetov","Sadykov","Nurlanov","Kassymov","Omarov","Serikov","Bekov","Zhumabayev","Tulegenov","Abenov","Mukhametov","Ivanov","Petrov","Smagulov","Dosov","Kenzhebayev","Ospanov","Utepov","Baimukhanov","Sultanov"]
groups = ["SE-2619","SE-2618","SE-2617","IT-2601","IT-2602","CS-2605","CS-2606","DS-2603","CY-2604","MT-2607"]
services = [("Certificate","C",5),("Retake permit","R",4),("Application","A",6),("Other","O",5)]
staff = [("Gulmira Bekenova","administrator"),("Saule Ibrayeva","operator"),("Marat Zhaksylykov","operator"),
         ("Botagoz Nurgaliyeva","operator"),("Erlan Mukashev","operator"),("Lyazzat Abdrakhmanova","operator"),
         ("Samal Toktarova","operator"),("Oleg Vasilyev","operator"),
         ("Aizhan Kuanyshbekova","operator"),("Timur Sarsenov","operator")]
staff_srv = {1:[1,2,3,4],2:[1],3:[1,3],4:[2],5:[2,4],6:[3],7:[3,4],8:[1,4],9:[2,3],10:[1,2]}
fixed_pins = {1:"482915", 2:"736204"}

if os.path.exists(DB): os.remove(DB)
db = sqlite3.connect(DB)
db.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
db.executemany("INSERT INTO services(name,prefix,avg_minutes) VALUES (?,?,?)", services)

# students
names = set()
while len(names) < 60:
    if random.random() < .5: n = random.choice(first_f)+" "+random.choice(last)+"a"
    else: n = random.choice(first_m)+" "+random.choice(last)
    names.add(n)
now = datetime.now().replace(microsecond=0)
tg_ids = random.sample(range(500_000_000, 999_999_999), 60)
for i,(n,tg) in enumerate(zip(sorted(names, key=lambda x: random.random()), tg_ids)):
    reg = (now - timedelta(days=random.randint(14,40), hours=random.randint(0,9))).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("INSERT INTO students(telegram_id,full_name,group_name,registered_at) VALUES (?,?,?,?)",
               (tg, n, random.choice(groups), reg))

# staff
for sid,(n,role) in enumerate(staff, start=1):
    pin = fixed_pins.get(sid, f"{secrets.randbelow(900000)+100000}")
    salt = secrets.token_hex(16)
    db.execute("INSERT INTO staff(telegram_id,full_name,role,pin_salt,pin_hash,pin_rounds) VALUES (?,?,?,?,?,?)",
               (100_000_000+sid*7919, n, role, salt, hash_pin(pin, salt), PIN_ROUNDS))
for sid, srvs in staff_srv.items():
    for s in srvs: db.execute("INSERT INTO staff_services VALUES (?,?)", (sid, s))

# history: 129 tickets over the last working days
today = date.today()
days = []
d = today - timedelta(days=1)
while len(days) < 10:
    if d.weekday() < 5: days.append(d)
    d -= timedelta(days=1)
days.sort()
weights = [4,2,3,1]      # certificate is the most frequent service (survey Q9)
hist_target = 129
per_day = [13,13,13,13,13,13,13,13,13,12]
tickets = []
for day, cnt in zip(days, per_day):
    counters = {1:0,2:0,3:0,4:0}
    t = datetime.combine(day, datetime.min.time()).replace(hour=10, minute=5)
    for _ in range(cnt):
        t += timedelta(minutes=random.randint(8,28))
        s = random.choices([1,2,3,4], weights)[0]
        counters[s] += 1
        tickets.append((day, s, counters[s], t))
students_ids = list(range(1,61))
def staff_for(s): return random.choice([k for k,v in staff_srv.items() if s in v])
for day, s, num, created in tickets:
    r = random.random()
    st = "done" if r < .80 else ("skipped" if r < .92 else "cancelled")
    sid = random.choice(students_ids)
    cr = created.strftime("%Y-%m-%d %H:%M:%S")
    called = finished = staff_id = None
    if st != "cancelled":
        c = created + timedelta(minutes=random.randint(6,35))
        called = c.strftime("%Y-%m-%d %H:%M:%S"); staff_id = staff_for(s)
        if st == "done": finished = (c + timedelta(minutes=random.randint(3,9))).strftime("%Y-%m-%d %H:%M:%S")
    notified = 1 if (st != "cancelled" and random.random() < .85) else 0
    db.execute("""INSERT INTO tickets(student_id,service_id,staff_id,queue_date,number,status,created_at,called_at,finished_at,notified)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""", (sid,s,staff_id,day.isoformat(),num,st,cr,called,finished,notified))

# today's live queue (21 tickets): (service, done, called, waiting)
live = {1:(3,1,5), 2:(1,1,2), 3:(2,1,3), 4:(1,0,1)}
pool = students_ids[:]; random.shuffle(pool)
base = datetime.combine(today, datetime.min.time()).replace(hour=10, minute=0)
n_live = 0
for s,(nd,nc,nw) in live.items():
    num = 0
    for status in ["done"]*nd + ["called"]*nc + ["waiting"]*nw:
        num += 1; n_live += 1
        created = now - timedelta(minutes=30 + (22 - n_live) * 3)
        called = finished = staff_id = None
        if status != "waiting":
            c = created + timedelta(minutes=10)
            called = c.strftime("%Y-%m-%d %H:%M:%S"); staff_id = staff_for(s)
            if status == "done": finished = (c + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("""INSERT INTO tickets(student_id,service_id,staff_id,queue_date,number,status,created_at,called_at,finished_at,notified)
                      VALUES (?,?,?,?,?,?,?,?,?,0)""",
                   (pool.pop(), s, staff_id, today.isoformat(), num, status, created.strftime("%Y-%m-%d %H:%M:%S"), called, finished))

# audit log: one row per call / skip / complete of the ticket history (60 rows), plus 6 refused attempts
rows = db.execute("""SELECT ticket_id, staff_id, status, called_at, finished_at FROM tickets
                     WHERE staff_id IS NOT NULL ORDER BY called_at DESC LIMIT 54""").fetchall()
log = []
for tid, stf, st, c, f in rows:
    act = "skip" if st == "skipped" else ("complete" if st == "done" else "call")
    log.append((stf, tid, act, f or c))
for i in range(6):
    log.append((None, None, "denied", (now - timedelta(hours=random.randint(1,200))).strftime("%Y-%m-%d %H:%M:%S")))
log.sort(key=lambda x: x[3])
db.executemany("INSERT INTO audit_log(staff_id,ticket_id,action,created_at) VALUES (?,?,?,?)", log)
db.commit()
for t in ["students","staff","services","staff_services","tickets","audit_log"]:
    print(t, db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
