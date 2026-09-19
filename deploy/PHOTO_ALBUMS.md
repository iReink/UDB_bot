# Daily photo albums

Install `requirements-photos.txt` into the bot venv (also used by the web service).
Back up SQLite with its backup API and run integrity_check before migration.
Run `photo_albums.ensure_schema()` before starting the updated bot/web.
Migration is additive; do not restore an old database after accepting new uploads.

Install `udb-photos.conf` as a systemd drop-in for udb-bot.service. Set
PHOTO_PUBLIC_BASE_URL to the public web origin (no trailing path). The current
deployment exposes HTTP on port 8080; links, photos and authentication are not
encrypted in transit. Configure HTTPS before treating this as private storage.

No topics are enabled by the migration. A bot admin must send /set_photo_topic
inside the desired supergroup topic. The previous /thread setting is independent.
Only new incoming messages are collected; original Telegram messages are never deleted.

Files live in daily_photo_media, not web_chat_media. Include this folder and the
database in coordinated backups. Unassigned uploads expire after seven days;
attached photos have no expiry. Disk below 512 MiB pauses downloads and retries.
One conversion process runs at a time, with 512 MiB address-space, 25 s CPU,
40 s wall-clock and 50 MP input limits. The normal bot cgroup includes this child.

Run `python -m unittest discover -s tests -p test_photo_albums.py -v` (httpx needed
for tests). Verify a real photo/album after enabling, including browser access,
owner/admin deletion and a future/ambiguous daily. Monitor worker warnings,
queue backlog, RSS, cgroup CPUUsageNSec, free disk and update latency.

Rollback: stop bot/web, restore backed-up source, restart. Preserve database and
daily_photo_media; additive tables can stay. The deletion guard trigger continues
protecting albums. Existing pending work resumes when updated code is restored.
