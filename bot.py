# -*- coding: utf-8 -*-
"""Digital Queue for the Dean's Office - Telegram bot (Assignment 4, SE-2619).

Uses the same SQLite database (queue.sqlite) and the same rules as the
Assignment 3 prototype (R1 registration, R2 take a ticket, R8 one active ticket,
R12 staff PIN + roles, audit_log). All SQL uses ? placeholders.

Run:  python bot.py        (instructions: README_RU.md)
"""
import asyncio
import hashlib
import hmac
import html
import logging
import math
import os
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

# =====================================================================
#  SETTINGS  -  paste the token from BotFather between the quotes
# =====================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_TOKEN_HERE")   # set the NEW token as an environment variable, never commit it

LANG = "ru"            # "ru" - Russian interface, "en" - English interface
SESSION_MIN = 30       # staff session length (minutes)
MAX_PIN_FAILS = 5      # wrong PINs before a temporary lock (threat #2 in Assignment 3)
LOCK_MIN = 5           # lock time (minutes)
NOTIFY_EVERY = 3       # how often the bot checks the database for new calls (seconds)
NEAR_AHEAD = 3         # "your turn is near" is sent when this many people (or fewer) are ahead
DAILY_LIMIT = 3        # maximum tickets one student can take per day
COUNT_CANCELLED = True # True: a cancelled ticket still counts towards the daily limit
PIN_ROUNDS = 600_000   # PBKDF2 rounds for new PIN hashes (OWASP); old hashes are upgraded at the next login
RATE_MAX = 10          # anti-flood: at most this many taps/messages per user ...
RATE_WINDOW = 10       # ... in this many seconds
ENFORCE_HOURS = False  # True: tickets can be taken only in office hours (R14). False = always open (demo)
OPEN_FROM, OPEN_TO = "09:00", "18:00"
WORKDAYS = (0, 1, 2, 3, 4)   # Monday = 0 ... Friday = 4
CALL_TIMEOUT_MIN = 5   # called student did not come after this time: the operator gets a hint
MAX_SWAPS = 1          # how many times a student may let the next person go first
CLOSE_STALE = True     # in a new day, close tickets left over from earlier days
CORRIDOR_FRESH_SEC = 120   # a people-counter reading older than this is not shown
# =====================================================================

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("DB_PATH", str(BASE / "queue.sqlite"))
IMG_DIR = BASE / "images"
HTML = ParseMode.HTML

# Pictures shown together with the bot messages (optimised JPEG, 1280 px wide).
IMAGES = {
    "team":   "team.jpg",     # welcome screen and main menu
    "notify": "notify.jpg",   # notification "your turn has come"
    "queue":  "queue.jpg",    # tickets, queue, staff panel
    "study":  "study.jpg",    # registration and help
}
ANIM_FILE = "bell.gif"   # animated bell (decorative), sent after the "your turn" notification
ANIM_ID = None           # Telegram file_id cache for the animation
PHOTO_IDS = {}             # Telegram file_id cache: each picture is uploaded only once

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("queue-bot")

