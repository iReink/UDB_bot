"""Choose one tapeworm infection route for a Telegram message."""

from __future__ import annotations


def infection_route(message, reply_source_id: int | None) -> tuple[str | None, int | None]:
    """Return a primary kind or a reply source, never both.

    Telegram may preserve ``reply_to_message`` on media messages.  Sticker and
    video-note triggers have their own infection probability and wording, so
    their primary route takes precedence over that reply metadata.
    """
    if getattr(message, "sticker", None):
        return "sticker", None
    if getattr(message, "video_note", None):
        return "round", None
    return None, reply_source_id
