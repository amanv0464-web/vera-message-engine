"""Thread-safe in-memory context and conversation state for a judge run."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from threading import RLock
from typing import Any


VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}
        self.sent_suppressions: set[str] = set()
        self.conversations: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.reply_fingerprints: Counter[str] = Counter()

    def put(self, scope: str, context_id: str, version: int, payload: dict[str, Any]) -> tuple[bool, int | None]:
        """Store a newer complete payload. Returns (accepted, current_version)."""
        with self._lock:
            current = self._contexts.get((scope, context_id))
            if current and version <= current["version"]:
                return False, current["version"]
            self._contexts[(scope, context_id)] = {"version": version, "payload": deepcopy(payload)}
            return True, None

    def get(self, scope: str, context_id: str | None) -> dict[str, Any] | None:
        if not context_id:
            return None
        with self._lock:
            entry = self._contexts.get((scope, context_id))
            return deepcopy(entry["payload"]) if entry else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {scope: 0 for scope in VALID_SCOPES}
            for scope, _ in self._contexts:
                counts[scope] += 1
            return counts

    def claim_suppression(self, key: str) -> bool:
        """Atomically claim a send key; false means the action was already sent."""
        with self._lock:
            if key in self.sent_suppressions:
                return False
            self.sent_suppressions.add(key)
            return True

    def add_turn(self, conversation_id: str, from_role: str, message: str) -> list[dict[str, str]]:
        with self._lock:
            self.conversations[conversation_id].append({"from": from_role, "message": message})
            return list(self.conversations[conversation_id])

    def count_reply(self, normalized_message: str) -> int:
        with self._lock:
            self.reply_fingerprints[normalized_message] += 1
            return self.reply_fingerprints[normalized_message]

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()
            self.sent_suppressions.clear()
            self.conversations.clear()
            self.reply_fingerprints.clear()
