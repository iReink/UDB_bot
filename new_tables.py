# new_tables.py
from contextlib import closing
from db import get_connection, add_or_update_user
from sosalsa import get_user_display_name

TARGET_USER_ID = 16725613
SITS_PER_SOS = 2  # сколько сит возвращаем за одну сосаную пару


def refund_sos_for_user(target_user_id: int = TARGET_USER_ID):
    """Возврат сит за сосания с пользователем target_user_id и удаление этих записей."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()

        # 1) Берём все строки с участием target_user_id
        cur.execute("""
            SELECT chat_id, user_id1, user_id2, sosalsa_count
            FROM sosalsa_stats
            WHERE user_id1 = ? OR user_id2 = ?
        """, (target_user_id, target_user_id))

        rows = cur.fetchall()
        if not rows:
            print(f"Нет записей для возврата сосаний с пользователем {target_user_id}")
            return

        for chat_id, u1, u2, count in rows:
            # Определяем, кому возвращаем ситы (не target_user_id)
            recipient_id = u2 if u1 == target_user_id else u1
            sit_to_add = SITS_PER_SOS * count

            # Получаем имя пользователя для логирования
            name = get_user_display_name(recipient_id, chat_id)

            # Добавляем ситы
            cur.execute("SELECT sits, name FROM users WHERE chat_id=? AND user_id=?", (chat_id, recipient_id))
            user_row = cur.fetchone()
            if user_row:
                old_sits = user_row[0] or 0
                cur.execute("UPDATE users SET sits=? WHERE chat_id=? AND user_id=?",
                            (old_sits + sit_to_add, chat_id, recipient_id))
            else:
                # если пользователя нет (маловероятно), создаём его
                cur.execute("INSERT INTO users (chat_id, user_id, name, sits) VALUES (?, ?, ?, ?)",
                            (chat_id, recipient_id, "", sit_to_add))

            print(
                f"Возвращаем {sit_to_add} сит пользователю {name} (user_id={recipient_id}) за сосания с {target_user_id}")

        # 2) Удаляем все эти записи
        cur.execute("""
            DELETE FROM sosalsa_stats
            WHERE user_id1 = ? OR user_id2 = ?
        """, (target_user_id, target_user_id))

        conn.commit()
        print("Возврат завершён и записи удалены.")


refund_sos_for_user(TARGET_USER_ID)