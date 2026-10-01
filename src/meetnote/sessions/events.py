"""有界内存事件日志；订阅者使用游标，不各自积压无界队列。"""

import asyncio
import json
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    data: dict

    def wire(self) -> str:
        body = json.dumps({"id": self.id, "type": self.type, "data": self.data}, ensure_ascii=False)
        return f"id: {self.id}\ndata: {body}\n\n"


class EventLog:
    def __init__(self, max_count: int, max_bytes: int):
        self.max_count, self.max_bytes = max_count, max_bytes
        self.items: deque[tuple[Event, int]] = deque()
        self.size = 0
        self.sequence = 0
        self.changed = asyncio.Event()

    def publish(self, kind: str, data: dict) -> None:
        self.sequence += 1
        event = Event(self.sequence, kind, data)
        size = len(event.wire().encode())
        if size > self.max_bytes:
            event = Event(self.sequence, kind, {"truncated": True, "reason": "event_size_limit"})
            size = len(event.wire().encode())
        self.items.append((event, size))
        self.size += size
        while len(self.items) > self.max_count or self.size > self.max_bytes:
            self.size -= self.items.popleft()[1]
        self.changed.set()
        self.changed = asyncio.Event()

    def after(self, cursor: int) -> list[Event] | None:
        if cursor > self.sequence or (self.items and cursor < self.items[0][0].id - 1):
            return None
        return [event for event, _ in self.items if event.id > cursor]
