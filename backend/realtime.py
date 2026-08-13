"""Minimal in-process event bus used by the SSE dashboard stream."""

from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: dict[int, list[queue.Queue[dict[str, Any]]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, user_id: int) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscriptions.setdefault(user_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, user_id: int, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._subscriptions.get(user_id, [])
            if subscriber in subscribers:
                subscribers.remove(subscriber)
            if not subscribers:
                self._subscriptions.pop(user_id, None)

    def publish(self, user_id: int, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscriptions.get(user_id, []))
        for subscriber in subscribers:
            subscriber.put(event)
