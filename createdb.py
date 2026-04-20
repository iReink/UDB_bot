import sqlite3
from contextlib import closing

DB_FILE = "stats.db"

IDLE_BUILDING_ORDER = {
    "sitopilka": 1,
    "kolodec_sita": 2,
    "sitoferma": 3,
    "masitskaya": 4,
    "sitvolny_zavod": 5,
}


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def ensure_idle_game_tables(cur: sqlite3.Cursor) -> None:
    if not _table_exists(cur, "idle_building_levels"):
        raise RuntimeError("Таблица idle_building_levels не найдена")

    cur.execute("PRAGMA table_info(idle_building_levels)")
    columns = {row[1] for row in cur.fetchall()}
    if "order" not in columns:
        cur.execute(
            'ALTER TABLE idle_building_levels ADD COLUMN "order" INTEGER NOT NULL DEFAULT 0'
        )

    # Если индекс поврежден, удаляем его и создаем заново ниже.
    cur.execute('DROP INDEX IF EXISTS idx_idle_building_levels_order')

    for building_code, building_order in IDLE_BUILDING_ORDER.items():
        cur.execute(
            'UPDATE idle_building_levels SET "order" = ? WHERE building_code = ?',
            (building_order, building_code),
        )

    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_idle_building_levels_order '
        'ON idle_building_levels("order", level)'
    )


def migrate() -> None:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        ensure_idle_game_tables(cur)
        conn.commit()


if __name__ == "__main__":
    migrate()
    print('Готово: поле "order" в idle_building_levels обновлено.')
