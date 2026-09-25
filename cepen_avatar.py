"""Build tapeworm avatars from registered base and profession sprite sheets."""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from collections import deque

from PIL import Image


ASSET_DIR = Path(__file__).resolve().parent / "assets" / "cepen"
BASE_SHEET = ASSET_DIR / "base-levels.png"
SKIN_DIR = ASSET_DIR / "skins"
CACHE_DIR = Path(tempfile.gettempdir()) / "udb-cepen-avatars-v3"
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

# Approximate mouth positions in the base sprites.  Profession sprites may move
# the face slightly, so these boxes are only anchors for finding the actual
# dark mouth outline in each rendered cell.
_MOUTH_ANCHORS = {
    1: (584, 676, 633, 733),
    2: (576, 604, 637, 672),
    3: (569, 558, 636, 635),
    4: (633, 510, 704, 591),
    5: (651, 461, 728, 547),
    6: (716, 388, 794, 476),
    7: (681, 364, 760, 451),
    8: (640, 345, 720, 435),
    9: (656, 318, 740, 409),
    10: (716, 279, 800, 371),
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


def _mouth_box(sprite: Image.Image, level: int) -> tuple[int, int, int, int]:
    """Locate the mouth outline near the level-specific face anchor."""
    anchor = _MOUTH_ANCHORS[level]
    anchor_width = anchor[2] - anchor[0]
    anchor_height = anchor[3] - anchor[1]
    center_x = (anchor[0] + anchor[2]) / 2
    center_y = (anchor[1] + anchor[3]) / 2
    search = (
        max(0, anchor[0] - 110),
        max(0, anchor[1] - 105),
        min(AVATAR_SIZE, anchor[2] + 110),
        min(AVATAR_SIZE, anchor[3] + 90),
    )
    pixels = sprite.load()

    def connected_components(
        points: set[tuple[int, int]], minimum_size: int
    ) -> list[tuple[int, int, int, int, int]]:
        components: list[tuple[int, int, int, int, int]] = []
        while points:
            first = points.pop()
            queue = deque([first])
            left = right = first[0]
            top = bottom = first[1]
            count = 1
            touches_edge = False
            while queue:
                x, y = queue.popleft()
                if x in {search[0], search[2] - 1} or y in {search[1], search[3] - 1}:
                    touches_edge = True
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
                for neighbor in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ):
                    if neighbor in points:
                        points.remove(neighbor)
                        queue.append(neighbor)
                        count += 1
            if count >= minimum_size and not touches_edge:
                components.append((left, top, right + 1, bottom + 1, count))
        return components

    # The inner mouth is usually the closest compact saturated-pink region to
    # the anchor.  It remains separate even when glasses or a costume connect
    # the dark outline to the rest of the character.
    mouth_color: set[tuple[int, int]] = set()
    for y in range(search[1], search[3]):
        for x in range(search[0], search[2]):
            red, green, blue, alpha = pixels[x, y]
            if (
                alpha > 128
                and red > 130
                and green < 105
                and blue < 170
                and red > green * 1.5
            ):
                mouth_color.add((x, y))
    color_components = connected_components(mouth_color, 20)
    nearby_color_components = []
    for component in color_components:
        left, top, right, bottom, _ = component
        width, height = right - left, bottom - top
        aspect = width / height
        component_x = (left + right) / 2
        component_y = (top + bottom) / 2
        distance = abs(component_x - center_x) + abs(component_y - center_y) * 1.2
        if (
            .4 <= aspect <= 2.5
            and width >= anchor_width * .2
            and height >= anchor_height * .2
            and width <= anchor_width * 1.8
            and height <= anchor_height * 1.8
            and distance <= max(anchor_width, anchor_height) * 1.15
        ):
            nearby_color_components.append((distance, component))
    if nearby_color_components:
        _, (left, top, right, bottom, _) = min(nearby_color_components)
        padding = 12
        return (
            max(0, left - padding),
            max(0, top - padding),
            min(AVATAR_SIZE, right + padding),
            min(AVATAR_SIZE, bottom + padding),
        )

    dark: set[tuple[int, int]] = set()
    for y in range(search[1], search[3]):
        for x in range(search[0], search[2]):
            red, green, blue, alpha = pixels[x, y]
            if alpha > 128 and red < 115 and green < 85 and blue < 105:
                dark.add((x, y))

    components = connected_components(dark, 41)

    if not components:
        return anchor

    def score(component: tuple[int, int, int, int, int]) -> float:
        left, top, right, bottom, count = component
        width, height = right - left, bottom - top
        component_x = (left + right) / 2
        component_y = (top + bottom) / 2
        distance = abs(component_x - center_x) + abs(component_y - center_y) * 1.2
        size_penalty = abs(width - anchor_width) * .25 + abs(height - anchor_height) * .2
        above_penalty = max(0, center_y - component_y) * .8
        tiny_penalty = 100 if width < anchor_width * .32 or height < anchor_height * .32 else 0
        return distance + size_penalty + above_penalty + tiny_penalty - min(count, 1000) / 1000

    left, top, right, bottom, _ = min(components, key=score)
    padding = 8
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(AVATAR_SIZE, right + padding),
        min(AVATAR_SIZE, bottom + padding),
    )


def _sad_sprite(sprite: Image.Image, level: int) -> Image.Image:
    """Turn the existing smile upside down without a second sprite set."""
    result = sprite.copy()
    box = _mouth_box(sprite, level)
    mouth = sprite.crop(box).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    result.paste(mouth, (box[0], box[1]))
    return result


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
    happy: bool = True,
    cache_dir: str | Path | None = None,
) -> Path:
    """Return a cached opaque 1024px PNG composed from separate source layers."""
    level = level_for_length(length_cm)
    skin = normalize_profession(profession)
    destination_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)
    mood = "happy" if happy else "sad"
    target = destination_dir / f"level-{level:02d}-{skin or 'base'}-{mood}.png"
    if target.exists():
        return target

    sprite_path = SKIN_DIR / f"{skin}.png" if skin else BASE_SHEET
    sprite = _cell(_sheet(str(sprite_path)), level)
    if not happy:
        sprite = _sad_sprite(sprite, level)
    canvas = Image.alpha_composite(_background(), sprite)

    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    canvas.convert("RGB").save(temporary, format="PNG", optimize=True)
    os.replace(temporary, target)
    return target
