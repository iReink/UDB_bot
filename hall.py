# hall.py
import random
import io
from pathlib import Path
from PIL import Image

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

# Путь к папке с изображениями
IMAGES_DIR = Path("images")

# Фон и картинки для наложения с координатами
BACKGROUND = IMAGES_DIR / "back.png"
OVERLAYS = [
    ("carl.png", (131, 311)),
    ("mako.png", (837, 198)),
    ("mors.png", (596, 151)),
]


def register_hall_handlers(dp):
    @dp.message(Command(commands=["hall"]))
    async def cmd_hall(message: types.Message):
        # Загружаем фон
        try:
            back = Image.open(BACKGROUND).convert("RGBA")
        except FileNotFoundError:
            await message.answer("Ошибка: фон не найден.")
            return

        # Для каждого оверлея делаем шанс 70%
        for filename, coords in OVERLAYS:
            if random.random() <= 0.7:
                overlay_path = IMAGES_DIR / filename
                try:
                    overlay = Image.open(overlay_path).convert("RGBA")
                except FileNotFoundError:
                    continue
                back.paste(overlay, coords, overlay)

        # Сохраняем результат в BytesIO
        img_bytes = io.BytesIO()
        back = back.convert("RGB")  # Telegram не любит RGBA в InputFile
        back.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        # Отправляем фото через BufferedInputFile
        photo = BufferedInputFile(img_bytes.read(), filename="hall.png")
        await message.answer_photo(photo)
