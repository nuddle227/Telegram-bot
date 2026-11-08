# Compatibility update notes

The project's `bot.py` was replaced with the separately supplied teammate file **without changing its contents**. Changes below apply only to supporting files, setup, tests, and documentation.

- `sensor_sim.py`: now writes to `sensor_readings` with column names expected by `bot.py`; added `--once` smoke-test option.
- `add_staff.py`: records `pin_rounds = 600000` with new salted staff PIN hashes, upgrades the legacy database schema if necessary, and works with old/new bot installations.
- `prototype.py`: checks PIN hashes using the recorded number of PBKDF2 iterations (or legacy 200000), honors operator permissions for queue display, and clarifies daily-limit errors.
- `schema.sql`: reflects the new columns and tables required by final bot code.
- `make_test_data.py`: records the correct PIN round count for generated accounts, uses a stable project-relative schema path, and retains synthetic data generation.
- `backup.py`: resolves local database/backup paths relative to the project folder and fails clearly if the database is absent.
- `.gitignore`, `README.md`, `README_RU.md`: updated for safe GitHub use and accurate run instructions.
- `tests/test_database_compat.py`: offline compatibility tests for staff authentication, sensor readings and daily ticket limits.

The original ZIP's local `queue.sqlite` and `backup/` contents are intentionally **not included** in the GitHub-ready archive. They can contain data not meant to be published and are regenerated locally as needed.

## Testing performed

- Python syntax compilation for all `.py` files.
- Synthetic database creation, schema checks, simulated sensor reading, staff add/update, PIN verification by both bot and CLI prototype, and daily ticket-limit checks.
- Migration check using an isolated copy of the original ZIP's SQLite database.
- Offline unit tests: three passed.
- Bytewise comparison: archive `bot.py` equals the separately provided teammate file.

**Not tested:** live Telegram login, callback/message delivery, actual Telegram API or hosting. Test these manually before the individual defense.

## GitHub authorship

When committing changes, make sure authorship is accurate. Do not fabricate teammate commits. If the teammate wrote `bot.py`, they should ideally commit/push that file using their own GitHub account; support-script and documentation changes should be attributed to the person who actually made/reviewed them.
