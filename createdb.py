import math
import sqlite3
from contextlib import closing

DB_FILE = "stats.db"
MAX_BUILDING_LEVEL = 20

IDLE_BUILDING_ORDER = {
    "sitopilka": 1,
    "kolodec_sita": 2,
    "sitoferma": 3,
    "masitskaya": 4,
    "sitvolny_zavod": 5,
}


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _rebuild_table_with_real_column(cur: sqlite3.Cursor, table_name: str, column_name: str) -> None:
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = cur.fetchall()
    if not columns:
        return

    indexes: list[tuple[str, str]] = []
    cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table_name,),
    )
    indexes = [(row[0], row[1]) for row in cur.fetchall()]

    tmp_table = f"{table_name}__tmp_real"
    cur.execute(f'DROP TABLE IF EXISTS "{tmp_table}"')

    col_defs: list[str] = []
    col_names: list[str] = []
    select_exprs: list[str] = []
    pk_columns: list[tuple[int, str]] = []

    for _cid, name, col_type, notnull, default_value, pk_order in columns:
        normalized_type = (col_type or "TEXT").strip()
        if name == column_name:
            normalized_type = "REAL"
        col_sql = f'"{name}" {normalized_type}'
        if notnull:
            col_sql += " NOT NULL"
        if default_value is not None:
            col_sql += f" DEFAULT {default_value}"
        col_defs.append(col_sql)
        col_names.append(f'"{name}"')

        if name == column_name:
            select_exprs.append(f'ROUND(COALESCE("{name}", 0), 3) AS "{name}"')
        else:
            select_exprs.append(f'"{name}"')

        if pk_order:
            pk_columns.append((int(pk_order), name))

    if pk_columns:
        pk_columns.sort(key=lambda item: item[0])
        pk_sql = ", ".join(f'"{name}"' for _, name in pk_columns)
        col_defs.append(f"PRIMARY KEY ({pk_sql})")

    cur.execute(f'CREATE TABLE "{tmp_table}" ({", ".join(col_defs)})')
    cur.execute(
        f'INSERT INTO "{tmp_table}" ({", ".join(col_names)}) '
        f'SELECT {", ".join(select_exprs)} FROM "{table_name}"'
    )
    cur.execute(f'DROP TABLE "{table_name}"')
    cur.execute(f'ALTER TABLE "{tmp_table}" RENAME TO "{table_name}"')

    for _name, sql in indexes:
        cur.execute(sql)


def ensure_fractional_sits(cur: sqlite3.Cursor) -> None:
    if _table_exists(cur, "users"):
        cur.execute("PRAGMA table_info(users)")
        users_columns = cur.fetchall()
        sits_column = next((row for row in users_columns if row[1] == "sits"), None)
        if sits_column:
            sits_type = (sits_column[2] or "").upper()
            if sits_type != "REAL":
                _rebuild_table_with_real_column(cur, "users", "sits")
            cur.execute("UPDATE users SET sits = ROUND(COALESCE(sits, 0), 3)")

    if _table_exists(cur, "sit_stats"):
        cur.execute("PRAGMA table_info(sit_stats)")
        sit_stats_columns = cur.fetchall()
        amount_column = next((row for row in sit_stats_columns if row[1] == "amount"), None)
        if amount_column:
            amount_type = (amount_column[2] or "").upper()
            if amount_type != "REAL":
                _rebuild_table_with_real_column(cur, "sit_stats", "amount")
            cur.execute("UPDATE sit_stats SET amount = ROUND(COALESCE(amount, 0), 3)")


def ensure_masturbate_log_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS masturbate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            is_winner INTEGER NOT NULL DEFAULT 0,
            reward_sits INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_masturbate_log_chat_time "
        "ON masturbate_log(chat_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_masturbate_log_chat_user "
        "ON masturbate_log(chat_id, user_id)"
    )


def ensure_matsturbator_achievement(cur: sqlite3.Cursor) -> None:
    if not _table_exists(cur, "achievements"):
        return

    cur.execute(
        """
        INSERT OR IGNORE INTO achievements (key, name_m, name_f)
        VALUES ('matsturbator', 'Дротик', 'Дротесса')
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO achievements (key, name_m, name_f)
        VALUES ('matershinnik', 'Гномик-матершинник', 'Гномка-матершинка')
        """
    )


def ensure_users_subscription_column(cur: sqlite3.Cursor) -> None:
    if not _table_exists(cur, "users"):
        return

    cur.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cur.fetchall()}
    if "subscription_till" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN subscription_till TEXT DEFAULT ''")


def ensure_profanity_columns(cur: sqlite3.Cursor) -> None:
    for table_name in ("daily_stats", "total_stats"):
        if not _table_exists(cur, table_name):
            continue
        cur.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cur.fetchall()}
        if "profanity_count" not in columns:
            cur.execute(
                f"ALTER TABLE {table_name} ADD COLUMN profanity_count INTEGER DEFAULT 0"
            )


def _floor_to_one_decimal(value: float) -> float:
    floored = math.floor(value * 10.0) / 10.0
    return round(floored, 1)


