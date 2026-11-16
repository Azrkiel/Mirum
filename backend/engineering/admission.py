"""Shared, no-queue admission for legacy and reviewed engineering execution."""
from collections import deque
import math
from threading import RLock
import time


class AdmissionBusy(RuntimeError):
    retry_after = 5


class ClientQuotaExceeded(AdmissionBusy):
    """A fairness refusal for one client; maps to the same HTTP 429 as busy."""
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, int(math.ceil(retry_after)))


class ClientFairness:
    """Deterministic per-client fairness in front of the single native slot (no queue).

    Client identity is the caller-supplied key (the API passes the rate limiter's key).
    Two rules, both measured on an injectable clock:
    1. Quota: at most ``per_hour`` job admissions per client in a rolling hour.
    2. Turn: a client admitted last cannot be re-admitted while a *different* client
       was refused (slot busy) since that admission, until that refusal is older
       than ``memory_seconds``. The refused client is then free to take the slot.
    """
    WINDOW = 3600.0

    def __init__(self, per_hour, memory_seconds=120, clock=time.monotonic):
        self.per_hour, self.memory, self._clock = per_hour, memory_seconds, clock
        self._lock = RLock()
        self._admits: dict[str, deque] = {}
        self._refused: dict[str, float] = {}
        self._last = None

    @classmethod
    def unlimited(cls):
        return cls(10**9, 1)

    def _prune(self, now):
        for client in list(self._admits):
            window = self._admits[client]
            while window and window[0] <= now - self.WINDOW:
                window.popleft()
            if not window:
                del self._admits[client]
        for client in [c for c, at in self._refused.items() if at <= now - self.memory]:
            del self._refused[client]

    def check_turn(self, client):
        with self._lock:
            now = self._clock(); self._prune(now)
            waiting = [at for c, at in self._refused.items() if c != client]
            if self._last == client and waiting:
                raise ClientQuotaExceeded('Another client is waiting for engineering capacity', min(waiting) + self.memory - now)

    def check(self, client):
        """Raise ClientQuotaExceeded if this client may not be admitted now."""
        with self._lock:
            self.check_turn(client)
            now = self._clock()
            window = self._admits.get(client)
            if window and len(window) >= self.per_hour:
                raise ClientQuotaExceeded('Engineering job quota reached for this client', window[0] + self.WINDOW - now)

    def refused(self, client):
        with self._lock:
            self._refused[client] = self._clock()

    def admitted(self, client, *, count=True):
        with self._lock:
            now = self._clock()
            if count:
                self._admits.setdefault(client, deque()).append(now)
            self._last = client
            self._refused.pop(client, None)


class NativeSlot:
    def __init__(self):
        self._lock = RLock()
        self.owner = None

    def claim(self, owner):
        with self._lock:
            if self.owner is not None:
                raise AdmissionBusy('Engineering capacity busy; retry after 5 seconds')
            self.owner = owner

    def release(self, owner):
        with self._lock:
            if self.owner is owner:
                self.owner = None


native_slot = NativeSlot()
