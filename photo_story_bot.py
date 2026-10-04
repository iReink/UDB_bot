"""Private-photo entry point; album collection does not block Telegram updates."""
import asyncio
import logging
from aiogram import F
import photo_story as stories

log=logging.getLogger(__name__)


def register(dp):
    @dp.message(F.chat.type=='private', F.photo)
    async def receive(message):
        # ImageIntake collects the whole album first and chooses generation or story.
        return


async def worker(bot):
    from ai_tasks import ensure_ai_tables
    await asyncio.to_thread(ensure_ai_tables)
    import time
    import daily_photo_story
    last_daily=0
    while True:
        try:
            if time.time()-last_daily>=60:
                await asyncio.to_thread(daily_photo_story.schedule)
                last_daily=time.time()
            for batch in await asyncio.to_thread(stories.seal_ready):
                notice=await bot.send_message(batch['chat_id'],'Фотографии приняты. Рассмотрю подборку и соберу небольшую историю. Если модели заняты или исчерпали квоту, запрос подождёт её восстановления.',
                                              reply_to_message_id=batch['first_message_id'],allow_sending_without_reply=True,parse_mode=None)
                from contextlib import closing
                with closing(stories.connection()) as conn, conn:
                    conn.execute('UPDATE ai_photo_story_batches SET notice_message_id=? WHERE id=?',(notice.message_id,batch['id']))
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Photo story collector iteration failed')
            await asyncio.sleep(5)
