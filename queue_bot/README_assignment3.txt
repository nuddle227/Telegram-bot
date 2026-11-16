Dean's Office Queue - Assignment 3 prototype (SE-2619)

Requirements: Python 3.8+ (standard library only). No installation needed.

1. Create the database with test data (or use the supplied queue.sqlite):
       python make_test_data.py
2. Run the prototype (examples):
       python prototype.py services
       python prototype.py register --tg 700100200 --name "Aigerim Sadykova" --group SE-2619
       python prototype.py take --tg 700100200 --service C
       python prototype.py my --tg 700100200
       python prototype.py cancel --tg 700100200
   Staff commands (Telegram ID + PIN are checked against the staff table):
       python prototype.py queue    --tg 100007919 --pin 482915 --service C
       python prototype.py call     --tg 100007919 --pin 482915 --service C
       python prototype.py complete --tg 100007919 --pin 482915 --service C
       python prototype.py skip     --tg 100007919 --pin 482915 --service C
       python prototype.py stats    --tg 100007919 --pin 482915

Test staff accounts (test data only):
   administrator  Telegram ID 100007919  PIN 482915
   operator       Telegram ID 100015838  PIN 736204   (works with Certificate only)
Set DB_PATH to use another database file, e.g. DB_PATH=copy.sqlite

Queue date: the program works with today's date. If the database still has an active
queue from an earlier day (as in the supplied test data), it continues that queue,
so the demo works on any day. To start a fresh queue for today, run: python make_test_data.py
