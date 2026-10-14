"""Durable Telegram voice intake and one sleeping transcription worker."""
import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import time
import uuid
from contextlib import closing
from pathlib import Path

import db
import ai_http
import ai_providers
import ai_runtime

MODEL = 'gemini-3.5-transcribe'
WAKE = asyncio.Event()
PREFIX = re.compile(r'^\s*(?:бот|вот)\b[\s,.:;!?—–-]*', re.I)
MAX_BYTES = 14_000_000  # base64 plus JSON must fit the API's 20 MB request limit


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_voice_transcripts (
        chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
        source_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
        transcript TEXT,attempts INTEGER NOT NULL DEFAULT 0,
        retry_at REAL NOT NULL DEFAULT 0,error TEXT,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        PRIMARY KEY(chat_id,message_id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_voice_ready ON ai_voice_transcripts(status,retry_at)')


def enqueue(message):
    if not message.voice or not message.from_user or message.from_user.is_bot:
        return False
    with closing(db.get_connection()) as conn, conn:
        return bool(conn.execute('''INSERT OR IGNORE INTO ai_voice_transcripts
            (chat_id,message_id,source_json,created_at,updated_at) VALUES(?,?,?,?,?)''',
            (message.chat.id,message.message_id,message.model_dump_json(exclude_none=True),
             ai_runtime.stamp(),ai_runtime.stamp())).rowcount)


def change(row, **values):
    allowed = {'status','transcript','attempts','retry_at','error'}
    if not values or not set(values) <= allowed:
        raise ValueError('Unknown voice state field')
    with closing(db.get_connection()) as conn, conn:
        conn.execute('UPDATE ai_voice_transcripts SET '+','.join(k+'=?' for k in values)
                     +',updated_at=? WHERE chat_id=? AND message_id=?',
                     (*values.values(),ai_runtime.stamp(),row['chat_id'],row['message_id']))


def addressed(message, text, bot_id):
    prefix = PREFIX.match(text)
    reply = message.reply_to_message
    own = (reply and reply.chat.id == message.chat.id and reply.from_user
           and reply.from_user.id == bot_id)
    return ('reply_to_bot' if own else 'mention_bot_word' if prefix else None,
            text[prefix.end():].strip() if prefix else text)


def extract(body):
    text = ''.join(part.get('text','') for step in body.get('steps',[])
                   if step.get('type')=='model_output' for part in step.get('content',[])
                   if part.get('type')=='text').strip()
    if body.get('status') != 'completed' or not text:
        raise ai_providers.ProviderUnavailable('Transcribe returned no speech',kind='permanent')
    return text


def transcribe(audio, duration, chat_id, message_id):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env.ai',override=False)
    key = os.getenv('GEMINI_API_KEY','').strip()
    if not key:
        raise ai_providers.ProviderUnavailable('Transcribe key unavailable',kind='permanent')
    count = max(128,math.ceil(duration*32))
    if count > ai_runtime.model_limits(MODEL)['TPM']:
        raise ai_providers.ProviderUnavailable('Запись слишком длинная для минутного бюджета распознавания.',kind='permanent')
    wait = ai_runtime.budget_wait(MODEL,count)
    if wait:
        raise ai_providers.ProviderUnavailable('Transcribe quota',wait,False,ai_runtime.budget_kind(MODEL,count))
    if not ai_runtime.model_ready(MODEL):
        raise ai_providers.ProviderUnavailable('Transcribe temporarily unavailable',30,False,'minute')
    reservation = ai_runtime.reserve(MODEL,count,2048)
    if reservation is None:
        raise ai_providers.ProviderUnavailable('Transcribe quota',60,False,'minute')
    payload = {'model':MODEL,'input':[{'type':'audio','mime_type':'audio/ogg',
                'data':base64.b64encode(audio).decode()}],
               'generation_config':{'transcription_config':{'mode':{'type':'verbatim'}}}}
    event = {'at':ai_runtime.stamp(),'provider':'google','model':MODEL,
             'context':{'operation':'transcribe','chat_id':chat_id,'message_id':message_id,
                        'audio_bytes':len(audio),'duration_seconds':duration},'status':'error'}
    metadata = {'provider':'google','model':MODEL}
    try:
        response = ai_http.post('https://generativelanguage.googleapis.com/v1beta/interactions',
                                headers={'x-goog-api-key':key},json=payload,timeout=(5,90))
        body = response.json()
        if not response.ok:
            kind = ai_providers.refusal_kind(response,body)
            delay = ai_providers.retry_delay(response,body)
            ai_runtime.cool_model(MODEL,delay,kind)
            event['error'] = 'HTTP '+str(response.status_code)
            raise ai_providers.ProviderUnavailable('Transcribe HTTP '+str(response.status_code),delay,False,kind)
        event.update(status='received',response=body)
        usage = body.get('usage') or body.get('usageMetadata') or {}
        normalized = {'prompt_tokens':usage.get('total_input_tokens',usage.get('promptTokenCount',count)),
                      'completion_tokens':usage.get('total_output_tokens',usage.get('candidatesTokenCount',0))}
        ai_runtime.settle(reservation,normalized)
        metadata['usage'] = normalized
        return extract(body)
    except Exception as exc:
        event.setdefault('error',type(exc).__name__)
        raise
    finally:
        try:
            ai_runtime.record_attempt('tasks',-1,uuid.uuid4().hex,
                                      {**metadata,'calls':[event]},'voice_transcribe')
        except Exception:
            logging.warning('Transcribe audit unavailable chat=%s message=%s',chat_id,message_id)


def save_text(row, text):
    with closing(db.get_connection()) as conn, conn:
        conn.execute("UPDATE ai_voice_transcripts SET status='ready',transcript=?,error=NULL,updated_at=? WHERE chat_id=? AND message_id=?",
                     (text,ai_runtime.stamp(),row['chat_id'],row['message_id']))
        source = json.loads(row['source_json'])
        user = (source.get('from_user') or source.get('from'))['id']
        conn.execute('UPDATE messages_reactions SET message_text=? WHERE chat_id=? AND message_id=? AND user_id=?',
                     (text,row['chat_id'],row['message_id'],user))


async def reply_text(bot, message, text):
    from aiogram.types import ReplyParameters
    for start in range(0,len(text),4000):
        await bot.send_message(message.chat.id,text[start:start+4000],parse_mode=None,
                               message_thread_id=message.message_thread_id,
                               reply_parameters=ReplyParameters(message_id=message.message_id,
                                                                allow_sending_without_reply=True))


async def deliver(bot, message, row, pipeline):
    trigger, text = addressed(message,row['transcript'],bot.id)
    if trigger and text and await asyncio.to_thread(ai_runtime.enabled,message.chat.id):
        # Backend type-check creation is idempotent by the original Telegram ID.
        derived = message.model_copy(update={'text':text,'entities':None})
        accepted = await pipeline(derived,voice_trigger=trigger)
        if accepted is not False:
            await asyncio.to_thread(change,row,status='done')
            return
    # Fence before Telegram: ambiguous send must never be replayed after restart.
    await asyncio.to_thread(change,row,status='delivering')
    await reply_text(bot,message,row['transcript'])
    await asyncio.to_thread(change,row,status='done')


def claim():
    with closing(db.get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT * FROM ai_voice_transcripts WHERE status IN ('queued','ready') AND retry_at<=? ORDER BY created_at LIMIT 1",(time.time(),)).fetchone()
        if not row:
            due = conn.execute("SELECT min(retry_at) FROM ai_voice_transcripts WHERE status IN ('queued','ready')").fetchone()[0]
            return None,max(1,(due-time.time())) if due else None
        result = dict(row)
        conn.execute("UPDATE ai_voice_transcripts SET status=?,attempts=attempts+1 WHERE chat_id=? AND message_id=?",
                     ('processing' if row['status']=='queued' else 'ready',row['chat_id'],row['message_id']))
        result['attempts'] += 1
        return result,None


async def process(bot, row, pipeline):
    from aiogram.types import Message
    message = Message.model_validate_json(row['source_json'],context={'bot':bot})
    try:
        if row['status']=='queued':
            voice = message.voice
            if voice.duration > 3600 or (voice.file_size or 0) > MAX_BYTES:
                raise ai_providers.ProviderUnavailable('Голосовое превышает лимит загрузки или длительности.',kind='permanent')
            destination = io.BytesIO()
            await bot.download(voice.file_id,destination=destination)
            audio = destination.getvalue()
            if len(audio)>MAX_BYTES:
                raise ai_providers.ProviderUnavailable('Голосовое превышает лимит загрузки.',kind='permanent')
            text = await asyncio.to_thread(transcribe,audio,voice.duration,row['chat_id'],row['message_id'])
            await asyncio.to_thread(save_text,row,text)
            row['transcript'] = text
        await deliver(bot,message,row,pipeline)
    except Exception as exc:
        with closing(db.get_connection()) as conn:
            state = conn.execute('SELECT status FROM ai_voice_transcripts WHERE chat_id=? AND message_id=?',
                                 (row['chat_id'],row['message_id'])).fetchone()[0]
        if state=='delivering':
            await asyncio.to_thread(change,row,status='failed',error='Telegram delivery unknown; not repeated')
            return
        kind = getattr(exc,'kind','transport')
        if kind=='minute' or kind=='transport' and row['attempts']<2:
            await asyncio.to_thread(change,row,status='ready' if row.get('transcript') else 'queued',
                                    retry_at=time.time()+max(30,getattr(exc,'retry_after',30)),error=type(exc).__name__)
            return
        safe_error = str(exc) if str(exc).startswith(('Голосовое превышает','Запись слишком длинная')) else None
        notice = ('Суточный лимит распознавания голосовых исчерпан. Попробуйте после сброса квоты.'
                  if kind=='daily' else safe_error or 'Не удалось распознать голосовое сообщение. Попробуйте позже.')
        await asyncio.to_thread(change,row,status='delivering',error=type(exc).__name__)
        try:
            await reply_text(bot,message,notice)
        finally:
            await asyncio.to_thread(change,row,status='failed')


def recover():
    with closing(db.get_connection()) as conn, conn:
        ensure_schema(conn)
        conn.execute("UPDATE ai_voice_transcripts SET status='queued' WHERE status='processing'")
        conn.execute("UPDATE ai_voice_transcripts SET status='failed',error='Telegram delivery unknown; not repeated' WHERE status='delivering'")


async def worker(bot, pipeline):
    while True:
        try:
            await asyncio.to_thread(recover)
            break
        except Exception:
            logging.exception('Voice startup recovery deferred')
            await asyncio.sleep(5)
    while True:
        WAKE.clear()
        try:
            row,delay = await asyncio.to_thread(claim)
        except Exception:
            logging.exception('Voice queue read deferred')
            await asyncio.sleep(5)
            continue
        if row:
            try:
                await process(bot,row,pipeline)
            except Exception:
                logging.exception('Voice processing failed chat=%s message=%s',row['chat_id'],row['message_id'])
            continue
        try:
            if delay is None:
                await WAKE.wait()
            else:
                await asyncio.wait_for(WAKE.wait(),timeout=delay)
        except asyncio.TimeoutError:
            pass
