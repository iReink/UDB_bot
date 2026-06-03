import re

from profanity import count_profanity


BOT_WORD_REACTION_HEART = "❤"
BOT_WORD_REACTION_POOP = "💩"

BOT_WORD_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])бот[а-яё]*\b",
    re.IGNORECASE,
)


def choose_bot_word_reaction(text: str | None) -> str | None:
    if not text or not BOT_WORD_RE.search(text):
        return None

    if count_profanity(text):
        return BOT_WORD_REACTION_POOP

    return BOT_WORD_REACTION_HEART
