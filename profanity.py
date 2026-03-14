import re

# Базовые паттерны русского мата. Это не исчерпывающий словарь,
# а практичный набор корней для игрового счётчика.
PROFANITY_PATTERNS = [
    r"\bбля(?:д|т|ть)?\w*\b",
    r"\b(?:за|на|по|про|вы|пере|под|у)?еб\w*\b",
    r"\b(?:рас|за|пере)?пизд\w*\b",
    r"\b(?:на|по|до|вы|за)?ху[йиеяю]\w*\b",
    r"\b(?:о|а)ху\w*\b",
    r"\bдолбоеб\w*\b",
    r"\bеблан\w*\b",
    r"\bпид(?:о|а)?р\w*\b",
    r"\bпидорас\w*\b",
    r"\bгандон\w*\b",
    r"\bмудак\w*\b",
    r"\bмудил\w*\b",
    r"\bсук(?:а|и|у|ой|е|ам|ами)?\b",
]

PROFANITY_REGEX = re.compile("|".join(f"(?:{pattern})" for pattern in PROFANITY_PATTERNS), re.IGNORECASE)
MAX_PROFANITY_PER_MESSAGE = 3


def normalize_profanity_text(text: str) -> str:
    return text.lower().replace("ё", "е")


def count_profanity(text: str | None) -> int:
    if not text:
        return 0

    normalized = normalize_profanity_text(text)
    matches = list(PROFANITY_REGEX.finditer(normalized))
    return min(MAX_PROFANITY_PER_MESSAGE, len(matches))