# ---------------------------------------------------------------------
#  Texts (every sound/notification has a text equivalent)
# ---------------------------------------------------------------------
TEXTS = {
 "ru": {
  "welcome": "👋 <b>Добро пожаловать в цифровую очередь деканата!</b>\n\nБерите талон прямо в Telegram, следите за очередью и получайте уведомление, когда подойдёт ваш черёд.\n\nВыберите действие кнопками ниже 👇",
  "menu": "🏠 <b>Главное меню</b>\nВыберите действие:",
  "b_take": "🎫 Взять талон", "b_my": "📋 Мой талон", "b_cancel": "❌ Отменить талон",
  "b_help": "ℹ️ Помощь", "b_staff": "👨‍💼 Для сотрудников", "b_menu": "🏠 Меню",
  "b_refresh": "🔄 Обновить", "b_back": "◀ Назад", "b_agree": "✅ Согласен", "b_decline": "✖ Не согласен",
  "b_yes_cancel": "✅ Да, отменить", "b_no": "◀ Нет",
  "choose": "🎫 <b>Выберите услугу</b>\nВ конце — сколько человек ждут сейчас:",
  "privacy": "🔒 <b>Регистрация</b>\n\nЧтобы взять талон, нужно один раз зарегистрироваться. Мы сохраняем только ФИО, группу и Telegram ID — они нужны только для очереди (Закон РК № 94-V «О персональных данных и их защите»). Данные видят только сотрудники деканата. Профиль можно удалить по запросу.\n\nВы согласны?",
  "declined": "Хорошо. Без регистрации талон взять нельзя, но вы можете вернуться в любой момент.",
  "ask_name": "✍️ Введите ваше <b>ФИО</b> (например: Айгерим Садыкова):",
  "bad_name": "⚠️ ФИО должно быть от 2 до 60 символов. Попробуйте ещё раз:",
  "ask_group": "✍️ Теперь введите вашу <b>группу</b> (например: SE-2619):",
  "bad_group": "⚠️ Группа должна быть от 3 до 12 символов. Попробуйте ещё раз:",
  "registered": "✅ <b>Готово!</b> {name} ({group}), вы зарегистрированы.\nТеперь можно взять талон.",
  "reg_exists": "Вы уже зарегистрированы. Выберите услугу:",
  "reg_fail": "⚠️ Не удалось зарегистрироваться (возможно, вы уже есть в базе). Нажмите «Взять талон».",
  "created": "🎫 <b>Талон создан!</b>",
  "ticket": "Талон: <b>{code}</b> ({service})\nПозиция в очереди: {pos}\nПеред вами: {ahead}\nПримерное ожидание: около {wait} мин\nСейчас обслуживается: {now}",
  "nobody": "пока никто",
  "near": "⏰ <b>Ваша очередь скоро!</b>",
  "near_msg": "⏰ <b>Ваша очередь скоро!</b>\nТалон {code}, перед вами: {ahead}. Подходите к деканату.",
  "called": "🔔 <b>Ваша очередь!</b>\nТалон <b>{code}</b> ({service}).\nПожалуйста, подойдите в деканат.",
  "no_ticket": "У вас нет активного талона. Нажмите «Взять талон».",
  "already": "⚠️ У вас уже есть активный талон. Сначала отмените его (правило: один активный талон на студента).",
  "limit": "⚠️ <b>Лимит талонов</b>\nВ день можно взять не более {n} талонов, и сегодня вы уже взяли все {n}. Попробуйте завтра.",
  "left": "Осталось талонов на сегодня: {left} из {n}.",
  "closed": "🕒 Сейчас деканат не работает. Часы работы: {hours}. Талон можно взять в рабочее время.",
  "b_hist": "📜 История", "b_delete": "🗑 Удалить мои данные", "b_later": "⏭ Пропустить вперёд", "b_yes_delete": "✅ Да, удалить",
  "hist": "📜 <b>Ваши талоны за сегодня</b>\n\n{lines}\n\n{left}",
  "hist_empty": "Сегодня талонов ещё нет.",
  "st": {"waiting": "ждёт", "called": "вызван", "done": "обслужен", "skipped": "пропущен", "cancelled": "отменён"},
  "delete_confirm": "🗑 <b>Удалить профиль?</b>\nБудут удалены ФИО, группа и Telegram ID. Записи о талонах останутся без привязки к вам. Действие нельзя отменить.",
  "deleted": "✅ Ваши данные удалены. Чтобы снова пользоваться ботом, зарегистрируйтесь заново.",
  "delete_later": "⚠️ Сегодня вы уже брали талоны, поэтому профиль можно удалить завтра (это защита от обхода лимита талонов).",
  "slow": "⏳ Слишком быстро. Подождите несколько секунд.",
  "later_ok": "⏭ Вы пропустили вперёд следующего студента. Ваш новый талон: {code}.",
  "later_no": "Пропустить вперёд нельзя: за вами никого нет или вы уже использовали эту возможность.",
  "timeout_hint": "⏰ {code} вызван {min} мин назад и не подошёл. Нажмите «Пропустить» в панели услуги.",
  "stats_extra": "\n\nПо часам (за всё время):\n{hours}\n\nСреднее ожидание, мин:\n{wait}\n\nПотеряно (пропущено или отменено): {lost}",
  "corridor": "👥 В коридоре сейчас: {n}",
  "cancel_confirm": "Отменить талон <b>{code}</b>?",
  "cancelled": "✅ Талон отменён.",
  "cant_cancel": "Отменить нечего: талона нет или вас уже вызвали.",
  "help": "ℹ️ <b>Как пользоваться ботом</b>\n\n1. Нажмите «Взять талон» и выберите услугу.\n2. Следите за позицией в разделе «Мой талон».\n3. Когда подойдёт очередь, бот пришлёт уведомление 🔔 — заходите в деканат.\n4. Передумали? Нажмите «Отменить талон».\n\nВ день можно взять не более {n} талонов. Часы работы: {hours}.\nВсе уведомления дублируются текстом, звук не обязателен.",
  "denied": "⛔ Доступ запрещён: функции сотрудников только для одобренных сотрудников.",
  "ask_pin": "🔐 Введите ваш <b>PIN</b> сотрудника. Сообщение с PIN бот сразу удалит.",
  "pin_wrong": "❌ Неверный PIN. Осталось попыток: {left}.",
  "pin_locked": "⛔ Слишком много неверных попыток. Повторите через {min} мин.",
  "staff_ok": "✅ Вход выполнен.",
  "staff_menu": "👨‍💼 <b>Панель сотрудника</b>\n{name} — {role}\nВыберите услугу:",
  "expired": "⌛ Сессия сотрудника истекла. Войдите снова.",
  "b_login": "🔐 Войти",
  "panel": "📋 <b>{service} ({prefix})</b> — в очереди: {n}",
  "empty": "Очередь пуста.",
  "serving": "СЕЙЧАС", "waiting": "ждёт", "more": "… и ещё {n}",
  "b_call": "📣 Вызвать следующего", "b_done": "✅ Завершить", "b_skip": "⏭ Пропустить",
  "b_stats": "📊 Статистика", "b_logout": "🚪 Выйти",
  "called_ok": "📣 Вызван {code}. Студент получил уведомление.",
  "busy": "⚠️ {code} ещё обслуживается. Сначала завершите или пропустите.",
  "nobody_now": "⚠️ Сейчас никто не обслуживается.",
  "empty_q": "⚠️ Очередь пуста.",
  "marked_done": "✅ {code} завершён.", "marked_skipped": "⏭ {code} пропущен.",
  "no_service": "⛔ Вы не работаете с этой услугой.",
  "admin_only": "⛔ Статистика доступна только администратору.",
  "stats": "📊 <b>Статистика</b>\n\nЗавершённые талоны по дням:\n{days}\n\nСейчас ждут:\n{waiting}",
  "logged_out": "Вы вышли из панели сотрудника.",
  "myid": "Ваш Telegram ID: <code>{id}</code>",
  "use_buttons": "Пожалуйста, пользуйтесь кнопками 👇",
  "error": "⚠️ Что-то пошло не так. Нажмите «Меню» и попробуйте ещё раз.",
  "svc": {"C": "Справка", "R": "Пересдача", "A": "Заявление", "O": "Другое"},
  "role": {"operator": "оператор", "administrator": "администратор"},
 },
 "en": {
  "welcome": "👋 <b>Welcome to the Dean's Office digital queue!</b>\n\nTake a ticket in Telegram, follow your position and get a notification when it is your turn.\n\nChoose an action with the buttons below 👇",
  "menu": "🏠 <b>Main menu</b>\nChoose an action:",
  "b_take": "🎫 Take a ticket", "b_my": "📋 My ticket", "b_cancel": "❌ Cancel ticket",
  "b_help": "ℹ️ Help", "b_staff": "👨‍💼 Staff", "b_menu": "🏠 Menu",
  "b_refresh": "🔄 Refresh", "b_back": "◀ Back", "b_agree": "✅ I agree", "b_decline": "✖ I do not agree",
  "b_yes_cancel": "✅ Yes, cancel", "b_no": "◀ No",
  "choose": "🎫 <b>Choose a service</b>\nThe number at the end is how many students are waiting now:",
  "privacy": "🔒 <b>Registration</b>\n\nTo take a ticket you register once. We store only your full name, study group and Telegram ID - they are used only for the queue (Law of the Republic of Kazakhstan No. 94-V). Only dean's office staff can see the data. You can ask us to delete your profile.\n\nDo you agree?",
  "declined": "Okay. A ticket cannot be taken without registration, but you can come back at any time.",
  "ask_name": "✍️ Enter your <b>full name</b> (for example: Aigerim Sadykova):",
  "bad_name": "⚠️ The name must be 2 to 60 characters. Try again:",
  "ask_group": "✍️ Now enter your <b>group</b> (for example: SE-2619):",
  "bad_group": "⚠️ The group must be 3 to 12 characters. Try again:",
  "registered": "✅ <b>Done!</b> {name} ({group}), you are registered.\nYou can now take a ticket.",
  "reg_exists": "You are already registered. Choose a service:",
  "reg_fail": "⚠️ Registration failed (you may already be in the database). Press \"Take a ticket\".",
  "created": "🎫 <b>Ticket created!</b>",
  "ticket": "Ticket: <b>{code}</b> ({service})\nPosition: {pos}\nPeople ahead: {ahead}\nEstimated wait: about {wait} min\nNow serving: {now}",
  "nobody": "nobody yet",
  "near": "⏰ <b>Your turn is near!</b>",
  "near_msg": "⏰ <b>Your turn is near!</b>\nTicket {code}, people ahead: {ahead}. Please come to the dean's office.",
  "called": "🔔 <b>It is your turn!</b>\nTicket <b>{code}</b> ({service}).\nPlease come to the dean's office.",
  "no_ticket": "You have no active ticket. Press \"Take a ticket\".",
  "already": "⚠️ You already have an active ticket. Cancel it first (rule: one active ticket per student).",
  "limit": "⚠️ <b>Ticket limit</b>\nYou can take at most {n} tickets per day, and you have already taken all {n} today. Please try again tomorrow.",
  "left": "Tickets left today: {left} of {n}.",
  "closed": "🕒 The dean's office is closed now. Office hours: {hours}. Please take a ticket during office hours.",
  "b_hist": "📜 History", "b_delete": "🗑 Delete my data", "b_later": "⏭ Let next go first", "b_yes_delete": "✅ Yes, delete",
  "hist": "📜 <b>Your tickets today</b>\n\n{lines}\n\n{left}",
  "hist_empty": "No tickets today yet.",
  "st": {"waiting": "waiting", "called": "called", "done": "served", "skipped": "skipped", "cancelled": "cancelled"},
  "delete_confirm": "🗑 <b>Delete your profile?</b>\nYour name, group and Telegram ID will be deleted. Ticket records stay, without a link to you. This cannot be undone.",
  "deleted": "✅ Your data has been deleted. Register again to use the bot.",
  "delete_later": "⚠️ You have taken tickets today, so the profile can be deleted tomorrow (this stops bypassing the daily limit).",
  "slow": "⏳ Too fast. Please wait a few seconds.",
  "later_ok": "⏭ The next student goes first. Your new ticket: {code}.",
  "later_no": "You cannot let someone go first: nobody is behind you, or you already used this option.",
  "timeout_hint": "⏰ {code} was called {min} min ago and has not come. Press \"Skip\" in the service panel.",
  "stats_extra": "\n\nBy hour (all time):\n{hours}\n\nAverage wait, min:\n{wait}\n\nLost (skipped or cancelled): {lost}",
  "corridor": "👥 People in the corridor now: {n}",
  "cancel_confirm": "Cancel ticket <b>{code}</b>?",
  "cancelled": "✅ Ticket cancelled.",
  "cant_cancel": "Nothing to cancel: no ticket, or you have already been called.",
  "help": "ℹ️ <b>How to use the bot</b>\n\n1. Press \"Take a ticket\" and choose a service.\n2. Follow your position in \"My ticket\".\n3. When your turn comes, the bot sends a 🔔 notification - come to the office.\n4. Changed your mind? Press \"Cancel ticket\".\n\nYou can take at most {n} tickets per day. Office hours: {hours}.\nEvery notification is also written as text, so sound is not required.",
  "denied": "⛔ Access denied: staff functions are only for approved staff.",
  "ask_pin": "🔐 Enter your staff <b>PIN</b>. The bot deletes the message with the PIN at once.",
  "pin_wrong": "❌ Wrong PIN. Attempts left: {left}.",
  "pin_locked": "⛔ Too many wrong attempts. Try again in {min} min.",
  "staff_ok": "✅ Logged in.",
  "staff_menu": "👨‍💼 <b>Staff panel</b>\n{name} - {role}\nChoose a service:",
  "expired": "⌛ Staff session expired. Please log in again.",
  "b_login": "🔐 Log in",
  "panel": "📋 <b>{service} ({prefix})</b> - in queue: {n}",
  "empty": "The queue is empty.",
  "serving": "NOW SERVING", "waiting": "waiting", "more": "... and {n} more",
  "b_call": "📣 Call next", "b_done": "✅ Complete", "b_skip": "⏭ Skip",
  "b_stats": "📊 Statistics", "b_logout": "🚪 Log out",
  "called_ok": "📣 {code} called. The student has been notified.",
  "busy": "⚠️ {code} is still being served. Complete or skip it first.",
  "nobody_now": "⚠️ Nobody is being served now.",
  "empty_q": "⚠️ The queue is empty.",
  "marked_done": "✅ {code} completed.", "marked_skipped": "⏭ {code} skipped.",
  "no_service": "⛔ You do not work with this service.",
  "admin_only": "⛔ Statistics are only for the administrator.",
  "stats": "📊 <b>Statistics</b>\n\nCompleted tickets per day:\n{days}\n\nWaiting now:\n{waiting}",
  "logged_out": "You have left the staff panel.",
  "myid": "Your Telegram ID: <code>{id}</code>",
  "use_buttons": "Please use the buttons 👇",
  "error": "⚠️ Something went wrong. Press \"Menu\" and try again.",
  "svc": {"C": "Certificate", "R": "Retake permit", "A": "Application", "O": "Other"},
  "role": {"operator": "operator", "administrator": "administrator"},
 },
}


