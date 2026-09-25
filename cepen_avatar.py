"""Build tapeworm avatars from a neutral growth level and profession overlay."""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image


ASSET_DIR = Path(__file__).resolve().parent / "assets" / "cepen"
BASE_SHEET = ASSET_DIR / "base-levels.png"
SKIN_DIR = ASSET_DIR / "skins"
CACHE_DIR = Path(tempfile.gettempdir()) / "udb-cepen-avatars-v1"
AVATAR_SIZE = 1024
LEVEL_BREAKPOINTS = (15, 30, 60, 120, 200, 300, 400, 500, 700)
PROFESSIONS = {
    "designer": "Цепень-дизайнер",
    "programmer": "Цепень-программист",
    "doctor": "Цепень-дядя доктор (или тётя)",
    "teacher": "Цепень-училка",
    "chef": "Цепень-поварёнок",
    "scientist": "Цепень-учёный",
    "plumber": "Цепень-сантехник",
    "artist": "Цепень-художник",
    "detective": "Цепень-сыщик",
    "gamer": "Цепень-геймер",
}


def level_for_length(length_cm: float) -> int:
    for level, maximum in enumerate(LEVEL_BREAKPOINTS, start=1):
        if length_cm <= maximum:
            return level
    return 10


def normalize_profession(value: str | None) -> str | None:
    key = str(value or "").strip().lower()
    return key if key in PROFESSIONS else None


def profession_title(value: str | None) -> str:
    key = normalize_profession(value)
    return PROFESSIONS[key] if key else "Без профессии"


@lru_cache(maxsize=16)
def _sheet(path: str) -> Image.Image:
    with Image.open(path) as source:
        return source.convert("RGBA")


def _cell(sheet: Image.Image, level: int) -> Image.Image:
    index = level - 1
    column, row = index % 5, index // 5
    left = round(column * sheet.width / 5)
    right = round((column + 1) * sheet.width / 5)
    top = round(row * sheet.height / 2)
    bottom = round((row + 1) * sheet.height / 2)
    return sheet.crop((left, top, right, bottom)).resize(
        (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS
    )


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    image = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE))
    pixels = image.load()
    for y in range(AVATAR_SIZE):
        ratio = y / (AVATAR_SIZE - 1)
        color = (
            255,
            round(248 - 8 * ratio),
            round(252 - 5 * ratio),
            255,
        )
        for x in range(AVATAR_SIZE):
            pixels[x, y] = color
    return image


def render_avatar(
    length_cm: float,
    profession: str | None = None,
    *,
    cache_dir: str | Path | None = None,
) -> Path:
    """Return a cached opaque 1024px PNG composed from separate source layers."""
    level = level_for_length(length_cm)
    skin = normalize_profession(profession)
    destination_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"level-{level:02d}-{skin or 'base'}.png"
    if target.exists():
        return target

    base = _cell(_sheet(str(BASE_SHEET)), level)
    canvas = Image.alpha_composite(_background(), base)
    if skin:
        overlay_path = SKIN_DIR / f"{skin}.png"
        overlay = _cell(_sheet(str(overlay_path)), level)
        canvas = Image.alpha_composite(canvas, overlay)

    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    canvas.convert("RGB").save(temporary, format="PNG", optimize=True)
    os.replace(temporary, target)
    return target
