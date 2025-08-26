import logging
from aiogram import types, F, Router

router = Router()  # Можно добавить в существующий dp.include_router(router)

# -------------------------------
# Ловим реакции конкретного пользователя
# -------------------------------
@router.message_reaction_updated()
async def handle_reaction_updated(update: types.MessageReactionUpdated):
    """
    Ловим событие, когда пользователь ставит или меняет реакцию.
    """
    chat_title = update.chat.title or "личный чат"
    message_id = update.message_id
    actor_name = update.user.full_name if update.user else "анонимный"
    old_reactions = [r.type for r in update.old_reaction]
    new_reactions = [r.type for r in update.new_reaction]

    # Превращаем в строку
    added = [r for r in new_reactions if r not in old_reactions]
    removed = [r for r in old_reactions if r not in new_reactions]

    if added:
        action_text = f"{actor_name} добавил(а) реакцию(и): {', '.join(added)}"
    elif removed:
        action_text = f"{actor_name} убрал(а) реакцию(и): {', '.join(removed)}"
    else:
        action_text = f"{actor_name} обновил(а) реакции"

    logging.info(f"В чате '{chat_title}' сообщение {message_id}: {action_text}")


# -------------------------------
# Ловим события анонимных реакций (только количество)
# -------------------------------
@router.message_reaction_count()
async def handle_reaction_count(update: types.MessageReactionCountUpdated):
    """
    Ловим событие изменения количества реакций на сообщение.
    """
    chat_title = update.chat.title or "личный чат"
    message_id = update.message_id
    reactions_info = [f"{r.type} ({r.count})" for r in update.reactions]
    total = sum(r.count for r in update.reactions)

    logging.info(
        f"В чате '{chat_title}' сообщение {message_id} изменились реакции: "
        f"{', '.join(reactions_info)}. Всего реакций: {total}"
    )
