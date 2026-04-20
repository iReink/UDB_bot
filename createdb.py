import sqlite3
from contextlib import closing
from pathlib import Path

IDLE_BUILDING_ORDER = {
    "sitopilka": 1,
    "kolodec_sita": 2,
    "sitoferma": 3,
    "masitskaya": 4,
    "sitvolny_zavod": 5,
}

DB_CANDIDATES = (
    "stats.db",
    "udb.sqlite3",
    "database.sqlite3",
    "UDB_bot/stats.db",
    "UDB_bot/udb.sqlite3",
    "UDB_bot/database.sqlite3",
)


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def ensure_idle_game_tables(cur: sqlite3.Cursor) -> bool:
    if not _table_exists(cur, "idle_building_levels"):
        return False

    cur.execute("PRAGMA table_info(idle_building_levels)")
    columns = {row[1] for row in cur.fetchall()}
    if "order" not in columns:
        cur.execute(
            'ALTER TABLE idle_building_levels ADD COLUMN "order" INTEGER NOT NULL DEFAULT 0'
        )

    # Индекс мог быть поврежден: удаляем и создаем заново.
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
    return True


def _existing_db_paths() -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in DB_CANDIDATES:
        path = Path(candidate).resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            result.append(path)
    return result


def migrate() -> list[Path]:
    updated_paths: list[Path] = []
    for db_path in _existing_db_paths():
        with closing(sqlite3.connect(db_path)) as conn:
            cur = conn.cursor()
            updated = ensure_idle_game_tables(cur)
            if updated:
                conn.commit()
                updated_paths.append(db_path)

    if not updated_paths:
        checked = ", ".join(str(p) for p in _existing_db_paths()) or "нет подходящих .db/.sqlite3 файлов"
        raise RuntimeError(
            "Таблица idle_building_levels не найдена ни в одной БД. "
            f"Проверены: {checked}"
        )
    return updated_paths


if __name__ == "__main__":
    paths = migrate()
    print("Готово: поле \"order\" обновлено в БД:")
    for path in paths:
        print(f" - {path}")
