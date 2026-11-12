"""Shared, no-queue admission for legacy and reviewed engineering execution."""
from threading import RLock


class AdmissionBusy(RuntimeError):
    retry_after = 5


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
