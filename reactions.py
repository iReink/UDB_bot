from aiogram import Router, types
import logging

router = Router()

@router.message_reaction_count()
async def handle_reaction_count(update: types.MessageReactionCountUpdated):
    chat_title = update.chat.title or "личный чат"
    message_id = update.message_id
    reactions_info = update.reactions
    total_reactions = sum(r.count for r in reactions_info)

    reactions_text = ", ".join(f"'{r.type}': {r.count}" for r in reactions_info)

    logging.info(
        f"В чате '{chat_title}' сообщение {message_id} получило реакции {reactions_text}. "
        f"Общее число реакций: {total_reactions}"
    )
