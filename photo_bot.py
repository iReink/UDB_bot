"""Telegram ingestion and the single durable album worker."""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from aiogram import BaseMiddleware, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import photo_albums as store
from settings import ADMIN_IDS

log = logging.getLogger(__name__)


class PhotoMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            await asyncio.to_thread(store.enqueue, event)
        except Exception:
            log.exception('Photo enqueue failed: chat=%s message=%s', event.chat.id, event.message_id)
        return await handler(event, data)


def register(dp):
    dp.message.outer_middleware(PhotoMiddleware())

    @dp.message(Command('set_photo_topic'))
    async def set_topic(message):
        if message.sender_chat or not message.from_user or message.from_user.id not in ADMIN_IDS:
            await message.answer('Настройка доступна только админам бота.')
            return
        if message.chat.type != 'supergroup' or not message.message_thread_id:
            await message.answer('Отправьте /set_photo_topic внутри нужной темы супергруппы.')
            return
        await asyncio.to_thread(store.configure_topic, message.chat.id, message.message_thread_id, message.from_user.id)
        await message.answer(f'Фототема настроена ✅\nchat_id: {message.chat.id}\nmessage_thread_id: {message.message_thread_id}')

    @dp.callback_query(F.data.startswith('photos:'))
    async def photo_action(callback):
        try:
            _, bid, action = callback.data.split(':')
            await asyncio.to_thread(store.choose, int(bid), callback.from_user.id,
                callback.from_user.id in ADMIN_IDS,
                int(action) if action.isdigit() else None, action == 'cancel')
            if action == 'cancel':
                await asyncio.to_thread(store.cleanup)
            await callback.answer('Готово')
        except (ValueError, PermissionError) as exc:
            await callback.answer(str(exc), show_alert=True)


def claim():
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        p = c.execute('''SELECT p.* FROM daily_photos p JOIN photo_batches b ON b.id=p.batch_id
          WHERE p.status='queued' AND p.retry_after<=? AND b.touched_at<=?
          AND b.decision NOT IN ('cancelled','expired') ORDER BY p.id LIMIT 1''', (time.time(),time.time()-2)).fetchone()
        if p:
            c.execute("UPDATE daily_photos SET status='processing',attempts=attempts+1 WHERE id=?", (p['id'],))
            return dict(p)


async def process(bot, p):
    prefix = str(p['id'])
    source = store.MEDIA_ROOT / (prefix+'.download')
    target = store.MEDIA_ROOT / (prefix+'.jpg')
    thumb = store.MEDIA_ROOT / (prefix+'.webp')
    proc = None
    try:
        if shutil.disk_usage(store.MEDIA_ROOT).free < store.MIN_FREE:
            raise OSError('Недостаточно свободного места; загрузка будет повторена позже.')
        file = await bot.get_file(p['file_id'])
        if (file.file_size or 0) > store.MAX_FILE:
            raise ValueError('Файл больше 20 МБ. Пришлите его как обычное фото.')
        # Limit even when Telegram omitted file_size.
        class LimitedWriter:
            def __init__(self, stream):
                self.stream, self.count = stream, 0
            def write(self, chunk):
                self.count += len(chunk)
                if self.count > store.MAX_FILE:
                    raise ValueError('Файл больше 20 МБ. Пришлите его как обычное фото.')
                return self.stream.write(chunk)
            def flush(self):
                self.stream.flush()
            def seek(self, *args):
                return self.stream.seek(*args)
        with source.open('wb') as stream:
            await bot.download_file(file.file_path, destination=LimitedWriter(stream), timeout=60, seek=False)
        proc = await asyncio.create_subprocess_exec(sys.executable, str(store.ROOT/'photo_convert.py'),
            str(source),str(target),str(thumb),p['kind'],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=40)
        if proc.returncode:
            raise ValueError(stderr.decode(errors='replace').strip() or 'Изображение слишком сложное для обработки.')
        result = json.loads(stdout)
        with store.connection() as c:
            cur = c.execute('''UPDATE daily_photos SET status='ready',path=?,thumb_path=?,width=?,height=?,stored_size=?,error=NULL
              WHERE id=? AND status='processing' ''',
              (target.name,thumb.name,result['width'],result['height'],result['size'],p['id']))
            keep = bool(cur.rowcount)
            c.execute('UPDATE photo_batches SET dirty=1 WHERE id=?', (p['batch_id'],))
        if not keep:
            target.unlink(missing_ok=True)
            thumb.unlink(missing_ok=True)
    except BaseException as exc:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.communicate()
        target.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
        if isinstance(exc, asyncio.CancelledError):
            raise
        permanent = isinstance(exc, ValueError) or p['attempts'] >= 2
        if isinstance(exc, OSError) and shutil.disk_usage(store.MEDIA_ROOT).free < store.MIN_FREE:
            permanent = False
        error = str(exc) if isinstance(exc,(ValueError,OSError)) else 'Не удалось загрузить фото. Попробуйте прислать его ещё раз.'
        with store.connection() as c:
            c.execute("UPDATE daily_photos SET status=?,error=?,retry_after=? WHERE id=? AND status='processing'",
                      ('failed' if permanent else 'queued',error[:300],time.time()+60,p['id']))
            c.execute('UPDATE photo_batches SET dirty=1 WHERE id=?', (p['batch_id'],))
        log.warning('Photo processing failed: id=%s error=%s', p['id'],type(exc).__name__)
    finally:
        source.unlink(missing_ok=True)


