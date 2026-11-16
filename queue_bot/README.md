# Digital Queue for the Dean's Office

Telegram bot for the queue of the dean's office (Astana IT University, SE-2619).
A student takes a ticket in the chat, sees the position and gets a message when the turn is near.
Dean's office staff call, skip and complete tickets in the same bot. Data is stored in SQLite.

Authors: Nurtore Akzhol, Aldiyar Saifula.

## Requirements
Python 3.10 or newer, a Telegram account, and a bot token from @BotFather.

## Run the project
1. Install the library: `pip install -r requirements.txt`
2. Create the database with invented test data: `python make_test_data.py`
   (creates `queue.sqlite` from `schema.sql`; it deletes an existing file with this name).
3. Give the bot your token, either in the line `BOT_TOKEN` of `bot.py` or as an environment variable:
   `set BOT_TOKEN=123456:ABC...` (Windows) or `export BOT_TOKEN=123456:ABC...` (Linux, macOS).
   Never commit a real token.
4. Add your own Telegram account as a staff member (the test staff in the database have invented Telegram IDs):
   send `/myid` to the bot, then run `python add_staff.py` and enter the ID, a name, the role and a PIN.
5. Start the bot: `python bot.py`. Keep the window open. Stop with Ctrl+C.
6. Optional, simulated IoT sensor ("smart corridor"): in a second terminal run `python sensor_sim.py --demo`.
   Students see the number of people in the corridor, the administrator gets the screen "Smart corridor" (see below).
Set `LANG = "en"` in `bot.py` for the English interface (default is Russian).

## Test accounts (all data is invented)
| Role | How to get it | Can do |
|---|---|---|
| Student | Any Telegram account: press Start, Take a ticket, agree, enter a name and a group | Take, view and cancel one ticket |
| Operator | `python add_staff.py`, role `operator`, services for example `C R` | Queue, call, skip, complete for assigned services only |
| Administrator | `python add_staff.py`, role `administrator` | All services and Statistics |

The supplied database also has invented staff for the command-line prototype
(administrator ID 100007919, PIN 482915; operator ID 100015838, PIN 736204, Certificate only).
These IDs do not belong to real Telegram accounts, so use `add_staff.py` to log in through the bot.

## Files
| File | Purpose |
|---|---|
| `bot.py` | the Telegram bot (student and staff screens, notifications, statistics) |
| `schema.sql`, `make_test_data.py` | database structure and test data generator |
| `add_staff.py` | adds or updates a staff member (PIN is stored as a salted PBKDF2 hash) |
| `sensor_queries.py` | ready-made read-only SQL queries for the sensor data (hourly average, peak, crowded share, ...) |
| `sensor_sim.py` | simulated corridor people counter (sends readings to the gateway in `bot.py`; table `sensor_readings`) |
| `backup.py` | daily backup of the database file |
| `prototype.py` | command-line prototype from Assignment 3 (same database and rules) |
| `images/`, `assets/`, `web/` | media: optimised JPEG pictures, bell animation, SVG logo, static page for the page-weight test |

## Security notes
PIN hash with salt (PBKDF2-HMAC-SHA256, 200,000 rounds), parameterised SQL, approved staff list with roles,
lockout after 5 wrong PINs, 30-minute staff session, audit log of staff actions.
The database file has no network port. The prototype contains only invented data.

## Smart corridor (IoT extension, Project Stage 2, Part D)
A people counter at the corridor door reports how many people entered and left. The bot turns this into three smart rules.
No camera, names or Telegram IDs are stored with a reading.

| Layer | What it is in this project |
|---|---|
| 1. Device | `sensor_sim.py` (simulates an ESP32 with two door-frame sensors) |
| 2. Network | HTTP POST `/api/v1/readings` with the header `X-Api-Key`, to `127.0.0.1:8765` (a real device would use Wi-Fi + HTTPS or MQTT over TLS) |
| 3. Processing | the gateway inside `bot.py`: validates the data, calculates "people inside", writes to `sensor_readings`, runs the rules |
| 4. Application | the Telegram bot: "Corridor" screen, notifications, administrator screen "Smart corridor" |

Rules: **A** the student with a waiting ticket can press "Tell me when it is free" and gets a message when the corridor has at most `CORRIDOR_LOW` (5) people;
**B** the staff get an alert when `CORRIDOR_HIGH` (12) or more people stay in the corridor for 3 readings in a row (at most one alert per 10 minutes);
**C** the administrators get an alert when a sensor that was online stops sending data (and a message when it is back).
Privacy: readings older than `SENSOR_KEEP_DAYS` (30) are deleted automatically; a subscription is deleted together with the student profile.
Settings are at the top of `bot.py`. Change the sensor key with the environment variable `SENSOR_API_KEY` (the same value for the bot and `sensor_sim.py`).
All data made by `sensor_sim.py` is simulated test data (sensor id `SIM-01`).