def t(key, **kw):
    s = TEXTS[LANG][key]
    return s.format(**kw) if kw else s


def sname(prefix, fallback=""):
    return TEXTS[LANG]["svc"].get(prefix, fallback or prefix)


def esc(s):
    return html.escape(str(s))


# ---------------------------------------------------------------------
#  Database layer (same rules as prototype.py from Assignment 3)
# ---------------------------------------------------------------------
@contextmanager
def conn():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def qdate(db):
    """Today, unless an older queue is still active (then continue it) - as in prototype.py."""
    today = date.today().isoformat()
    old = db.execute("SELECT MAX(queue_date) FROM tickets WHERE status IN ('waiting','called')").fetchone()[0]
    return old if old and old < today else today


def hash_pin(pin, salt_hex, rounds=PIN_ROUNDS):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), rounds).hex()


def log_action(db, staff_id, ticket_id, action):
    db.execute("INSERT INTO audit_log(staff_id, ticket_id, action, created_at) VALUES (?,?,?,?)",
               (staff_id, ticket_id, action, now()))


def service_by_prefix(db, prefix):
    return db.execute("SELECT * FROM services WHERE prefix = ?", (prefix.upper(),)).fetchone()


def ensure_schema():
    """Small migrations, safe to run at every start (adds what the new features need)."""
    with conn() as db:
        def cols(table):
            return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for col, ddl in (("near_notified", "INTEGER NOT NULL DEFAULT 0"), ("swaps", "INTEGER NOT NULL DEFAULT 0"),
                         ("timeout_hinted", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols("tickets"):
                db.execute(f"ALTER TABLE tickets ADD COLUMN {col} {ddl}")
        if "pin_rounds" not in cols("staff"):
            db.execute("ALTER TABLE staff ADD COLUMN pin_rounds INTEGER NOT NULL DEFAULT 200000")
        db.execute("""CREATE TABLE IF NOT EXISTS pin_lock (telegram_id INTEGER PRIMARY KEY,
                      n INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS sensor_readings (
                      reading_id INTEGER PRIMARY KEY, sensor_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
                      people_in INTEGER NOT NULL CHECK (people_in >= 0), people_out INTEGER NOT NULL CHECK (people_out >= 0),
                      in_corridor INTEGER NOT NULL CHECK (in_corridor >= 0))""")


def close_stale():
    """New day: tickets left from earlier days are closed as 'skipped', so the queue starts clean."""
    if not CLOSE_STALE:
        return 0
    with conn() as db:
        cur = db.execute("UPDATE tickets SET status = 'skipped' WHERE status IN ('waiting','called') AND date(created_at) < ?",
                         (now()[:10],))
        return cur.rowcount


def office_open(dt=None):
    dt = dt or datetime.now()
    return dt.weekday() in WORKDAYS and OPEN_FROM <= dt.strftime("%H:%M") < OPEN_TO


def corridor_now():
    """People in the corridor from the latest sensor reading, or None if there is no fresh reading."""
    with conn() as db:
        r = db.execute("SELECT in_corridor, recorded_at FROM sensor_readings ORDER BY recorded_at DESC, reading_id DESC LIMIT 1").fetchone()
    if r is None:
        return None
    try:
        age = (datetime.now() - datetime.strptime(r["recorded_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None
    return r["in_corridor"] if age <= CORRIDOR_FRESH_SEC else None


HITS = {}      # telegram_id -> times of the latest taps (anti-flood, threat #7)


def rate_ok(tg):
    q = HITS.setdefault(tg, deque())
    t0 = time.time()
    while q and q[0] < t0 - RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_MAX:
        return False
    q.append(t0)
    return True


def _count_filter():
    return "" if COUNT_CANCELLED else " AND status <> 'cancelled'"


def tickets_today(db, student_id):
    """How many tickets this student has already taken today (by created_at)."""
    return db.execute("SELECT COUNT(*) FROM tickets WHERE student_id = ? AND date(created_at) = ?" + _count_filter(),
                      (student_id, now()[:10])).fetchone()[0]


def ensure_limit_trigger():
    """The database itself refuses the (DAILY_LIMIT+1)-th ticket of a day, like R8 (one_active_ticket)."""
    with conn() as db:
        db.execute("DROP TRIGGER IF EXISTS daily_ticket_limit")
        db.execute(f"""CREATE TRIGGER daily_ticket_limit BEFORE INSERT ON tickets
                       WHEN NEW.student_id IS NOT NULL AND (SELECT COUNT(*) FROM tickets
                            WHERE student_id = NEW.student_id AND date(created_at) = date(NEW.created_at){_count_filter()}) >= {int(DAILY_LIMIT)}
                       BEGIN SELECT RAISE(ABORT, 'DAILY_LIMIT'); END""")


def tickets_left(tg):
    with conn() as db:
        st = get_student(db, tg)
        return None if st is None else max(0, DAILY_LIMIT - tickets_today(db, st["student_id"]))


# ----- student side -----
def get_student(db, tg):
    return db.execute("SELECT * FROM students WHERE telegram_id = ?", (tg,)).fetchone()


def register_student(tg, name, group):                                  # WRITE (R1)
    try:
        with conn() as db:
            db.execute("INSERT INTO students(telegram_id, full_name, group_name, registered_at) VALUES (?,?,?,?)",
                       (tg, name, group, now()))
        return True
    except sqlite3.IntegrityError:
        return False


def services_list():                                                    # READ (R2)
    with conn() as db:
        return db.execute("""SELECT s.name, s.prefix, s.avg_minutes, COUNT(t.ticket_id) AS waiting
                             FROM services s
                             LEFT JOIN tickets t ON t.service_id = s.service_id
                                  AND t.queue_date = ? AND t.status = 'waiting'
                             GROUP BY s.service_id ORDER BY s.service_id""", (qdate(db),)).fetchall()


def take_ticket(tg, prefix):                                            # WRITE (R2, R8)
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return "need_reg"
        srv = service_by_prefix(db, prefix)
        if srv is None:
            return "bad_service"
        if db.execute("SELECT 1 FROM tickets WHERE student_id = ? AND status IN ('waiting','called')",
                      (st["student_id"],)).fetchone():
            return "already"
        if tickets_today(db, st["student_id"]) >= DAILY_LIMIT:
            return "limit"
        d = qdate(db)
        for _ in range(3):                      # a few tries: two students can pick the same number at once
            n = db.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM tickets WHERE service_id = ? AND queue_date = ?",
                           (srv["service_id"], d)).fetchone()[0]
            try:
                db.execute("""INSERT INTO tickets(student_id, service_id, queue_date, number, status, created_at)
                              VALUES (?,?,?,?, 'waiting', ?)""", (st["student_id"], srv["service_id"], d, n, now()))
                break
            except sqlite3.IntegrityError as e:
                msg = str(e)
                if "DAILY_LIMIT" in msg:
                    return "limit"              # the trigger daily_ticket_limit refused it
                if "tickets.number" in msg:
                    continue                    # number taken by someone else a moment ago: try the next one
                return "already"                # the unique index one_active_ticket refused it
        else:
            return "error"
    return "ok"


def my_ticket(tg):                                                      # READ
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return None
        tk = db.execute("""SELECT t.*, s.prefix, s.name AS sname, s.avg_minutes FROM tickets t
                           JOIN services s ON s.service_id = t.service_id
                           WHERE t.student_id = ? AND t.status IN ('waiting','called')""",
                        (st["student_id"],)).fetchone()
        if tk is None:
            return None
        ahead = db.execute("""SELECT COUNT(*) FROM tickets WHERE service_id = ? AND queue_date = ?
                              AND status = 'waiting' AND number < ?""",
                           (tk["service_id"], tk["queue_date"], tk["number"])).fetchone()[0]
        serving = db.execute("""SELECT number FROM tickets WHERE service_id = ? AND queue_date = ?
                                AND status = 'called'""", (tk["service_id"], tk["queue_date"])).fetchone()
        return {"code": f"{tk['prefix']}-{tk['number']}", "prefix": tk["prefix"], "service": sname(tk["prefix"], tk["sname"]),
                "status": tk["status"], "can_later": tk["swaps"] < MAX_SWAPS, "ahead": ahead, "pos": ahead + 1, "wait": ahead * tk["avg_minutes"],
                "serving": f"{tk['prefix']}-{serving['number']}" if serving else t("nobody")}


def history_today(tg):                                                  # READ
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return None
        rows = db.execute("""SELECT t.number, t.status, t.created_at, s.prefix, s.name FROM tickets t
                             JOIN services s ON s.service_id = t.service_id
                             WHERE t.student_id = ? AND date(t.created_at) = ? ORDER BY t.ticket_id""",
                          (st["student_id"], now()[:10])).fetchall()
        return rows, max(0, DAILY_LIMIT - tickets_today(db, st["student_id"]))


def swap_later(tg):                                                     # WRITE: let the next student go first
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return None
        tk = db.execute("SELECT * FROM tickets WHERE student_id = ? AND status = 'waiting'", (st["student_id"],)).fetchone()
        if tk is None or tk["swaps"] >= MAX_SWAPS:
            return None
        nxt = db.execute("""SELECT ticket_id, number FROM tickets WHERE service_id = ? AND queue_date = ?
                            AND status = 'waiting' AND number > ? ORDER BY number LIMIT 1""",
                         (tk["service_id"], tk["queue_date"], tk["number"])).fetchone()
        if nxt is None:
            return None
        a, b = tk["number"], nxt["number"]
        db.execute("UPDATE tickets SET number = ? WHERE ticket_id = ?", (1_000_000_000, tk["ticket_id"]))   # (service, date, number) is unique
        db.execute("UPDATE tickets SET number = ? WHERE ticket_id = ?", (a, nxt["ticket_id"]))
        db.execute("UPDATE tickets SET number = ?, swaps = swaps + 1 WHERE ticket_id = ?", (b, tk["ticket_id"]))
        prefix = db.execute("SELECT prefix FROM services WHERE service_id = ?", (tk["service_id"],)).fetchone()[0]
        return f"{prefix}-{b}"


def delete_profile(tg):                                                 # WRITE: delete my data
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return "none"
        if tickets_today(db, st["student_id"]) > 0:
            return "later"                      # otherwise delete + register again would reset the daily limit
        db.execute("DELETE FROM students WHERE student_id = ?", (st["student_id"],))   # tickets.student_id becomes NULL
        return "ok"


def cancel_ticket(tg):                                                  # WRITE
    with conn() as db:
        st = get_student(db, tg)
        if st is None:
            return False
        cur = db.execute("UPDATE tickets SET status = 'cancelled' WHERE student_id = ? AND status = 'waiting'",
                         (st["student_id"],))
        return cur.rowcount > 0


# ----- staff side -----
def staff_row(db, tg):
    return db.execute("SELECT * FROM staff WHERE telegram_id = ? AND is_active = 1", (tg,)).fetchone()


def verify_pin(tg, pin):                                                # R12
    with conn() as db:
        row = staff_row(db, tg)
        rounds = row["pin_rounds"] if row is not None else PIN_ROUNDS
        ok = row is not None and hmac.compare_digest(hash_pin(pin, row["pin_salt"], rounds), row["pin_hash"])
        if not ok:
            log_action(db, row["staff_id"] if row else None, None, "denied")
        elif rounds < PIN_ROUNDS:               # upgrade an old hash while the right PIN is at hand
            db.execute("UPDATE staff SET pin_hash = ?, pin_rounds = ? WHERE staff_id = ?",
                       (hash_pin(pin, row["pin_salt"], PIN_ROUNDS), PIN_ROUNDS, row["staff_id"]))
        return dict(row) if ok else None


def lock_left(tg):
    """Minutes of PIN lock left (0 = not locked). Stored in the database, so a restart does not reset it."""
    with conn() as db:
        r = db.execute("SELECT locked_until FROM pin_lock WHERE telegram_id = ?", (tg,)).fetchone()
    return math.ceil((r["locked_until"] - time.time()) / 60) if r and r["locked_until"] > time.time() else 0


def _set_lock(db, tg, n, until):
    db.execute("""INSERT INTO pin_lock(telegram_id, n, locked_until) VALUES (?,?,?)
                  ON CONFLICT(telegram_id) DO UPDATE SET n = excluded.n, locked_until = excluded.locked_until""", (tg, n, until))


def pin_failed(tg):
    """Count a wrong PIN. Returns attempts left, 0 = the login is now locked."""
    with conn() as db:
        r = db.execute("SELECT n FROM pin_lock WHERE telegram_id = ?", (tg,)).fetchone()
        n = (r["n"] if r else 0) + 1
        if n >= MAX_PIN_FAILS:
            _set_lock(db, tg, 0, time.time() + LOCK_MIN * 60)
            return 0
        _set_lock(db, tg, n, 0)
        return MAX_PIN_FAILS - n


def pin_reset(tg):
    with conn() as db:
        _set_lock(db, tg, 0, 0)


def deny(staff_id=None):
    with conn() as db:
        log_action(db, staff_id, None, "denied")


def can_serve(db, staff, srv):
    if staff["role"] == "administrator":
        return True
    return db.execute("SELECT 1 FROM staff_services WHERE staff_id = ? AND service_id = ?",
                      (staff["staff_id"], srv["service_id"])).fetchone() is not None


def staff_services(staff):
    with conn() as db:
        if staff["role"] == "administrator":
            return db.execute("SELECT * FROM services ORDER BY service_id").fetchall()
        return db.execute("""SELECT s.* FROM services s JOIN staff_services x ON x.service_id = s.service_id
                             WHERE x.staff_id = ? ORDER BY s.service_id""", (staff["staff_id"],)).fetchall()


def queue_rows(staff, prefix):                                          # READ
    with conn() as db:
        srv = service_by_prefix(db, prefix)
        if srv is None or not can_serve(db, staff, srv):
            log_action(db, staff["staff_id"], None, "denied")
            return None, None
        rows = db.execute("""SELECT t.number, t.status, st.full_name, st.group_name FROM tickets t
                             LEFT JOIN students st ON st.student_id = t.student_id
                             WHERE t.service_id = ? AND t.queue_date = ? AND t.status IN ('waiting','called')
                             ORDER BY t.number""", (srv["service_id"], qdate(db))).fetchall()
        return dict(srv), rows


def staff_do(staff, prefix, action):                                    # WRITE (R6, R7)
    """action: call | complete | skip. Returns (code, ticket_code)."""
    with conn() as db:
        srv = service_by_prefix(db, prefix)
        if srv is None or not can_serve(db, staff, srv):
            log_action(db, staff["staff_id"], None, "denied")
            return "no_service", ""
        d = qdate(db)
        cur_t = db.execute("SELECT * FROM tickets WHERE service_id = ? AND queue_date = ? AND status = 'called'",
                           (srv["service_id"], d)).fetchone()
        if action == "call":
            if cur_t:
                return "busy", f"{srv['prefix']}-{cur_t['number']}"
            nxt = db.execute("""SELECT ticket_id, number FROM tickets WHERE service_id = ? AND queue_date = ?
                                AND status = 'waiting' ORDER BY number LIMIT 1""", (srv["service_id"], d)).fetchone()
            if nxt is None:
                return "empty_q", ""
            db.execute("UPDATE tickets SET status = 'called', called_at = ?, staff_id = ? WHERE ticket_id = ?",
                       (now(), staff["staff_id"], nxt["ticket_id"]))
            log_action(db, staff["staff_id"], nxt["ticket_id"], "call")
            return "called_ok", f"{srv['prefix']}-{nxt['number']}"
        if cur_t is None:
            return "nobody_now", ""
        new = "done" if action == "complete" else "skipped"
        db.execute("UPDATE tickets SET status = ?, finished_at = ? WHERE ticket_id = ?",
                   (new, now() if new == "done" else None, cur_t["ticket_id"]))
        log_action(db, staff["staff_id"], cur_t["ticket_id"], action)
        return f"marked_{new}", f"{srv['prefix']}-{cur_t['number']}"


def stats_text():                                                       # READ (R10)
    since = "'1970-01-01'"
    with conn() as db:
        days = db.execute("""SELECT queue_date, COUNT(*) AS n FROM tickets WHERE status = 'done'
                             GROUP BY queue_date ORDER BY queue_date DESC LIMIT 5""").fetchall()
        wait = db.execute("""SELECT s.prefix, s.name, COUNT(t.ticket_id) AS n FROM services s
                             LEFT JOIN tickets t ON t.service_id = s.service_id AND t.queue_date = ?
                                  AND t.status = 'waiting'
                             GROUP BY s.service_id ORDER BY s.service_id""", (qdate(db),)).fetchall()
        hours = db.execute(f"""SELECT strftime('%H', created_at) AS h, COUNT(*) AS n FROM tickets
                               WHERE date(created_at) >= {since} GROUP BY h ORDER BY h""").fetchall()
        waits = db.execute(f"""SELECT s.prefix, s.name, ROUND(AVG((julianday(t.called_at) - julianday(t.created_at)) * 1440), 1) AS m
                               FROM tickets t JOIN services s ON s.service_id = t.service_id
                               WHERE t.called_at IS NOT NULL AND date(t.created_at) >= {since}
                               GROUP BY s.service_id ORDER BY s.service_id""").fetchall()
        lost = db.execute(f"""SELECT COUNT(*) AS total, COALESCE(SUM(status IN ('skipped','cancelled')), 0) AS lost
                              FROM tickets WHERE date(created_at) >= {since}""").fetchone()
    d = "\n".join(f"  {r['queue_date']} — {r['n']}" for r in days) or "  —"
    w = "\n".join(f"  [{r['prefix']}] {sname(r['prefix'], r['name'])} — {r['n']}" for r in wait)
    hh = "\n".join(f"  {r['h']}:00 — {r['n']}" for r in hours) or "  —"
    ww = "\n".join(f"  [{r['prefix']}] {sname(r['prefix'], r['name'])} — {r['m']}" for r in waits) or "  —"
    ll = f"{lost['lost']} / {lost['total']} ({round(100 * lost['lost'] / lost['total'], 1)}%)" if lost["total"] else "—"
    text = t("stats", days=d, waiting=w) + t("stats_extra", hours=hh, wait=ww, lost=ll)
    n = corridor_now()
    return text + ("\n\n" + t("corridor", n=n) if n is not None else "")


# ---------------------------------------------------------------------
#  Pictures and screens
# ---------------------------------------------------------------------
def photo_src(key):
    """file_id if the picture was already uploaded, else the local file; None if the file is missing."""
    if key in PHOTO_IDS:
        return PHOTO_IDS[key]
    path = IMG_DIR / IMAGES[key]
    return path if path.exists() else None


def remember(key, msg):
    if key and isinstance(msg, Message) and msg.photo:
        PHOTO_IDS[key] = msg.photo[-1].file_id


def kb(*rows):
    """kb([("text", "callback"), ...], [...])"""
    return InlineKeyboardMarkup([[InlineKeyboardButton(a, callback_data=b) for a, b in row] for row in rows])


async def show(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text, img=None, markup=None):
    """Show a screen: picture + caption + buttons. After a button press the old message is edited."""
    q = update.callback_query
    src = photo_src(img) if img else None
    if q and q.message:
        was_photo = bool(q.message.photo)
        try:
            if src is not None and was_photo:
                m = await q.edit_message_media(InputMediaPhoto(src, caption=text, parse_mode=HTML), reply_markup=markup)
                remember(img, m)
                return
            if src is None and not was_photo:
                await q.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
                return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            log.warning("edit failed, sending a new message: %s", e)
        try:
            await q.message.delete()
        except TelegramError:
            pass
    chat_id = update.effective_chat.id
    if src is not None:
        m = await ctx.bot.send_photo(chat_id, src, caption=text, parse_mode=HTML, reply_markup=markup)
        remember(img, m)
    else:
        await ctx.bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=markup)


def main_kb():
    return kb([(t("b_take"), "svc"), (t("b_my"), "my")],
              [(t("b_cancel"), "cancel"), (t("b_hist"), "hist")],
              [(t("b_help"), "help"), (t("b_staff"), "staff")])


async def screen_menu(update, ctx, text=None, img="team"):
    await show(update, ctx, text or t("menu"), img, main_kb())


async def screen_services(update, ctx, head=""):
    rows = services_list()
    buttons = [[(f"[{r['prefix']}] {sname(r['prefix'], r['name'])} — {r['waiting']}", f"take:{r['prefix']}")] for r in rows]
    left = tickets_left(update.effective_user.id)
    extra = t("left", left=left, n=DAILY_LIMIT) + "\n\n" if left is not None else ""
    await show(update, ctx, (head + "\n\n" if head else "") + extra + t("choose"), "queue",
               kb(*buttons, [(t("b_menu"), "menu")]))


async def screen_privacy(update, ctx):
    await show(update, ctx, t("privacy"), "study", kb([(t("b_agree"), "agree"), (t("b_decline"), "decline")]))


def ticket_text(tk):
    if tk["status"] == "called":
        return t("called", code=tk["code"], service=esc(tk["service"]))
    s = t("ticket", code=tk["code"], service=esc(tk["service"]), pos=tk["pos"], ahead=tk["ahead"],
          wait=tk["wait"], now=tk["serving"])
    return (t("near") + "\n\n" + s) if tk["ahead"] <= NEAR_AHEAD else s


async def screen_my(update, ctx, head=""):
    tk = my_ticket(update.effective_user.id)
    if tk is None:
        await show(update, ctx, (head + "\n\n" if head else "") + t("no_ticket"), "queue",
                   kb([(t("b_take"), "svc")], [(t("b_menu"), "menu")]))
        return
    rows = [[(t("b_refresh"), "my")]]
    if tk["status"] == "waiting":
        rows[0].append((t("b_cancel"), "cancel"))
        if tk["can_later"] and tk["ahead"] >= 0:
            rows.append([(t("b_later"), "later")])
    rows.append([(t("b_menu"), "menu")])
    await show(update, ctx, (head + "\n\n" if head else "") + ticket_text(tk),
               "notify" if tk["status"] == "called" else "queue", kb(*rows))


async def screen_hist(update, ctx):
    h = history_today(update.effective_user.id)
    if h is None:
        await screen_privacy(update, ctx)
        return
    rows, left = h
    lines = "\n".join(f"{r['prefix']}-{r['number']} · {esc(sname(r['prefix'], r['name']))} · "
                      f"{TEXTS[LANG]['st'][r['status']]} · {r['created_at'][11:16]}" for r in rows) or t("hist_empty")
    await show(update, ctx, t("hist", lines=lines, left=t("left", left=left, n=DAILY_LIMIT)), "queue",
               kb([(t("b_take"), "svc")], [(t("b_menu"), "menu")]))


async def screen_cancel(update, ctx):
    tk = my_ticket(update.effective_user.id)
    if tk is None or tk["status"] != "waiting":
        await show(update, ctx, t("cant_cancel"), "queue", kb([(t("b_menu"), "menu")]))
        return
    await show(update, ctx, t("cancel_confirm", code=tk["code"]), "queue",
               kb([(t("b_yes_cancel"), "cancel_yes"), (t("b_no"), "my")]))


# ----- staff screens -----
def session_ok(ctx):
    return ctx.user_data.get("staff_until", 0) > time.time()


async def staff_guard(update, ctx):
    """Every staff action passes here: approved staff + valid session (R12). Returns the staff row or None."""
    tg = update.effective_user.id
    with conn() as db:
        row = staff_row(db, tg)
    if row is None:
        deny()
        await show(update, ctx, t("denied"), "queue", kb([(t("b_menu"), "menu")]))
        return None
    if not session_ok(ctx):
        await show(update, ctx, t("expired"), "queue", kb([(t("b_login"), "staff")], [(t("b_menu"), "menu")]))
        return None
    return dict(row)


async def screen_staff_menu(update, ctx, staff, head=""):
    rows = [[(f"[{s['prefix']}] {sname(s['prefix'], s['name'])}", f"sq:{s['prefix']}")] for s in staff_services(staff)]
    if staff["role"] == "administrator":
        rows.append([(t("b_stats"), "stats")])
    rows.append([(t("b_logout"), "logout"), (t("b_menu"), "menu")])
    body = t("staff_menu", name=esc(staff["full_name"]), role=TEXTS[LANG]["role"][staff["role"]])
    n = corridor_now()
    head = "\n".join(x for x in (head, t("corridor", n=n) if n is not None else "") if x)
    await show(update, ctx, (head + "\n\n" if head else "") + body, "queue", kb(*rows))


async def screen_panel(update, ctx, staff, prefix, head=""):
    srv, rows = queue_rows(staff, prefix)
    if srv is None:
        await show(update, ctx, t("no_service"), "queue", kb([(t("b_back"), "smenu")]))
        return
    lines = [t("panel", service=esc(sname(srv["prefix"], srv["name"])), prefix=srv["prefix"], n=len(rows)), ""]
    if not rows:
        lines.append(t("empty"))
    for r in rows[:10]:
        mark = t("serving") if r["status"] == "called" else t("waiting")
        lines.append(f"{srv['prefix']}-{r['number']}  {esc(r['full_name'] or '—')} ({esc(r['group_name'] or '—')}) — {mark}")
    if len(rows) > 10:
        lines.append(t("more", n=len(rows) - 10))
    p = srv["prefix"]
    await show(update, ctx, (head + "\n\n" if head else "") + "\n".join(lines), "queue",
               kb([(t("b_call"), f"sa:call:{p}")],
                  [(t("b_done"), f"sa:complete:{p}"), (t("b_skip"), f"sa:skip:{p}")],
                  [(t("b_refresh"), f"sq:{p}"), (t("b_back"), "smenu")]))


# ---------------------------------------------------------------------
#  Handlers
# ---------------------------------------------------------------------
async def cmd_start(update, ctx):
    ctx.user_data.pop("state", None)
    await screen_menu(update, ctx, t("welcome"), "team")


async def cmd_menu(update, ctx):
    ctx.user_data.pop("state", None)
    await screen_menu(update, ctx)


async def cmd_help(update, ctx):
    await show(update, ctx, t("help", n=DAILY_LIMIT, hours=f"{OPEN_FROM}–{OPEN_TO}"), "study",
               kb([(t("b_delete"), "del")], [(t("b_menu"), "menu")]))


async def cmd_myid(update, ctx):
    await update.message.reply_text(t("myid", id=update.effective_user.id), parse_mode=HTML)


async def on_button(update, ctx):
    q = update.callback_query
    if not rate_ok(q.from_user.id):
        await q.answer(t("slow"))
        return
    await q.answer()
    data = q.data or ""
    tg = q.from_user.id
    ctx.user_data.pop("state", None)            # any button cancels a half-finished text input
    parts = data.split(":")
    cmd = parts[0]

    if cmd == "menu":
        await screen_menu(update, ctx)
    elif cmd == "help":
        await cmd_help(update, ctx)
    elif cmd == "svc":
        with conn() as db:
            known = get_student(db, tg) is not None
        if known:
            await screen_services(update, ctx)
        else:
            await screen_privacy(update, ctx)
    elif cmd == "agree":
        with conn() as db:
            known = get_student(db, tg) is not None
        if known:
            await screen_services(update, ctx, t("reg_exists"))
        else:
            ctx.user_data["state"] = "name"
            await show(update, ctx, t("ask_name"), "study", kb([(t("b_menu"), "menu")]))
    elif cmd == "decline":
        await screen_menu(update, ctx, t("declined"))
    elif cmd == "hist":
        await screen_hist(update, ctx)
    elif cmd == "later":
        code = swap_later(tg)
        await screen_my(update, ctx, t("later_ok", code=code) if code else t("later_no"))
    elif cmd == "del":
        await show(update, ctx, t("delete_confirm"), "study", kb([(t("b_yes_delete"), "del_yes"), (t("b_no"), "help")]))
    elif cmd == "del_yes":
        res = delete_profile(tg)
        await show(update, ctx, t({"ok": "deleted", "later": "delete_later"}.get(res, "deleted")), "study", kb([(t("b_menu"), "menu")]))
    elif cmd == "take" and ENFORCE_HOURS and not office_open():
        await show(update, ctx, t("closed", hours=f"{OPEN_FROM}–{OPEN_TO}"), "queue", kb([(t("b_menu"), "menu")]))
    elif cmd == "take":
        res = take_ticket(tg, parts[1])
        if res == "need_reg":
            await screen_privacy(update, ctx)
        elif res == "already":
            await screen_my(update, ctx, t("already"))
        elif res == "limit":
            await show(update, ctx, t("limit", n=DAILY_LIMIT), "queue", kb([(t("b_menu"), "menu")]))
        elif res == "ok":
            await screen_my(update, ctx, t("created"))
        else:
            await screen_services(update, ctx)
    elif cmd == "my":
        await screen_my(update, ctx)
    elif cmd == "cancel":
        await screen_cancel(update, ctx)
    elif cmd == "cancel_yes":
        ok = cancel_ticket(tg)
        await show(update, ctx, t("cancelled") if ok else t("cant_cancel"), "queue",
                   kb([(t("b_take"), "svc")], [(t("b_menu"), "menu")]))

    # ---- staff ----
    elif cmd == "staff":
        with conn() as db:
            row = staff_row(db, tg)
        if row is None:
            deny()
            await show(update, ctx, t("denied"), "queue", kb([(t("b_menu"), "menu")]))
        elif lock_left(tg):
            await show(update, ctx, t("pin_locked", min=lock_left(tg)), "queue", kb([(t("b_menu"), "menu")]))
        elif session_ok(ctx):
            await screen_staff_menu(update, ctx, dict(row))
        else:
            ctx.user_data["state"] = "pin"
            await show(update, ctx, t("ask_pin"), "queue", kb([(t("b_menu"), "menu")]))
    elif cmd in ("smenu", "sq", "sa", "stats", "logout"):
        if cmd == "logout":
            ctx.user_data.pop("staff_until", None)
            await screen_menu(update, ctx, t("logged_out"))
            return
        staff = await staff_guard(update, ctx)
        if staff is None:
            return
        if cmd == "smenu":
            await screen_staff_menu(update, ctx, staff)
        elif cmd == "sq":
            await screen_panel(update, ctx, staff, parts[1])
        elif cmd == "sa":
            code, tcode = staff_do(staff, parts[2], parts[1])
            await screen_panel(update, ctx, staff, parts[2], t(code, code=tcode) if code != "no_service" else t("no_service"))
        elif cmd == "stats":
            if staff["role"] != "administrator":
                deny(staff["staff_id"])
                await show(update, ctx, t("admin_only"), "queue", kb([(t("b_back"), "smenu")]))
            else:
                await show(update, ctx, stats_text(), "queue", kb([(t("b_refresh"), "stats"), (t("b_back"), "smenu")]))


async def on_text(update, ctx):
    state = ctx.user_data.get("state")
    text = (update.message.text or "").strip()
    tg = update.effective_user.id
    menu_btn = kb([(t("b_menu"), "menu")])
    if not rate_ok(tg):
        await update.message.reply_text(t("slow"))
        return

    if state == "name":
        if not 2 <= len(text) <= 60:
            await show(update, ctx, t("bad_name"), "study", menu_btn)
            return
        ctx.user_data["reg_name"] = text
        ctx.user_data["state"] = "group"
        await show(update, ctx, t("ask_group"), "study", menu_btn)

    elif state == "group":
        if not 3 <= len(text) <= 12:
            await show(update, ctx, t("bad_group"), "study", menu_btn)
            return
        name = ctx.user_data.pop("reg_name", "")
        ctx.user_data.pop("state", None)
        if name and register_student(tg, name, text):
            await show(update, ctx, t("registered", name=esc(name), group=esc(text)), "study",
                       kb([(t("b_take"), "svc")], [(t("b_menu"), "menu")]))
        else:
            await show(update, ctx, t("reg_fail"), "study", kb([(t("b_take"), "svc")], [(t("b_menu"), "menu")]))

    elif state == "pin":
        try:
            await update.message.delete()            # do not leave the PIN in the chat
        except TelegramError:
            pass
        if lock_left(tg):
            ctx.user_data.pop("state", None)
            await show(update, ctx, t("pin_locked", min=lock_left(tg)), "queue", menu_btn)
            return
        staff = verify_pin(tg, text)
        if staff:
            pin_reset(tg)
            ctx.user_data.pop("state", None)
            ctx.user_data["staff_until"] = time.time() + SESSION_MIN * 60
            await screen_staff_menu(update, ctx, staff, t("staff_ok"))
        else:
            left = pin_failed(tg)
            if left == 0:
                ctx.user_data.pop("state", None)
                await show(update, ctx, t("pin_locked", min=LOCK_MIN), "queue", menu_btn)
            else:
                await show(update, ctx, t("pin_wrong", left=left), "queue", menu_btn)
    else:
        await screen_menu(update, ctx, t("use_buttons"))


async def on_error(update, ctx):
    log.error("Unhandled error", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await ctx.bot.send_message(update.effective_chat.id, t("error"), reply_markup=kb([(t("b_menu"), "menu")]))
    except TelegramError:
        pass


# ---------------------------------------------------------------------
#  Notifications: the bot checks the database and tells students when their turn comes
# ---------------------------------------------------------------------
CLEAN = {"day": None}   # day of the last clean-up of old tickets


def pending_called():
    with conn() as db:
        return db.execute("""SELECT t.ticket_id, t.number, s.prefix, s.name, st.telegram_id FROM tickets t
                             JOIN services s ON s.service_id = t.service_id
                             JOIN students st ON st.student_id = t.student_id
                             WHERE t.status = 'called' AND t.notified = 0""").fetchall()


def pending_near():
    with conn() as db:
        rows = db.execute("""SELECT t.ticket_id, t.number, s.prefix, s.name, st.telegram_id,
                                    (SELECT COUNT(*) FROM tickets x WHERE x.service_id = t.service_id
                                       AND x.queue_date = t.queue_date AND x.status = 'waiting'
                                       AND x.number < t.number) AS ahead
                             FROM tickets t JOIN services s ON s.service_id = t.service_id
                             JOIN students st ON st.student_id = t.student_id
                             WHERE t.status = 'waiting' AND t.queue_date = ? AND t.near_notified = 0""", (qdate(db),)).fetchall()
    return [r for r in rows if r["ahead"] <= NEAR_AHEAD]


def pending_timeouts():
    """Called tickets whose student has not come for CALL_TIMEOUT_MIN minutes (operator gets a hint once)."""
    with conn() as db:
        rows = db.execute("""SELECT t.ticket_id, t.number, t.called_at, s.prefix, sf.telegram_id AS staff_tg
                             FROM tickets t JOIN services s ON s.service_id = t.service_id
                             JOIN staff sf ON sf.staff_id = t.staff_id
                             WHERE t.status = 'called' AND t.timeout_hinted = 0""").fetchall()
    cutoff = datetime.now() - timedelta(minutes=CALL_TIMEOUT_MIN)
    out = []
    for r in rows:
        try:
            if datetime.strptime(r["called_at"][:19], "%Y-%m-%d %H:%M:%S") <= cutoff:
                out.append(r)
        except (TypeError, ValueError):
            pass
    return out


async def tell(bot, chat_id, text):
    """Plain text message (hint for staff). True = handled."""
    try:
        await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=kb([(t("b_menu"), "menu")]))
        return True
    except (Forbidden, BadRequest) as e:
        log.info("hint to %s not delivered: %s", chat_id, e)
        return True
    except TelegramError as e:
        log.warning("hint to %s failed, will retry: %s", chat_id, e)
        return False


async def send_bell(bot, chat_id):
    """Decorative animated bell. No caption: all information is already in the text notification above.
    It is a GIF without sound and Telegram never plays it with sound, so nothing is conveyed by sound alone."""
    global ANIM_ID
    path = IMG_DIR / ANIM_FILE
    src = ANIM_ID or (path if path.exists() else None)
    if src is None:
        return
    try:
        m = await bot.send_animation(chat_id, src)
        if m.animation:
            ANIM_ID = m.animation.file_id
    except TelegramError as e:                 # the animation is optional; never repeat the notification because of it
        log.info("animation to %s not sent: %s", chat_id, e)


async def push(bot, chat_id, text, animate=False):
    """Send a notification with the picture. True = handled (sent, or the chat does not exist)."""
    src = photo_src("notify")
    markup = kb([(t("b_my"), "my"), (t("b_menu"), "menu")])
    try:
        if src is not None:
            m = await bot.send_photo(chat_id, src, caption=text, parse_mode=HTML, reply_markup=markup)
            remember("notify", m)
        else:
            await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=markup)
        if animate:
            await send_bell(bot, chat_id)
        return True
    except (Forbidden, BadRequest) as e:       # user blocked the bot / test student with a fake Telegram ID
        log.info("notification to %s not delivered: %s", chat_id, e)
        return True
    except TelegramError as e:                 # network problem - try again later
        log.warning("notification to %s failed, will retry: %s", chat_id, e)
        return False


async def notifier(bot):
    while True:
        try:
            for r in pending_called():
                text = t("called", code=f"{r['prefix']}-{r['number']}", service=esc(sname(r["prefix"], r["name"])))
                if await push(bot, r["telegram_id"], text, animate=True):
                    with conn() as db:
                        db.execute("UPDATE tickets SET notified = 1 WHERE ticket_id = ?", (r["ticket_id"],))
            for r in pending_near():
                text = t("near_msg", code=f"{r['prefix']}-{r['number']}", ahead=r["ahead"])
                if await push(bot, r["telegram_id"], text):
                    with conn() as db:
                        db.execute("UPDATE tickets SET near_notified = 1 WHERE ticket_id = ?", (r["ticket_id"],))
            for r in pending_timeouts():
                if await tell(bot, r["staff_tg"], t("timeout_hint", code=f"{r['prefix']}-{r['number']}", min=CALL_TIMEOUT_MIN)):
                    with conn() as db:
                        db.execute("UPDATE tickets SET timeout_hinted = 1 WHERE ticket_id = ?", (r["ticket_id"],))
            if CLEAN["day"] != date.today().isoformat():       # a new day started while the bot was running
                CLEAN["day"] = date.today().isoformat()
                log.info("clean-up of old tickets: %s closed", close_stale())
        except Exception:
            log.exception("notifier error")
        await asyncio.sleep(NOTIFY_EVERY)


async def post_shutdown(app):
    task = app.bot_data.get("notifier")
    if task:
        task.cancel()


async def post_init(app):
    app.bot_data["notifier"] = asyncio.create_task(notifier(app.bot))


def main():
    if BOT_TOKEN.startswith("PASTE") or ":" not in BOT_TOKEN:
        raise SystemExit("Вставьте токен от BotFather в строку BOT_TOKEN в начале файла bot.py")
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"База данных не найдена: {DB_PATH}. Положите queue.sqlite рядом с bot.py "
                         f"или создайте её: python make_test_data.py")
    ensure_schema()
    ensure_limit_trigger()
    CLEAN["day"] = date.today().isoformat()
    log.info("clean-up of old tickets: %s closed", close_stale())
    for key, name in IMAGES.items():
        if not (IMG_DIR / name).exists():
            log.warning("Picture %s not found - messages will be sent without it", IMG_DIR / name)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))
    app.add_error_handler(on_error)
    log.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
