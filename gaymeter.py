"""Chat-scoped game statistics and durable bite history."""
import random
from contextlib import closing
from datetime import datetime

PHRASES = (
    "Датчик смазан, показания сняты.",
    "Статистика подмигивает тебе из кустов.",
    "Прибор нагрелся от твоих приключений.",
    "Сухие цифры, мокрые воспоминания.",
    "Смазка закончилась, статистика осталась.",
    "Твои поцелуи оставили цифровой след.",
    "Лаборатория нежности одобряет этот замер.",
    "Шпёхометр передаёт привет гейметру.",
    "Держи результат, только не облизывай.",
    "Прибор краснеет, но продолжает считать.",
    "Математика сегодня без штанов.",
    "Цифры знают про твои шалости.",
    "Взвесили укусы, пересчитали засосы.",
    "Твой внутренний баклажан прошёл калибровку.",
    "Поцелуи закончились, протокол остался.",
    "Датчик нежности слегка запотел.",
    "Замер готов, полотенце рядом.",
    "Любовная бухгалтерия сошлась до копейки.",
    "Каждый шпёх оставляет след в отчётности.",
    "Паспорт шалостей пополнился свежим замером.",
    "Губы отдыхали, калькулятор работал.",
    "Счётчик страсти требует технического перерыва.",
    "Сегодня твои приключения выражены в процентах.",
    "Баклажан проверен, пломба на месте.",
    "Засосы учтены, алиби не требуется.",
    "Шкала скользкая, держись покрепче.",
    "Твой послужной список пахнет поцелуями.",
    "Отдел шалостей завершил инвентаризацию.",
    "Прибор доволен, оператор слегка смущён.",
    "Цифры оголились, выводы делай сам.",
)


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bite_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            biter_id INTEGER NOT NULL,
            victim_id INTEGER NOT NULL,
            body_part_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            biter_sex TEXT CHECK (biter_sex IN ('m','f') OR biter_sex IS NULL),
            victim_sex TEXT CHECK (victim_sex IN ('m','f') OR victim_sex IS NULL),
            CHECK (biter_id != victim_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bite_events_biter ON bite_events(chat_id,biter_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bite_events_victim ON bite_events(chat_id,victim_id)")


def log_bite(conn, chat_id, biter_id, victim_id, body_part_id):
    """Append to the caller's transaction; never commit independently."""
    sexes = {}
    for row in conn.execute(
        "SELECT user_id,sex FROM users WHERE chat_id=? AND user_id IN (?,?)",
        (chat_id, biter_id, victim_id),
    ):
        sexes[row['user_id']] = row['sex'] if row['sex'] in ('m', 'f') else None
    conn.execute("""
        INSERT INTO bite_events(chat_id,biter_id,victim_id,body_part_id,created_at,biter_sex,victim_sex)
        VALUES (?,?,?,?,?,?,?)
    """, (chat_id, biter_id, victim_id, body_part_id, datetime.now().isoformat(timespec='seconds'),
          sexes.get(biter_id), sexes.get(victim_id)))


def get_stats(chat_id, user_id):
    import db
    with closing(db.get_connection()) as conn:
        conn.execute('BEGIN')
        user = conn.execute('SELECT sex FROM users WHERE chat_id=? AND user_id=?',
                            (chat_id, user_id)).fetchone()
        sex = user['sex'] if user else None
        rows = conn.execute("""
            WITH interactions(partner_id,weight,events) AS (
                SELECT CASE WHEN user_id1=:user THEN user_id2 ELSE user_id1 END,
                       3, COALESCE(sosalsa_count,0)
                FROM sosalsa_stats WHERE chat_id=:chat AND (user_id1=:user OR user_id2=:user)
                UNION ALL
                SELECT CASE WHEN user_id1=:user THEN user_id2 ELSE user_id1 END,
                       10, COALESCE(shpehalsa_count,0)
                FROM sosalsa_stats WHERE chat_id=:chat AND (user_id1=:user OR user_id2=:user)
                UNION ALL
                SELECT victim_id,1,1 FROM bite_events WHERE chat_id=:chat AND biter_id=:user
                UNION ALL
                SELECT biter_id,1,1 FROM bite_events WHERE chat_id=:chat AND victim_id=:user
            )
            SELECT i.weight,i.events,p.sex FROM interactions i
            LEFT JOIN users p ON p.chat_id=:chat AND p.user_id=i.partner_id
            WHERE i.events>0
        """, {'chat': chat_id, 'user': user_id})
        total = same = unknown = 0
        for row in rows:
            weighted = row['weight'] * row['events']
            total += weighted
            if row['sex'] not in ('m', 'f'):
                unknown += row['events']
            elif sex in ('m', 'f') and row['sex'] == sex:
                same += weighted
        return {'sex': sex, 'total': total, 'same': same, 'unknown': unknown,
                'percent': 100 * same / total if total and sex in ('m', 'f') else None}


def format_result(name, stats):
    percent = stats['percent']
    if stats['sex'] not in ('m', 'f'):
        result = 'Пол в профиле не указан — процент пока не вычислить.'
    elif percent is None:
        result = 'Пока нет событий для расчёта — прибор отдыхает.'
    else:
        filled = min(10, int(percent / 10 + 0.5))
        result = f"Процент гейства: {percent:.1f}%\n{'🍆' * filled}{'▫️' * (10 - filled)}"
    text = f'🌈 Гейметр · {name}\n{result}\n{random.choice(PHRASES)}'
    text += '\nПо формуле: доля взаимодействий со своим полом.'
    if stats['unknown']:
        text += f"\nСобытий с неизвестным полом партнёра: {stats['unknown']} (только в знаменателе)."
    text += '\nСтарые укусы без журнала не учтены.'
    return text


async def gay_command(message):
    import db
    if message.chat.type not in ('group', 'supergroup'):
        await message.reply('Гейметр работает в групповом чате.', parse_mode=None)
        return
    if not message.from_user or message.sender_chat:
        await message.reply('Вызови /gay от своего аккаунта.', parse_mode=None)
        return
    user_id, chat_id = message.from_user.id, message.chat.id
    name = db.get_user_display_name(user_id, chat_id) or message.from_user.full_name
    await message.reply(format_result(name, get_stats(chat_id, user_id)), parse_mode=None)
