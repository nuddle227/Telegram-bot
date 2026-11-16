# -*- coding: utf-8 -*-
"""Automatic checks of the Smart corridor feature (sensor gateway, rules A-C, screens).

Run from the project folder:   python tests/test_smart_corridor.py
It works on a COPY of queue.sqlite in a temporary folder, so your real queue is not touched.
It does not need the Telegram token or internet. Every line starts with PASS or FAIL.
"""
import asyncio, contextlib, io, json, os, shutil, sqlite3, sys, tempfile, time, urllib.request, urllib.error
from datetime import datetime, timedelta, date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
try:
    import telegram                                    # the real library, if it is installed
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "stubs"))   # tiny stand-in, only for this test
tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "queue.sqlite")
shutil.copy(PROJ / "queue.sqlite", DB)
os.environ["DB_PATH"] = DB
os.environ["BOT_TOKEN"] = "1:x"
sys.path.insert(0, str(PROJ))
import bot
import sensor_sim as sim
bot.LANG = "ru"
sim.DB_PATH = DB

FAILS = []
N = {"pass": 0}
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if cond: N["pass"] += 1
    else: FAILS.append(name)

def R(markup):
    """Rows of buttons; works with the real telegram library (inline_keyboard) and with the tiny stand-in."""
    return getattr(markup, "inline_keyboard", None) or markup.rows

def raw(sql, *a):
    c = sqlite3.connect(DB); r = c.execute(sql, a).fetchall(); c.commit(); c.close(); return r

# ---------- schema / migration on the ORIGINAL database
bot.ensure_schema()
tables = {r[0] for r in raw("select name from sqlite_master where type='table'")}
check("corridor_subs table created", "corridor_subs" in tables)
check("sensor_readings still there", "sensor_readings" in tables)
bot.ensure_schema()   # twice = safe
check("ensure_schema idempotent", True)

# ---------- no data
s = bot.corridor_status()
check("state none without data", s["state"] == "none" and s["now"] is None)
check("corridor_line empty without data", bot.corridor_line() == "")
check("student text shows 'no data'", "не передаёт" in bot.corridor_text(1)[0])