def _build_idle_level_rows() -> list[tuple[str, str, str, int, int, float, int]]:
    rows: list[tuple[str, str, str, int, int, float, int]] = []

    for level in range(1, MAX_BUILDING_LEVEL + 1):
        rows.append(
            (
                "sitopilka",
                "Ситопилка",
                "sitopilka.png",
                IDLE_BUILDING_ORDER["sitopilka"],
                level,
                _floor_to_one_decimal((level ** 0.85) + 3.0),
                level,
            )
        )

    kolodec_income = 0
    for level in range(1, MAX_BUILDING_LEVEL + 1):
        kolodec_income += 1 + (level // 10)
        rows.append(
            (
                "kolodec_sita",
                "Колодец сита",
                "colodec.png",
                IDLE_BUILDING_ORDER["kolodec_sita"],
                level,
                round(_floor_to_one_decimal((level ** 0.95) + 3.0) + (level // 10), 1),
                kolodec_income,
            )
        )

    for level in range(1, MAX_BUILDING_LEVEL + 1):
        rows.append(
            (
                "sitoferma",
                "Ситоферма",
                "sitoferma.png",
                IDLE_BUILDING_ORDER["sitoferma"],
                level,
                _floor_to_one_decimal((level ** 1.1) + 7.0),
                level * 2,
            )
        )

    masitskaya_income = 0
    for level in range(1, MAX_BUILDING_LEVEL + 1):
        masitskaya_income += 2 + (level // 10)
        rows.append(
            (
                "masitskaya",
                "Маситская",
                "masit.png",
                IDLE_BUILDING_ORDER["masitskaya"],
                level,
                round(_floor_to_one_decimal((level ** 1.1) + 7.0) + ((level // 10) * 4), 1),
                masitskaya_income,
            )
        )

    sitvolny_income = 0
    for level in range(1, MAX_BUILDING_LEVEL + 1):
        sitvolny_income += 3 + (level // 6)
        rows.append(
            (
                "sitvolny_zavod",
                "Ситвольный завод",
                "sitovol.png",
                IDLE_BUILDING_ORDER["sitvolny_zavod"],
                level,
                round(_floor_to_one_decimal((level ** 1.4) + 12.0) + ((level // 10) * 5), 1),
                sitvolny_income,
            )
        )

    return rows


def ensure_idle_game_tables(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS idle_building_levels (
            building_code TEXT NOT NULL,
            building_name TEXT NOT NULL,
            image_file TEXT NOT NULL,
            "order" INTEGER NOT NULL CHECK("order" > 0),
            level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 20),
            upgrade_cost_sits REAL NOT NULL,
            income_microsits_per_hour INTEGER NOT NULL CHECK(income_microsits_per_hour >= 0),
            PRIMARY KEY (building_code, level)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_idle_building_levels_code ON idle_building_levels(building_code)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_idle_building_levels_order ON idle_building_levels(\"order\", level)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS idle_player_buildings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            building_code TEXT NOT NULL,
            current_level INTEGER NOT NULL CHECK(current_level BETWEEN 1 AND 20),
            lifetime_earned_microsits INTEGER NOT NULL DEFAULT 0 CHECK(lifetime_earned_microsits >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, chat_id, building_code),
            FOREIGN KEY (building_code, current_level)
                REFERENCES idle_building_levels(building_code, level)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_idle_player_buildings_owner "
        "ON idle_player_buildings(chat_id, user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_idle_player_buildings_building "
        "ON idle_player_buildings(building_code)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS idle_hourly_income_ticks (
            hour_key TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute("PRAGMA table_info(idle_building_levels)")
    columns = {row[1] for row in cur.fetchall()}
    if "income_microsits_per_hour" not in columns:
        cur.execute(
            "ALTER TABLE idle_building_levels "
            "ADD COLUMN income_microsits_per_hour INTEGER NOT NULL DEFAULT 0"
        )
    if "order" not in columns:
        cur.execute(
            "ALTER TABLE idle_building_levels "
            "ADD COLUMN \"order\" INTEGER NOT NULL DEFAULT 0"
        )

    for row in _build_idle_level_rows():
        cur.execute(
            """
            INSERT INTO idle_building_levels (
                building_code,
                building_name,
                image_file,
                "order",
                level,
                upgrade_cost_sits,
                income_microsits_per_hour
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(building_code, level) DO UPDATE SET
                building_name = excluded.building_name,
                image_file = excluded.image_file,
                "order" = excluded."order",
                upgrade_cost_sits = excluded.upgrade_cost_sits,
                income_microsits_per_hour = excluded.income_microsits_per_hour
            """,
            row,
        )


def migrate() -> None:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        ensure_fractional_sits(cur)
        ensure_masturbate_log_table(cur)
        ensure_matsturbator_achievement(cur)
        ensure_users_subscription_column(cur)
        ensure_profanity_columns(cur)
        ensure_idle_game_tables(cur)
        conn.commit()


if __name__ == "__main__":
    migrate()
    print("DB migration complete: fractional sits + misc tables + idle game tables.")
