import logging
from aiogram import Router, types

router = Router()

@router.message_reaction_count()
async def handle_reaction_count_update(event: types.MessageReactionCountUpdated):
    """
    Ловим изменения количества реакций на сообщение.
    Выводим: чат, автор сообщения, реакции, общее число реакций.
    """
    chat_title = event.chat.title or f"Чат {event.chat.id}"
    message_id = event.message_id
    reactions_info = event.reactions or []
    total_reactions = sum(r.count for r in reactions_info)

    # Составляем строку с типами и количеством
    reactions_text = ", ".join(f"'{r.type}': {r.count}" for r in reactions_info)

    logging.info(
        f"В чате '{chat_title}' сообщение {message_id} получило реакции {reactions_text}. "
        f"Общее число реакций: {total_reactions}"
    )