def notice(batch_id):
    store.decide(batch_id)
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        b = dict(c.execute('SELECT * FROM photo_batches WHERE id=?', (batch_id,)).fetchone())
        rows = c.execute('SELECT status,error FROM daily_photos WHERE batch_id=?', (batch_id,)).fetchall()
        count = sum(r['status']=='ready' for r in rows)
        buttons = []
        if b['decision'] in ('cancelled','expired'):
            text = 'Загрузка отменена. Копии фотографий удалены.' if b['decision']=='cancelled' else 'Срок выбора дейлика истёк. Копии фотографий удалены; оригиналы остались в Telegram.'
        elif count == 0:
            text = 'В этой загрузке пока нет сохранённых фотографий.'
        elif b['decision']=='attached':
            daily = c.execute('SELECT name FROM daily_events WHERE id=? AND chat_id=?', (b['daily_id'],b['chat_id'])).fetchone()
            if not daily:
                c.execute("UPDATE photo_batches SET decision='pending',daily_id=NULL,dirty=1 WHERE id=?", (batch_id,))
                return None
            text = f'Фотографии ({count}) успешно привязаны к дейлику {daily[0]}'
            token = store.album_token(c,b['daily_id'],b['chat_id'])
            base = os.environ.get('PHOTO_PUBLIC_BASE_URL','').rstrip('/')
            if base and count:
                buttons.append([InlineKeyboardButton(text='Открыть в браузере',url=base+'/albums/'+token)])
        else:
            text = f'Уточните к какому дейлику привязать фотографии ({count})'
            options = store.choices(c,b)
            for r in options:
                buttons.append([InlineKeyboardButton(text=f"{r['date']} {r['time']} · {r['name']}"[:64],callback_data=f"photos:{batch_id}:{r['id']}")])
            if not options:
                text += '\nПодходящих дейликов нет. Создайте дейлик и обновите список.'
            buttons.append([InlineKeyboardButton(text='Обновить список',callback_data=f'photos:{batch_id}:refresh')])
        if b['decision'] not in ('cancelled','expired'):
            buttons.append([InlineKeyboardButton(text='Не привязывать',callback_data=f'photos:{batch_id}:cancel')])
        errors = sorted({r['error'] for r in rows if r['error']})
        if errors:
            text += '\n\nНе сохранено: '+str(sum(r['status']!='ready' for r in rows))+'.\n'+'\n'.join(errors)[:800]
        # Clear before I/O: concurrent late messages/callbacks set dirty again.
        c.execute('UPDATE photo_batches SET dirty=0 WHERE id=?', (batch_id,))
        return b,text,InlineKeyboardMarkup(inline_keyboard=buttons)


async def notify(bot, bid):
    data = await asyncio.to_thread(notice,bid)
    if not data:
        return
    b,text,markup = data
    try:
        if b['reply_id']:
            await bot.edit_message_text(text,chat_id=b['chat_id'],message_id=b['reply_id'],reply_markup=markup,parse_mode=None)
        else:
            msg = await bot.send_message(b['chat_id'],text,message_thread_id=b['thread_id'],
                reply_to_message_id=b['first_message_id'],allow_sending_without_reply=True,reply_markup=markup,parse_mode=None)
            with store.connection() as c:
                c.execute('UPDATE photo_batches SET reply_id=? WHERE id=?',(msg.message_id,bid))
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc):
            with store.connection() as c:
                if "message to edit not found" in str(exc):
                    c.execute('UPDATE photo_batches SET reply_id=NULL WHERE id=?',(bid,))
            retry_notice(bid,60)
            log.warning('Album notice rejected: batch=%s',bid)
    except Exception as exc:
        retry_notice(bid, max(10,exc.retry_after) if isinstance(exc,TelegramRetryAfter) else 60)


def retry_notice(bid,delay):
    with store.connection() as c:
        c.execute('UPDATE photo_batches SET dirty=1,notify_after=? WHERE id=?',(time.time()+delay,bid))


async def worker(bot):
    with store.connection() as c:
        c.execute("UPDATE daily_photos SET status='queued' WHERE status='processing'")
        c.execute('UPDATE photo_batches SET dirty=1')
    last_cleanup = 0
    while True:
        try:
            if time.time()-last_cleanup > 60:
                await asyncio.to_thread(store.cleanup)
                last_cleanup=time.time()
            p = await asyncio.to_thread(claim)
            if p:
                await process(bot,p)
            with store.connection() as c:
                batches = c.execute('''SELECT id FROM photo_batches b WHERE dirty=1 AND touched_at<? AND notify_after<=?
                  AND NOT EXISTS(SELECT 1 FROM daily_photos p WHERE p.batch_id=b.id AND p.status='processing')
                  AND NOT EXISTS(SELECT 1 FROM daily_photos p WHERE p.batch_id=b.id AND p.status='queued' AND p.error IS NULL)
                  ORDER BY id LIMIT 1''',(time.time()-2,time.time())).fetchall()
            for b in batches:
                await notify(bot,b['id'])
                await asyncio.sleep(3)  # stay below group send limits
            if not p:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Album worker iteration failed')
            await asyncio.sleep(5)
