"""Telegram intake and an independent server-side image generation loop."""
import asyncio
from contextlib import closing
import json
import logging
import time
from types import SimpleNamespace

from aiogram import BaseMiddleware, F
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import imagegen as store

log=logging.getLogger(__name__)
bot_username=''


class ImageIntake(BaseMiddleware):
    async def __call__(self,handler,event,data):
        from ai_conversation import reply_image
        reference=reply_image(event,getattr(data.get('bot'),'id',None))
        if reference and not store.addressed(reference,bot_username):reference=None
        if event.photo or reference:
            try:
                await asyncio.to_thread(store.collect,reference or event,bot_username)
            except Exception:
                log.exception('Image intake failed')
        return await handler(event,data)


def register(dp):
    dp.message.outer_middleware(ImageIntake())
    @dp.callback_query(F.data.startswith('imagegen:'))
    async def choice(callback):
        try:
            _,bid,action=callback.data.split(':')
            if action not in ('first4','cancel') or not callback.message:
                raise ValueError('Некорректный выбор')
            await asyncio.to_thread(store.choose,int(bid),callback.from_user.id,callback.message.chat.id,action=='first4')
            await callback.answer('Принято' if action=='first4' else 'Отменено')
            await callback.message.edit_reply_markup(reply_markup=None)
        except ValueError as exc:
            await callback.answer(str(exc),show_alert=True)


async def collect_loop(bot):
    import photo_story
    from ai_runtime import enabled
    while True:
        try:
            with closing(store.connect()) as conn:
                batches=[dict(r) for r in conn.execute("""SELECT * FROM ai_imagegen_batches b WHERE
                    (status='collecting' AND touched_at<?) OR status='ready'
                    OR (status='confirm' AND notice_message_id IS NULL)
                    OR (status='typed' AND NOT EXISTS(SELECT 1 FROM ai_type_checks t
                      WHERE t.chat_id=b.chat_id AND t.request_message_id=b.first_message_id))
                    ORDER BY id LIMIT 20""",(time.time()-3,))]
            for batch in batches:
                with closing(store.connect()) as conn:
                    inputs=store.photos(conn,batch['id'])
                if not enabled(batch['chat_id']):
                    state='cancelled'
                elif not batch['addressed']:
                    if batch['private']:
                        for photo in inputs:
                            # Reuse the unchanged story queue after deciding the entire album's intent.
                            message=SimpleNamespace(chat=SimpleNamespace(id=batch['chat_id'],type='private'),
                                from_user=SimpleNamespace(id=batch['user_id']),message_id=photo['message_id'],
                                photo=[SimpleNamespace(file_id=photo['file_id'])],caption=photo['caption'],
                                media_group_id=batch['group_key'] if len(inputs)>1 else None)
                            result=await asyncio.to_thread(photo_story.enqueue,message)
                            if result=='busy':
                                await bot.send_message(batch['chat_id'],'Уже готовлю три истории. Дождитесь ответа перед новой подборкой.',parse_mode=None)
                                break
                    state='done'
                elif len(inputs)>4 and batch['status'] not in ('ready','typed'):
                    state='confirm'
                else:
                    state='typed'
                with closing(store.connect()) as conn, conn:
                    changed=conn.execute("UPDATE ai_imagegen_batches SET status=? WHERE id=? AND status IN ('collecting','ready','typed','confirm')",(state,batch['id'])).rowcount
                if not changed:
                    continue
                if state=='confirm':
                    keyboard=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text='Взять первые 4',callback_data=f"imagegen:{batch['id']}:first4"),
                        InlineKeyboardButton(text='Отмена',callback_data=f"imagegen:{batch['id']}:cancel")]])
                    notice=await bot.send_message(batch['chat_id'],'В работу можно взять только первые 4 фотографии. Использовать их?',
                        reply_to_message_id=batch['first_message_id'],allow_sending_without_reply=True,
                        reply_markup=keyboard,parse_mode=None)
                    with closing(store.connect()) as conn, conn:
                        conn.execute('UPDATE ai_imagegen_batches SET notice_message_id=? WHERE id=?',(notice.message_id,batch['id']))
                elif state=='typed':
                    try:
                        await asyncio.to_thread(store.queue_type,batch)
                    except Exception:
                        with closing(store.connect()) as conn, conn:
                            conn.execute("UPDATE ai_imagegen_batches SET status='ready' WHERE id=?",(batch['id'],))
                        raise
            # Typing a caption as something else must not consume a permanent pending slot.
            with closing(store.connect()) as conn, conn:
                conn.execute("""UPDATE ai_imagegen_batches SET status='done' WHERE status='typed' AND EXISTS(
                  SELECT 1 FROM ai_type_checks t WHERE t.chat_id=ai_imagegen_batches.chat_id
                  AND t.request_message_id=ai_imagegen_batches.first_message_id AND t.status IN ('done','failed'))""")
                conn.execute("UPDATE ai_imagegen_batches SET status='cancelled' WHERE status='confirm' AND created_at<?",(time.time()-86400,))
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Image collector iteration failed')
            await asyncio.sleep(5)


