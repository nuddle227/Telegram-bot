"""Offline smoke tests for the final project support scripts.

Run after installing requirements:
    python -m unittest discover -s tests -v

Uses a temporary database and never accesses a real Telegram account or network.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import bot
import add_staff
import prototype
import sensor_sim


class DatabaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "queue.sqlite")
        self.old_paths = (bot.DB_PATH, add_staff.DB_PATH, prototype.DB_PATH, sensor_sim.DB_PATH)
        env = {**os.environ, "DB_PATH": self.db_path}
        subprocess.run([sys.executable, str(PROJECT / "make_test_data.py")],
                       check=True, env=env, capture_output=True, text=True)
        bot.DB_PATH = add_staff.DB_PATH = prototype.DB_PATH = sensor_sim.DB_PATH = self.db_path
        bot.ensure_schema()
        bot.ensure_limit_trigger()

    def tearDown(self):
        bot.DB_PATH, add_staff.DB_PATH, prototype.DB_PATH, sensor_sim.DB_PATH = self.old_paths
        self.tmp.cleanup()

    def test_sensor_reading_visible_to_bot(self):
        sensor_sim.simulate(once=True)
        count = bot.corridor_now()
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 0)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0], 1)

    def test_staff_pin_in_bot_and_prototype(self):
        with patch("builtins.input", side_effect=["777888999", "Test Operator", "operator", "C R"]), \
                patch("getpass.getpass", return_value="890123"):
            add_staff.main()
        self.assertIsNotNone(bot.verify_pin(777888999, "890123"))
        with prototype.connect() as db:
            self.assertIsNotNone(prototype.check_staff(db, 777888999, "890123"))
        with sqlite3.connect(self.db_path) as db:
            rounds = db.execute("SELECT pin_rounds FROM staff WHERE telegram_id = 777888999").fetchone()[0]
        self.assertEqual(rounds, 600_000)

    def test_registration_and_daily_limit(self):
        telegram_id = 777888111
        self.assertTrue(bot.register_student(telegram_id, "Test Student", "SE-2619"))
        for _ in range(3):
            self.assertEqual(bot.take_ticket(telegram_id, "C"), "ok")
            self.assertIsNotNone(bot.my_ticket(telegram_id))
            self.assertTrue(bot.cancel_ticket(telegram_id))
        self.assertEqual(bot.take_ticket(telegram_id, "C"), "limit")
        self.assertEqual(len(bot.history_today(telegram_id)[0]), 3)


if __name__ == "__main__":
    unittest.main()
