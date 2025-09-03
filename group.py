from aiogram.types import FSInputFile
from aiogram import types
from aiogram.filters import Command

def register_poe_command(dp):
    @dp.message(Command("poe"))
    async def send_poe_voice(message: types.Message):
        try:
            voice = FSInputFile("images/poehali.ogg")  # Оборачиваем путь в FSInputFile
            await message.answer_voice(voice)
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке: {e}")
