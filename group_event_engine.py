from dataclasses import dataclass

from db import add_sits, get_user_display_name
from sosalsa import get_sits

from masturbate_store import MasturbateStore


EVENT_COST = 1
JOIN_COST = 1


@dataclass(slots=True)
class EngineResult:
    ok: bool
    code: str
    display_name: str | None = None
    thread_id: int | None = None


class GroupEventEngine:
    def __init__(self, store: MasturbateStore) -> None:
        self.store = store

    @staticmethod
    def resolve_display_name(chat_id: int, user_id: int, fallback: str) -> str:
        db_name = get_user_display_name(user_id, chat_id)
        if db_name and db_name != str(user_id):
            return db_name
        return fallback

    def get_event(self, chat_id: int):
        return self.store.get_event(chat_id)

    def start_event(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        thread_id: int | None,
        source: str = "tg",
    ) -> EngineResult:
        balance = float(get_sits(chat_id, user_id))
        if balance < EVENT_COST:
            return EngineResult(ok=False, code="insufficient_sits", display_name=display_name)

        add_sits(chat_id, user_id, -EVENT_COST)
        status = self.store.create_event(
            chat_id=chat_id,
            started_by_user_id=user_id,
            starter_display_name=display_name,
            thread_id=thread_id,
            source=source,
        )
        if status != "started":
            add_sits(chat_id, user_id, EVENT_COST)
            if status == "active_exists":
                return EngineResult(ok=False, code="event_already_active")
            return EngineResult(ok=False, code="unexpected_error")

        return EngineResult(ok=True, code="event_started", display_name=display_name, thread_id=thread_id)

    def join_as_participant(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        source: str = "tg",
        allow_freebie_on_insufficient: bool = True,
    ) -> EngineResult:
        event = self.store.get_event(chat_id)
        if not event:
            return EngineResult(ok=False, code="no_active_event")
        if int(event["join_open"] or 0) != 1:
            return EngineResult(ok=False, code="join_window_closed", thread_id=event["thread_id"])

        balance = float(get_sits(chat_id, user_id))
        if balance < JOIN_COST:
            if not allow_freebie_on_insufficient:
                return EngineResult(ok=False, code="insufficient_sits", display_name=display_name, thread_id=event["thread_id"])
            status = self.store.add_member(
                chat_id=chat_id,
                user_id=user_id,
                display_name=display_name,
                role="spectator",
                is_freebie=True,
                source=source,
            )
            if status == "already_joined":
                return EngineResult(ok=False, code="already_joined", display_name=display_name, thread_id=event["thread_id"])
            if status == "join_closed":
                return EngineResult(ok=False, code="join_window_closed", display_name=display_name, thread_id=event["thread_id"])
            if status == "no_event":
                return EngineResult(ok=False, code="no_active_event", display_name=display_name, thread_id=event["thread_id"])
            return EngineResult(ok=True, code="joined_as_freebie", display_name=display_name, thread_id=event["thread_id"])

        add_sits(chat_id, user_id, -JOIN_COST)
        status = self.store.add_member(
            chat_id=chat_id,
            user_id=user_id,
            display_name=display_name,
            role="participant",
            is_freebie=False,
            source=source,
        )
        if status != "added":
            add_sits(chat_id, user_id, JOIN_COST)
            if status == "already_joined":
                return EngineResult(ok=False, code="already_joined", display_name=display_name, thread_id=event["thread_id"])
            if status == "join_closed":
                return EngineResult(ok=False, code="join_window_closed", display_name=display_name, thread_id=event["thread_id"])
            if status == "no_event":
                return EngineResult(ok=False, code="no_active_event", display_name=display_name, thread_id=event["thread_id"])
            return EngineResult(ok=False, code="unexpected_error", display_name=display_name, thread_id=event["thread_id"])

        return EngineResult(ok=True, code="joined_as_participant", display_name=display_name, thread_id=event["thread_id"])

    def join_as_spectator(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        source: str = "tg",
    ) -> EngineResult:
        event = self.store.get_event(chat_id)
        if not event:
            return EngineResult(ok=False, code="no_active_event")
        if int(event["join_open"] or 0) != 1:
            return EngineResult(ok=False, code="join_window_closed", display_name=display_name, thread_id=event["thread_id"])

        status = self.store.add_member(
            chat_id=chat_id,
            user_id=user_id,
            display_name=display_name,
            role="spectator",
            is_freebie=True,
            source=source,
        )
        if status == "added":
            return EngineResult(ok=True, code="joined_as_spectator", display_name=display_name, thread_id=event["thread_id"])
        if status == "already_joined":
            return EngineResult(ok=False, code="already_joined", display_name=display_name, thread_id=event["thread_id"])
        if status == "join_closed":
            return EngineResult(ok=False, code="join_window_closed", display_name=display_name, thread_id=event["thread_id"])
        if status == "no_event":
            return EngineResult(ok=False, code="no_active_event", display_name=display_name, thread_id=event["thread_id"])
        return EngineResult(ok=False, code="unexpected_error", display_name=display_name, thread_id=event["thread_id"])

    def add_reminder(self, chat_id: int, user_id: int, display_name: str) -> EngineResult:
        status = self.store.add_reminder(chat_id=chat_id, user_id=user_id, display_name=display_name)
        if status == "added":
            return EngineResult(ok=True, code="reminder_added", display_name=display_name)
        if status == "already_added":
            return EngineResult(ok=False, code="reminder_exists", display_name=display_name)
        if status == "no_event":
            return EngineResult(ok=False, code="no_active_event", display_name=display_name)
        return EngineResult(ok=False, code="unexpected_error", display_name=display_name)
