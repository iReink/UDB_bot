"""Durable photo album storage. No Telegram/web imports or startup side effects."""
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import db

ROOT = Path(__file__).resolve().parent
MEDIA_ROOT = ROOT / 'daily_photo_media'
TZ = ZoneInfo('Asia/Yekaterinburg')
MAX_FILE = 20_000_000
MIN_FREE = 512 * 1024 * 1024


@contextmanager
def connection():
    conn = db.get_connection()
    conn.create_function('CASEFOLD', 1, lambda s: str(s or '').casefold(), deterministic=True)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_schema():
    MEDIA_ROOT.mkdir(exist_ok=True)
    with connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS photo_topics (
          chat_id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL,
          configured_by INTEGER NOT NULL, configured_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS photo_batches (
          id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
          user_id INTEGER, sender_chat_id INTEGER, group_key TEXT NOT NULL,
          first_message_id INTEGER NOT NULL, sent_at REAL NOT NULL, touched_at REAL NOT NULL,
          daily_id INTEGER, decision TEXT NOT NULL DEFAULT 'new', reply_id INTEGER,
          dirty INTEGER NOT NULL DEFAULT 1, notify_after REAL NOT NULL DEFAULT 0,
          UNIQUE(chat_id, group_key));
        CREATE TABLE IF NOT EXISTS daily_photo_albums (
          daily_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, token TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS daily_photos (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
          user_id INTEGER, sender_chat_id INTEGER, message_id INTEGER NOT NULL,
          file_id TEXT NOT NULL, file_unique_id TEXT, kind TEXT NOT NULL,
          file_size INTEGER, sent_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
          path TEXT, thumb_path TEXT, width INTEGER, height INTEGER, stored_size INTEGER,
          error TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_after REAL NOT NULL DEFAULT 0,
          UNIQUE(chat_id, message_id));
        CREATE INDEX IF NOT EXISTS idx_photos_batch ON daily_photos(batch_id,status);
        CREATE INDEX IF NOT EXISTS idx_photos_queue ON daily_photos(status,retry_after);
        CREATE INDEX IF NOT EXISTS idx_batches_daily ON photo_batches(daily_id,chat_id);
        CREATE INDEX IF NOT EXISTS idx_batches_pending ON photo_batches(decision,sent_at);
        CREATE INDEX IF NOT EXISTS idx_daily_photo_dates ON daily_events(chat_id,date,time,id);
        CREATE TRIGGER IF NOT EXISTS protect_daily_photos BEFORE DELETE ON daily_events
        WHEN EXISTS (SELECT 1 FROM photo_batches b JOIN daily_photos p ON p.batch_id=b.id
          WHERE b.daily_id=OLD.id AND b.chat_id=OLD.chat_id AND p.status IN ('ready','queued','processing'))
        BEGIN SELECT RAISE(ABORT, 'Сначала удалите фотографии дейлика'); END;
        CREATE TRIGGER IF NOT EXISTS forget_deleted_daily_album AFTER DELETE ON daily_events
        BEGIN
          DELETE FROM daily_photo_albums WHERE daily_id=OLD.id AND chat_id=OLD.chat_id;
          UPDATE photo_batches SET daily_id=NULL,decision='cancelled',dirty=1
            WHERE daily_id=OLD.id AND chat_id=OLD.chat_id;
        END;
        ''')


def configure_topic(chat_id, thread_id, user_id):
    with connection() as c:
        c.execute('INSERT OR REPLACE INTO photo_topics VALUES (?,?,?,?)',
                  (chat_id, thread_id, user_id, time.time()))


def enqueue(message):
    """Called by outer middleware, so documents/FSM handlers cannot bypass collection."""
    if any(getattr(message, key, None) for key in ('animation', 'video', 'sticker', 'video_note')):
        return False
    photo = message.photo[-1] if message.photo else None
    document = message.document
    if not photo and not document:
        return False
    with connection() as c:
        topic = c.execute('SELECT thread_id FROM photo_topics WHERE chat_id=?', (message.chat.id,)).fetchone()
        if not topic or topic[0] != message.message_thread_id:
            return False
        c.execute('BEGIN IMMEDIATE')
        sender = message.sender_chat
        uid = message.from_user.id if message.from_user and not sender else None
        sender_id = sender.id if sender else None
        file = photo or document
        group_key = 'g:' + str(message.media_group_id) if message.media_group_id else 'm:' + str(message.message_id)
        now = time.time()
        c.execute('''INSERT OR IGNORE INTO photo_batches
          (chat_id,thread_id,user_id,sender_chat_id,group_key,first_message_id,sent_at,touched_at)
          VALUES (?,?,?,?,?,?,?,?)''', (message.chat.id, message.message_thread_id, uid,
            sender_id, group_key, message.message_id, message.date.timestamp(), now))
        batch = c.execute('SELECT * FROM photo_batches WHERE chat_id=? AND group_key=?',
                          (message.chat.id, group_key)).fetchone()
        status = 'cancelled' if batch['decision'] in ('cancelled', 'expired') else 'queued'
        error = None
        if status == 'queued' and (file.file_size or 0) > MAX_FILE:
            status, error = 'failed', 'Файл больше 20 МБ. Пришлите его как обычное фото.'
        cur = c.execute('''INSERT OR IGNORE INTO daily_photos
          (batch_id,chat_id,user_id,sender_chat_id,message_id,file_id,file_unique_id,kind,file_size,sent_at,status,error)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (batch['id'], message.chat.id, uid, sender_id,
          message.message_id, file.file_id, file.file_unique_id, 'photo' if photo else 'document',
          file.file_size, message.date.timestamp(), status, error))
        if cur.rowcount:
            c.execute('UPDATE photo_batches SET touched_at=?,dirty=1 WHERE id=?', (now, batch['id']))
    return True


def candidates(c, chat_id, sent_at):
    at = datetime.fromtimestamp(sent_at, TZ)
    rows = c.execute('''SELECT id,name,date,time FROM daily_events WHERE chat_id=?
       AND date BETWEEN ? AND ? ORDER BY date,time,id''',
       (chat_id, (at.date()-timedelta(days=1)).isoformat(), at.date().isoformat())).fetchall()
    return [r for r in rows if event_time(r) <= at]


def event_time(row):
    return datetime.strptime(row['date']+' '+row['time'], '%Y-%m-%d %H:%M').replace(tzinfo=TZ)


def choices(c, batch):
    at = datetime.fromtimestamp(batch['sent_at'], TZ)
    key = at.strftime('%Y-%m-%d %H:%M')
    past = c.execute('''SELECT id,name,date,time FROM daily_events WHERE chat_id=?
       AND date||' '||time<=? ORDER BY date DESC,time DESC,id DESC LIMIT 5''', (batch['chat_id'], key)).fetchall()
    future = c.execute('''SELECT id,name,date,time FROM daily_events WHERE chat_id=?
       AND date||' '||time>? AND date||' '||time<=? ORDER BY date,time,id''',
       (batch['chat_id'], key, (at+timedelta(hours=24)).strftime('%Y-%m-%d %H:%M'))).fetchall()
    return past + future


def album_token(c, daily_id, chat_id):
    c.execute('INSERT OR IGNORE INTO daily_photo_albums VALUES (?,?,?)',
              (daily_id, chat_id, secrets.token_urlsafe(32)))
    return c.execute('SELECT token FROM daily_photo_albums WHERE daily_id=? AND chat_id=?',
                     (daily_id, chat_id)).fetchone()[0]


def decide(batch_id):
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        b = c.execute('SELECT * FROM photo_batches WHERE id=?', (batch_id,)).fetchone()
        if b['decision'] != 'new':
            return
        if not c.execute("SELECT 1 FROM daily_photos WHERE batch_id=? AND status='ready' LIMIT 1", (batch_id,)).fetchone():
            return
        found = candidates(c, b['chat_id'], b['sent_at'])
        daily_id = found[0]['id'] if len(found) == 1 else None
        if daily_id:
            album_token(c, daily_id, b['chat_id'])
        c.execute('UPDATE photo_batches SET decision=?,daily_id=?,dirty=1 WHERE id=?',
                  ('attached' if daily_id else 'pending', daily_id, batch_id))


def choose(batch_id, user_id, is_admin, daily_id=None, cancel=False):
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        b = c.execute('SELECT * FROM photo_batches WHERE id=?', (batch_id,)).fetchone()
        if not b or not (is_admin or b['user_id'] == user_id):
            raise PermissionError('Эта загрузка доступна только отправителю и админам бота')
        if b['decision'] in ('cancelled', 'expired'):
            raise ValueError('Загрузка уже отменена или истекла')
        if cancel:
            c.execute("UPDATE photo_batches SET decision='cancelled',daily_id=NULL,dirty=1 WHERE id=?", (batch_id,))
            c.execute("UPDATE daily_photos SET status='deleted' WHERE batch_id=?", (batch_id,))
        elif daily_id is not None:
            if not c.execute("SELECT 1 FROM daily_photos WHERE batch_id=? AND status='ready' LIMIT 1", (batch_id,)).fetchone():
                raise ValueError('В загрузке нет сохранённых фотографий')
            if b['decision'] == 'attached':
                raise ValueError('Фотографии уже привязаны')
            if daily_id not in {r['id'] for r in choices(c, b)}:
                raise ValueError('Дейлик больше недоступен. Обновите список')
            album_token(c, daily_id, b['chat_id'])
            c.execute("UPDATE photo_batches SET decision='attached',daily_id=?,dirty=1 WHERE id=?", (daily_id, batch_id))
        else:
            c.execute('UPDATE photo_batches SET dirty=1 WHERE id=?', (batch_id,))


def safe_path(value):
    path = (MEDIA_ROOT / value).resolve()
    path.relative_to(MEDIA_ROOT.resolve())
    return path


def cleanup():
    with connection() as c:
        c.execute("UPDATE photo_batches SET decision='expired',dirty=1 WHERE decision IN ('new','pending') AND sent_at<?",
                  (time.time()-7*86400,))
        c.execute("UPDATE daily_photos SET status='deleted' WHERE batch_id IN (SELECT id FROM photo_batches WHERE decision='expired')")
        rows = c.execute("SELECT id,path,thumb_path FROM daily_photos WHERE status='deleted' AND (path IS NOT NULL OR thumb_path IS NOT NULL)").fetchall()
    for r in rows:
        for key in ('path', 'thumb_path'):
            if r[key]:
                safe_path(r[key]).unlink(missing_ok=True)
        with connection() as c:
            c.execute('UPDATE daily_photos SET path=NULL,thumb_path=NULL WHERE id=?', (r['id'],))


def daily_summary(daily_id, chat_id):
    with connection() as c:
        row = c.execute('''SELECT COUNT(p.id) n,a.token FROM daily_photo_albums a
          JOIN photo_batches b ON b.daily_id=a.daily_id AND b.chat_id=a.chat_id
          JOIN daily_photos p ON p.batch_id=b.id AND p.status='ready'
          WHERE a.daily_id=? AND a.chat_id=? AND b.decision='attached' GROUP BY a.token''', (daily_id, chat_id)).fetchone()
    return {'photo_count': row['n'] if row else 0,
            'album_url': '/albums/'+row['token'] if row else None}


def has_protected_photos(daily_id, chat_id):
    with connection() as c:
        return c.execute('''SELECT 1 FROM photo_batches b JOIN daily_photos p ON p.batch_id=b.id
          WHERE b.daily_id=? AND b.chat_id=? AND p.status IN ('ready','queued','processing') LIMIT 1''',
          (daily_id,chat_id)).fetchone() is not None


def delete_photo(photo_id, user_id, is_admin):
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        p = c.execute('SELECT * FROM daily_photos WHERE id=?', (photo_id,)).fetchone()
        if not p:
            raise LookupError('Фото не найдено')
        if not (is_admin or p['user_id'] == user_id):
            raise PermissionError('Удалять фото может отправитель или админ бота')
        c.execute("UPDATE daily_photos SET status='deleted' WHERE id=?", (photo_id,))
        c.execute('UPDATE photo_batches SET dirty=1 WHERE id=?', (p['batch_id'],))
    cleanup()
