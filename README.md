# StudyFlow — multi-user release

## Bothost configuration

- Start command: `python start.py`
- Web port: `3000` (or the platform-provided `PORT`)
- `DATABASE_PATH=/app/data/studyflow.db`
- `DATA_DIR=/app/data`
- `SESSION_SECRET` — long random secret (at least 32 random characters)
- `TELEGRAM_BOT_TOKEN` — token from BotFather
- `COOKIE_SECURE=1`

Install dependencies from `requirements.txt`. Keep `/app/data` on persistent storage. The SQLite database and uploaded images are stored there.

## First deployment and migration

1. Back up `/app/data/studyflow.db` before deploying.
2. Upload the contents of this release to the project, preserving `static/index.html`.
3. Set the environment variables above and run `python start.py`.
4. Register the first account. Existing single-user habits, tasks, lessons, grades, reminder settings and Telegram link are assigned to this first account automatically. Later accounts start empty.
5. Each additional user registers independently and links their own Telegram chat from their own account.

Registration requires a username (3–32 characters: letters, numbers, `_`, `.`, `-`) and password of at least 8 characters.

## Features

- Per-user accounts and data isolation.
- Habits, completion history, school timetable, grades and progress chart.
- Homework image attachments (JPG/PNG/WEBP, max 8 MB each).
- Personal cheat-sheet image library.
- Per-user Telegram chat binding and reminders.
- Reminder intervals: 1, 2, 3, 4, 6, 8, 12 or 24 hours, with a user-selected starting time and timezone.

Only one application worker should run because the Telegram long-polling bot is launched in the same process.
