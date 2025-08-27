# likes.py
from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.types import Message
from aiogram.filters import Command
from main import dp, bot
from db import get_connection  # или другие функции из db

# --- Меню лайков ---
def build_likes_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Топ залайканых за неделю", callback_data="likes:weekly_top")],
        [InlineKeyboardButton("Топ залайканых за всё время", callback_data="likes:alltime_top")],
        [InlineKeyboardButton("Топ добряков недели", callback_data="likes:weekly_givers")],
        [InlineKeyboardButton("Топ добряков за всё время", callback_data="likes:alltime_givers")],
        [InlineKeyboardButton("Топ-5 сообщений недели", callback_data="likes:weekly_msgs")],
        [InlineKeyboardButton("Топ-5 сообщений за всё время", callback_data="likes:alltime_msgs")],
        [InlineKeyboardButton("Статистика чата", callback_data="likes:chat_stats")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("like"))
async def cmd_like(message: Message):
    await message.answer(
        "❤️ Самая добрая статистика про ваши лайки ❤️",
        reply_markup=build_likes_keyboard()
    )


dp.message.register(cmd_like, F.text == "/like")


# --- Обработчик кнопок ---
async def likes_menu_callback(callback_query: CallbackQuery):
    data = callback_query.data
    chat_id = callback_query.message.chat.id

    text = ""
    with get_connection() as conn:
        cur = conn.cursor()

        if data == "likes:weekly_top":
            cur.execute("""
                SELECT u.name, SUM(d.react_taken) as likes
                FROM users u
                JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
                WHERE u.chat_id = ? AND d.date >= date('now','-6 days')
                GROUP BY u.user_id
                ORDER BY likes DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "🏆 Топ получателей лайков за неделю:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:alltime_top":
            cur.execute("""
                SELECT name, total_likes_taken FROM total_stats
                WHERE chat_id = ?
                ORDER BY total_likes_taken DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "🏆 Топ получателей лайков за всё время:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:weekly_givers":
            cur.execute("""
                SELECT u.name, SUM(d.react_given) as likes
                FROM users u
                JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
                WHERE u.chat_id = ? AND d.date >= date('now','-6 days')
                GROUP BY u.user_id
                ORDER BY likes DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💖 Топ добряков недели:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:alltime_givers":
            cur.execute("""
                SELECT name, total_likes_given FROM total_stats
                WHERE chat_id = ?
                ORDER BY total_likes_given DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💖 Топ добряков за всё время:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:weekly_msgs":
            cur.execute("""
                SELECT message_id, react_taken, text
                FROM daily_messages
                WHERE chat_id = ? AND date >= date('now','-6 days')
                ORDER BY react_taken DESC
                LIMIT 5
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💬 Топ-5 сообщений недели:\n"
            for message_id, react_taken, msg_text in rows:
                link = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
                snippet = (msg_text[:50] + "...") if msg_text else ""
                text += f"❤️ {react_taken} — {link} — {snippet}\n"

        elif data == "likes:alltime_msgs":
            cur.execute("""
                SELECT message_id, react_taken, text
                FROM total_messages
                WHERE chat_id = ?
                ORDER BY react_taken DESC
                LIMIT 5
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💬 Топ-5 сообщений за всё время:\n"
            for message_id, react_taken, msg_text in rows:
                link = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
                snippet = (msg_text[:50] + "...") if msg_text else ""
                text += f"❤️ {react_taken} — {link} — {snippet}\n"

        elif data == "likes:chat_stats":
            cur.execute("""
                SELECT SUM(react_taken) as week_likes, SUM(messages) as week_msgs
                FROM daily_stats
                WHERE chat_id = ? AND date >= date('now','-6 days')
            """, (chat_id,))
            week_likes, week_msgs = cur.fetchone()
            week_avg = week_likes / week_msgs if week_msgs else 0

            cur.execute("""
                SELECT SUM(total_likes_taken) as all_likes, SUM(total_messages) as all_msgs
                FROM total_stats
                WHERE chat_id = ?
            """, (chat_id,))
            all_likes, all_msgs = cur.fetchone()
            all_avg = all_likes / all_msgs if all_msgs else 0

            text = (
                f"📊 Статистика чата:\n"
                f"За неделю: {week_likes} лайков, ср. на сообщение {week_avg:.2f}\n"
                f"За всё время: {all_likes} лайков, ср. на сообщение {all_avg:.2f}"
            )

    # Редактируем сообщение с меню
    await callback_query.message.edit_text(text, reply_markup=build_likes_keyboard())
    await callback_query.answer()


dp.callback_query.register(likes_menu_callback, F.data.startswith("likes:"))