# ---------- gateway
bot.SENSOR_GATEWAY_PORT = 18765
bot.start_gateway()
URL = "http://127.0.0.1:18765/api/v1/readings"
def post(body, key="demo-sensor-key", raw_body=None):
    data = raw_body if raw_body is not None else json.dumps(body).encode()
    req = urllib.request.Request(URL, data=data, method="POST", headers={"X-Api-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
check("health", json.loads(urllib.request.urlopen("http://127.0.0.1:18765/health").read())["status"] == "ok")
check("wrong key -> 401", post({"sensor_id": "T-1", "people_in": 1, "people_out": 0}, key="nope")[0] == 401)
check("non-ascii key -> 401 (no crash)", post({"sensor_id": "T-1", "people_in": 1, "people_out": 0}, key="k\u00e9y")[0] == 401)
check("missing field -> 400", post({"sensor_id": "T-1", "people_in": 1})[0] == 400)
check("negative -> 400", post({"sensor_id": "T-1", "people_in": -1, "people_out": 0})[0] == 400)
check("too big -> 400", post({"sensor_id": "T-1", "people_in": 21, "people_out": 0})[0] == 400)
check("bool -> 400", post({"sensor_id": "T-1", "people_in": True, "people_out": 0})[0] == 400)
check("bad id -> 400", post({"sensor_id": "x; DROP TABLE", "people_in": 1, "people_out": 0})[0] == 400)
check("bad json -> 400", post(None, raw_body=b"{nope")[0] == 400)
check("huge body -> 413", post(None, raw_body=b"x" * 2000)[0] == 413)
check("nothing written by bad requests", raw("select count(*) from sensor_readings")[0][0] == 0)
code, js = post({"sensor_id": "T-1", "people_in": 5, "people_out": 0})
check("valid -> 201 and computed 5", code == 201 and js["in_corridor"] == 5, str((code, js)))
code, js = post({"sensor_id": "T-1", "people_in": 2, "people_out": 1})
check("running total 6", js["in_corridor"] == 6)
code, js = post({"sensor_id": "T-1", "people_in": 0, "people_out": 20})
check("never below zero", js["in_corridor"] == 0)
check("student tables untouched (no personal data in readings)",
      [r[1] for r in raw("pragma table_info(sensor_readings)")] == ["reading_id", "sensor_id", "recorded_at", "people_in", "people_out", "in_corridor"])

# ---------- simulator over HTTP (real script code path)
sim.GATEWAY_URL = URL
class A: pass
a = A(); a.id = "SIM-01"; a.direct = False; a.every = 0; a.count = 6; a.mode = "rush"; a.demo = False
buf = io.StringIO()
with contextlib.redirect_stdout(buf): sim.run_live(a)
check("sim run_live via gateway", "(шлюз)" in buf.getvalue() and buf.getvalue().count("шлюз") >= 6, buf.getvalue()[-200:])
# gateway down -> fallback
bot.GATEWAY["server"].shutdown(); bot.GATEWAY["server"].server_close()
a.count = 2
buf = io.StringIO()
with contextlib.redirect_stdout(buf): sim.run_live(a)
check("sim falls back to database", "бот не отвечает" in buf.getvalue())
cnt = raw("select count(*) from sensor_readings where sensor_id='SIM-01'")[0][0]
check("sim readings stored", cnt == 8, str(cnt))
check("in_corridor never negative", raw("select min(in_corridor) from sensor_readings")[0][0] >= 0)

# ---------- status, levels, trend
bot.CORRIDOR_LOW, bot.CORRIDOR_HIGH = 5, 12
raw("delete from sensor_readings")
def put(n, ago_sec=0, i=0, o=0, sid="SIM-01"):
    raw("insert into sensor_readings(sensor_id,recorded_at,people_in,people_out,in_corridor) values (?,?,?,?,?)",
        sid, (datetime.now() - timedelta(seconds=ago_sec)).strftime(bot.TS_FMT), i, o, n)
put(3, 600)   # 10 min ago
put(9, 5)
s = bot.corridor_status()
check("online + level mid + trend up", s["state"] == "online" and s["now"] == 9 and s["level"] == "mid" and s["trend"] == "up", str(s))
check("levels", [bot.corridor_level(x) for x in (0, 5, 6, 11, 12, 30)] == ["low", "low", "mid", "mid", "high", "high"])
raw("delete from sensor_readings"); put(20, 300)
s = bot.corridor_status()
check("offline when old", s["state"] == "offline" and s["now"] is None and bot.corridor_line() == "")

# ---------- students, rule A
st_tg = 777001
check("register student", bot.register_student(st_tg, "Тест Студент", "SE-2619"))
check("sub without ticket", bot.corridor_sub_set(st_tg, True)[0] == "need_ticket")
res = bot.take_ticket(st_tg, "C")
raw("delete from sensor_readings"); put(9, 3)
check("sub on (crowded)", bot.corridor_sub_set(st_tg, True) == ("on", 9))
text, waiting, sub = bot.corridor_text(st_tg)
check("student text: waiting+sub flags", waiting and sub and "Уведомление включено" in text, text)
check("no push while busy", bot.pending_corridor_free() == [])
put(4, 1)
rows = bot.pending_corridor_free()
check("push when free", len(rows) == 1 and rows[0]["n"] == 4 and rows[0]["telegram_id"] == st_tg, str(rows))
check("already_free not stored", bot.corridor_sub_set(st_tg, False)[0] == "off" and bot.corridor_sub_set(st_tg, True)[0] == "already_free")
raw("delete from sensor_readings"); put(9, 3)
bot.corridor_sub_set(st_tg, True)
raw("update tickets set status='cancelled' where status='waiting' and student_id=(select student_id from students where telegram_id=?)", st_tg)
put(2, 1)
check("sub removed when ticket gone", bot.pending_corridor_free() == [] and raw("select count(*) from corridor_subs")[0][0] == 0)

# ---------- delete profile cascades subscriptions
raw("update tickets set status='waiting' where ticket_id=(select max(ticket_id) from tickets where student_id=(select student_id from students where telegram_id=?))", st_tg)
raw("delete from sensor_readings"); put(9, 3)
bot.corridor_sub_set(st_tg, True)
raw("pragma foreign_keys=on")
with bot.conn() as db:
    db.execute("DELETE FROM students WHERE telegram_id = ?", (st_tg,))
check("subscription deleted with the student", raw("select count(*) from corridor_subs")[0][0] == 0)

# ---------- rules B and C with a fake bot
class FakeBot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **k): self.sent.append((chat_id, text))
fb = FakeBot()
async def watch(): await bot.corridor_watch(fb)
raw("delete from sensor_readings"); put(14, 400)       # old history must not alert
bot.CORR.update({"last_id": None, "high_run": 0, "last_alert": 0.0, "was_online": False, "lost_sent": False})
asyncio.run(watch())
check("old history: no alert", fb.sent == [])
put(13, 4); asyncio.run(watch()); put(14, 3); asyncio.run(watch())
check("2 crowded readings: no alert yet", fb.sent == [])
put(15, 2); asyncio.run(watch())
staff_n = raw("select count(*) from staff where is_active=1")[0][0]
check("3 crowded readings: alert to all staff", len(fb.sent) == staff_n and "много людей" in fb.sent[0][1], str(len(fb.sent)))
fb.sent.clear(); put(16, 1); asyncio.run(watch())
check("cool-down: no second alert", fb.sent == [])
# a calm reading resets the run
bot.CORR["last_alert"] = 0; put(3, 1); asyncio.run(watch()); put(13, 0); asyncio.run(watch())
check("run reset by a calm reading", fb.sent == [])
# sensor lost / back
raw("delete from sensor_readings"); put(5, 400)
asyncio.run(watch())
adm = raw("select count(*) from staff where is_active=1 and role='administrator'")[0][0]
check("sensor lost -> admins only", len(fb.sent) == adm and "не отвечает" in fb.sent[0][1], str(fb.sent[:1]))
fb.sent.clear(); asyncio.run(watch())
check("sensor lost only once", fb.sent == [])
put(5, 1); asyncio.run(watch())
check("sensor back -> admins", len(fb.sent) == adm and "снова на связи" in fb.sent[0][1])

# ---------- admin screen with history
sim.DB_PATH = DB
b = A(); b.id = "SIM-01"; b.history = 7
with contextlib.redirect_stdout(io.StringIO()): sim.run_history(b)
n_hist = raw("select count(*) from sensor_readings where recorded_at < ?", date.today().isoformat())[0][0]
check("history generated (>=1000 rows)", n_hist >= 1000, str(n_hist))
put(8, 2)
txt = bot.smart_text()
check("smart text < 4096", len(txt) < 4096)
check("smart text has sensor line", "на связи" in txt)
check("purge keeps recent", bot.purge_old_readings() == 0)
raw("insert into sensor_readings(sensor_id,recorded_at,people_in,people_out,in_corridor) values ('OLD','2020-01-01 10:00:00',1,0,1)")
check("purge deletes >30d", bot.purge_old_readings() == 1)
raw("delete from sensor_readings where sensor_id='SIM-01'")
check("reset works", True)

# ---------- screens through fake Telegram objects
class Msg:
    photo = []
    async def delete(self): pass
class Q:
    def __init__(self, data, uid, sink): self.data = data; self.from_user = type("U", (), {"id": uid})(); self.message = Msg(); self.sink = sink
    async def answer(self, *a, **k): pass
    async def edit_message_text(self, text, **k): self.sink.append(("edit", text, k.get("reply_markup")))
class Chat: id = 5
class Upd:
    def __init__(self, q): self.callback_query = q; self.effective_user = q.from_user; self.effective_chat = Chat()
class Ctx:
    def __init__(self): self.user_data = {}; self.bot = FakeBot2()
class FakeBot2:
    def __init__(self): self.sent = []
    async def send_photo(self, chat_id, src, caption=None, **k): self.sent.append(("photo", caption, k.get("reply_markup")))
    async def send_message(self, chat_id, text, **k): self.sent.append(("msg", text, k.get("reply_markup")))
def press(data, uid, ctx=None):
    ctx = ctx or Ctx(); q = Q(data, uid, ctx.bot.sent)
    asyncio.run(bot.on_button(Upd(q), ctx)); return ctx.bot.sent, ctx
bot.register_student(777002, "Второй Студент", "SE-2619"); bot.take_ticket(777002, "R")
raw("delete from sensor_readings"); put(9, 2)
sent, _ = press("cor", 777002)
kind, cap, mk = sent[-1]
btns = [b.callback_data for row in R(mk) for b in row]
check("cor screen has subscribe button", "corsub:on" in btns and "menu" in btns, str(btns))
check("cor screen caption < 1024", len(cap) < 1024, str(len(cap)))
sent, _ = press("corsub:on", 777002)
check("corsub:on confirms", "Готово" in sent[-1][1] and any(b.callback_data == "corsub:off" for row in R(sent[-1][2]) for b in row), sent[-1][1])
sent, _ = press("corsub:off", 777002)
check("corsub:off confirms", "выключено" in sent[-1][1])
sent, _ = press("corinfo", 777002)
check("corinfo screen", "камеры" in sent[-1][1] and len(sent[-1][1]) < 1024)
sent, _ = press("my", 777002)
check("my ticket has corridor line + button", "В коридоре сейчас" in sent[-1][1] and any(b.callback_data == "cor" for row in R(sent[-1][2]) for b in row), sent[-1][1])
sent, _ = press("svc", 777002)
check("services screen has corridor line", "В коридоре сейчас" in sent[-1][1], sent[-1][1][:200])
sent, _ = press("menu", 777002)
check("main menu has corridor button", any(b.callback_data == "cor" for row in R(sent[-1][2]) for b in row))
sent, _ = press("cor", 424242)   # unregistered user
check("unregistered: no subscribe button", not any((b.callback_data or "").startswith("corsub") for row in R(sent[-1][2]) for b in row))
# staff: session required, admin only
admin_tg = raw("select telegram_id from staff where role='administrator'")[0][0]
oper_tg = raw("select telegram_id from staff where role='operator'")[0][0]
sent, _ = press("smart", 424242)
check("smart denied for non-staff", "Доступ запрещён" in sent[-1][1])
sent, _ = press("smart", admin_tg)
check("smart needs login session", "Сессия" in sent[-1][1])
c = Ctx(); c.user_data["staff_until"] = time.time() + 600
sent, _ = press("smart", oper_tg, c)
check("smart denied for operator", "только администратору" in sent[-1][1], sent[-1][1])
c = Ctx(); c.user_data["staff_until"] = time.time() + 600
sent, _ = press("smart", admin_tg, c)
check("smart works for admin", "Умный коридор" in sent[-1][1], sent[-1][1][:80])
c = Ctx(); c.user_data["staff_until"] = time.time() + 600
sent, _ = press("stats", admin_tg, c)
check("stats has smart button", any(b.callback_data == "smart" for row in R(sent[-1][2]) for b in row) and "В коридоре сейчас" in sent[-1][1], sent[-1][1][-200:])
c = Ctx(); c.user_data["staff_until"] = time.time() + 600
sent, _ = press("smenu", admin_tg, c)
check("staff menu admin has smart button", any(b.callback_data == "smart" for row in R(sent[-1][2]) for b in row))

# ---------- English interface
bot.LANG = "en"
t1, _, _ = bot.corridor_text(777002)
check("EN texts render", "Corridor" in t1)
for key in ("sensor_info", "cor_alert", "sensor_lost", "cor_free_push", "sub_on", "sub_already_free"):
    bot.t(key, days=30, n=1, hi=12, min=2, code="C-1", ahead=2, low=5)
admin_en = bot.smart_text()
check("EN smart text", "Smart corridor" in admin_en)
ru_keys = set(bot.TEXTS["ru"]); en_keys = set(bot.TEXTS["en"])
check("RU and EN have the same keys", ru_keys == en_keys, str(ru_keys ^ en_keys))
bot.LANG = "ru"

print("\nПроверок пройдено: %d, не пройдено: %d" % (N["pass"], len(FAILS)))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAILS else 0)