def finish(task_id,state,text):
    from ai_runtime import stamp
    with closing(store.connect()) as conn, conn:
        conn.execute('UPDATE ai_imagegen_jobs SET state=?,image=NULL,updated_at=? WHERE task_id=?',(state,time.time(),task_id))
        conn.execute('UPDATE ai_tasks SET status=?,result_text=?,error_text=?,updated_at=?,finished_at=? WHERE id=?',
            ('done' if state=='done' else 'failed',text if state=='done' else None,None if state=='done' else text,stamp(),stamp(),task_id))


def prepare(task):
    import ai_audit
    from ai_providers import call_external,ProviderUnavailable,PromptTooLarge
    from photo_story import image_parts
    inputs=json.loads(task['payload_json']).get('photos',[])
    parts=image_parts(inputs,task['chat_id']) if inputs else []
    original=store.reference_context(inputs)+task['prompt']
    rewrite=dict(task,task_type='imagegen_prepare',prompt=store.REWRITE+original,payload={'photos':inputs},
                 _image_parts=parts,system_instruction='')
    ai_audit.begin()
    try:
        try:
            prompt,meta=call_external(rewrite,20)
            prompt=prompt.strip()
            if not 1<=len(prompt)<=1800:
                prompt=original
                log.warning('Image prompt preparation invalid; using original: task=%s',task['id'])
        except (ProviderUnavailable,PromptTooLarge) as exc:
            prompt=original
            log.warning('Image prompt preparation unavailable; using original: task=%s reason=%s',
                        task['id'],type(exc).__name__)
    finally:
        calls=ai_audit.take()
        from ai_runtime import record_attempt
        import uuid
        try:
            record_attempt('tasks',task['id'],uuid.uuid4().hex,{'provider':'google','model':calls[-1]['model'] if calls else '',
                'calls':calls},'prompt_preparation')
        except Exception:
            log.warning('Image preparation audit failed: task=%s',task['id'],exc_info=True)
    images,size=store.reference_images(parts)
    with closing(store.connect()) as conn, conn:
        conn.execute('UPDATE ai_imagegen_jobs SET prepared_prompt=?,width=?,height=?,updated_at=? WHERE task_id=?',
                     (prompt,*size,time.time(),task['id']))
    return prompt,images,size


def generate(task,prompt,images,size):
    def started(attempt):
        with closing(store.connect()) as conn, conn:
            conn.execute("UPDATE ai_imagegen_jobs SET state='generating',updated_at=? WHERE task_id=?",(time.time(),task['id']))
    image=store.generate(task,prompt,images,size,started)
    with closing(store.connect()) as conn, conn:
        conn.execute("UPDATE ai_imagegen_jobs SET state='result_ready',image=?,updated_at=? WHERE task_id=?",(image,time.time(),task['id']))
    return image


