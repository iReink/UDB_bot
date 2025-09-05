# hall.py
import random
from io import BytesIO
from pathlib import Path

from aiogram import types, Bot
from aiogram.filters import Command
from PIL import Image

IMAGES_DIR = Path("images")  # папка с картинками

# Список накладываемых картинок с координатами (x, y)
OVERLAY_IMAGES = [
    ("carl.png", (131, 311)),
    ("mako.png", (837, 198)),
    ("mors.png", (596, 151)),
]

BACKGROUND_IMAGE = "back.png"


def generate_hall_image():
    """Генерируем итоговое изображение для зала славы"""
    # Открываем фон
    bg_path = IMAGES_DIR / BACKGROUND_IMAGE
    base_image = Image.open(bg_path).convert("RGBA")

    # Накладываем остальные картинки с шансом 70%
    for filename, coords in OVERLAY_IMAGES:
        if random.random() <= 0.7:
            overlay_path = IMAGES_DIR / filename
            overlay_img = Image.open(overlay_path).convert("RGBA")
            base_image.paste(overlay_img, coords, overlay_img)  # альфа-канал учитывается

    # Сохраняем результат в BytesIO, чтобы отправить через Telegram
    output = BytesIO()
    output.name = "hall.png"
    base_image.save(output, format="PNG")
    output.seek(0)
    return output


def register_hall_handlers(dp):
    """Регистрируем обработчики для модуля hall"""
    @dp.message(Command(commands=["hall"]))
    async def cmd_hall(message: types.Message):
        """Отправляем зал славы с изображениями"""
        img_bytes = generate_hall_image()
        await message.answer_photo(img_bytes)
