from aiogram import types
from aiogram.filters import Command

def register_poe_command(dp):
    @dp.message(Command("poe"))
    async def send_poe_voice(message: types.Message):
        try:
            with open("images/poehali.ogg", "rb") as voice_file:
                await message.answer_voice(voice_file)
        except FileNotFoundError:
            await message.answer("❌ Файл poehali.ogg не найден.")