async def run_task(bot,task):
    from ai_providers import ProviderUnavailable
    from ai_runtime import stamp
    try:
        with closing(store.connect()) as conn:
            job=dict(conn.execute('SELECT * FROM ai_imagegen_jobs WHERE task_id=?',(task['id'],)).fetchone())
        if job['state']=='result_ready':
            image=job['image']
        else:
            # Persist before sending: an uncertain acknowledgement is never duplicated on restart.
            if not job['notice_sent']:
                with closing(store.connect()) as conn, conn:
                    conn.execute('UPDATE ai_imagegen_jobs SET notice_sent=1 WHERE task_id=?',(task['id'],))
                await bot.send_message(task['chat_id'],'Взял задачу в работу. Подготовлю промпт и сгенерирую изображение.',
                    reply_to_message_id=task['request_message_id'],allow_sending_without_reply=True,parse_mode=None)
            if job['prepared_prompt']:
                from photo_story import image_parts
                inputs=json.loads(task['payload_json']).get('photos',[])
                parts=await asyncio.to_thread(image_parts,inputs,task['chat_id']) if inputs else []
                images,size=store.reference_images(parts); prompt=job['prepared_prompt']
            else:
                prompt,images,size=await asyncio.to_thread(prepare,task)
            image=await asyncio.to_thread(generate,task,prompt,images,size)
        with closing(store.connect()) as conn, conn:
            conn.execute("UPDATE ai_imagegen_jobs SET state='delivering',updated_at=? WHERE task_id=?",(time.time(),task['id']))
        try:
            sent=await bot.send_photo(task['chat_id'],BufferedInputFile(image,filename='image.png'),
                reply_to_message_id=task['request_message_id'],allow_sending_without_reply=True)
        except Exception:
            finish(task['id'],'failed','Доставка изображения неизвестна; автоматическая повторная отправка отключена')
            log.warning('Image delivery outcome unknown: task=%s',task['id'])
            return
        finish(task['id'],'done','Изображение отправлено')
        with closing(store.connect()) as conn, conn:
            conn.execute('UPDATE ai_tasks SET response_message_id=? WHERE id=?',(sent.message_id,task['id']))
    except ProviderUnavailable:
        # Only photo download can reach here; rewriting failures already use the original prompt.
        with closing(store.connect()) as conn, conn:
            attempt=conn.execute('SELECT transport_attempt FROM ai_tasks WHERE id=?',(task['id'],)).fetchone()[0] or 0
            if attempt<2:
                conn.execute("UPDATE ai_tasks SET status='pending',retry_at=?,transport_attempt=coalesce(transport_attempt,0)+1,error_text=? WHERE id=?",
                    (stamp((10,30)[attempt]),'Не удалось загрузить фотографии; повторяю загрузку',task['id']))
                return
        text='Не удалось загрузить фотографии после трёх попыток. Отправьте запрос заново.'
        finish(task['id'],'failed',text)
        await bot.send_message(task['chat_id'],text,reply_to_message_id=task['request_message_id'],
                               allow_sending_without_reply=True,parse_mode=None)
    except Exception as exc:
        # Request-library exceptions may contain a credential-bearing Telegram URL.
        safe=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else 'Не удалось подготовить изображение. Попробуйте позже'
        finish(task['id'],'failed',safe)
        await bot.send_message(task['chat_id'],safe,reply_to_message_id=task['request_message_id'],
                               allow_sending_without_reply=True,parse_mode=None)


def claim_image_task():
    from ai_runtime import stamp
    with closing(store.connect()) as conn:
        if not conn.execute("SELECT 1 FROM ai_tasks WHERE task_type='imagegen' AND status='pending' AND (retry_at IS NULL OR retry_at<=?) LIMIT 1",(stamp(),)).fetchone():return None
    with closing(store.connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM ai_tasks WHERE task_type='imagegen' AND status='pending' AND (retry_at IS NULL OR retry_at<=?) ORDER BY id LIMIT 1",(stamp(),)).fetchone()
        if row:conn.execute("UPDATE ai_tasks SET status='processing',updated_at=? WHERE id=?",(stamp(),row['id']))
        return dict(row) if row else None


async def worker(bot):
    global bot_username
    from ai_tasks import ensure_ai_tables
    from ai_runtime import enabled,stamp
    from dotenv import load_dotenv
    from ai_tasks import BASE_DIR
    import os
    load_dotenv(BASE_DIR/'.env.ai',override=False)
    load_dotenv(os.getenv('CLOUDFLARE_ENV_FILE','/root/.config/udb-cloudflare/credentials.env'),override=False)
    load_dotenv(os.getenv('HF_ENV_FILE','/root/.config/udb-huggingface/credentials.env'),override=False)
    await asyncio.to_thread(ensure_ai_tables)
    bot_username=(await bot.get_me()).username or ''
    with closing(store.connect()) as conn, conn:
        # Never repeat a generator call or Telegram delivery of unknown outcome after restart.
        uncertain=[r[0] for r in conn.execute("SELECT task_id FROM ai_imagegen_jobs WHERE state IN ('generating','delivering')")]
        conn.execute("UPDATE ai_tasks SET status='pending' WHERE task_type='imagegen' AND status='processing'")
    for tid in uncertain:
        finish(tid,'failed','Процесс прерван с неизвестным результатом; повторите запрос вручную')
    collector=asyncio.create_task(collect_loop(bot))
    try:
        while True:
            try:
                task=await asyncio.to_thread(claim_image_task)
                if task:
                    if enabled(task['chat_id']):
                        await run_task(bot,task)
                    else:
                        finish(task['id'],'failed','ИИ отключён в этом чате')
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Image generator iteration failed')
                await asyncio.sleep(5)
    finally:
        collector.cancel()
        await asyncio.gather(collector,return_exceptions=True)
