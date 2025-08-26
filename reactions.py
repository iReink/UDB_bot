# reactions.py
import logging
from aiogram import Router, types

router = Router()


@router.message_reaction_count()
async def handle_reaction_count(update: types.MessageReactionCountUpdated):
    """
    Ловим изменения количества реакций на сообщения.
    Логируем: чат, автор сообщения, реакцию, общее количество.
    """
    chat_title = update.chat.title or str(update.chat.id)
    message_id = update.message_id
    total_reactions = sum(r.count for r in update.reactions)

    reactions_text = ", ".join(f"'{r.type}': {r.count}" for r in update.reactions)

    # Telegram API не отдаёт напрямую автора сообщения, нужно получать через get_message
    # но для простого логирования можно ограничиться chat_id и message_id
    logging.info(
        f"В чате '{chat_title}' сообщение {message_id} получило реакции: {reactions_text}. "
        f"Общее число реакций: {total_reactions}"
    )
