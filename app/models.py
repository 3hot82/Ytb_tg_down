from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MediaJob:
    id: str
    chat_id: int
    user_id: int | None
    message_id: int
    url: str
    created_at: float
    status_message_id: int | None = None

    @classmethod
    def create(
        cls,
        *,
        chat_id: int,
        user_id: int | None,
        message_id: int,
        url: str,
        status_message_id: int | None = None,
    ) -> "MediaJob":
        return cls(
            id=uuid.uuid4().hex,
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            url=url,
            created_at=time.time(),
            status_message_id=status_message_id,
        )

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def loads(cls, raw: str | bytes) -> "MediaJob":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return cls(**data)
