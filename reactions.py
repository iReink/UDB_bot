# reactions_router.py
import logging
from aiogram import Router, types

router = Router()

@router.message_reaction_count()
async def handle_reaction_count(update: types.MessageReactionCountUpdated):
    chat_title = update.chat.title or "личный чат"
    message_id = update.message_id
    total_reactions = sum(r.count for r in update.reactions)
    reactions_text = ", ".join(f"{r.type}: {r.count}" for r in update.reactions)

    logging.info(
        f"В чате '{chat_title}' сообщение {message_id} получило реакции: {reactions_text}. "
        f"Общее число реакций: {total_reactions}"
    )
