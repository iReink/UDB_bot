"""/summary refreshes the chat chronology and presents its scoped Mini App link."""
import asyncio
import logging
import os
import ai_tasks
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from summary_access import issue_link


def direct_link(username, token):
    name=os.getenv('SUMMARY_MINIAPP_NAME','summary').strip()
    app='/'+name if name else ''
    return f'https://t.me/{username}{app}?startapp={token}&mode=compact'


async def summary_command(message):
    if not message.from_user:
        await message.reply('Не удалось определить пользователя. Отправьте /summary от своего имени.')
        return
    try:
        result = await asyncio.to_thread(
            ai_tasks.create_chat_summary_task_for_chat,
            chat_id=message.chat.id, command_requested=True,
        )
        working = bool(result.get('created')) or result.get('skipped_reason') == 'summary_already_pending'
    except Exception:
        logging.exception('Failed to request chat summary')
        working = False
    if working:
        await asyncio.sleep(5)
    me=await message.bot.me()
    token=issue_link(message.chat.id,message.bot.token)
    await message.reply('Саммари этого чата',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Открыть саммари',url=direct_link(me.username,token))]]))


def register_handlers(dispatcher):
    dispatcher.message.register(summary_command,Command('summary'))
