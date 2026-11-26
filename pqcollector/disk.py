"""Pause collection before the Docker data filesystem runs out of headroom."""

from __future__ import annotations

import shutil
from pathlib import Path
from time import monotonic


class DiskCapacityError(RuntimeError):
    pass


class DiskGuard:
    check_seconds = 5
    reserve_bytes = 1024**3
    recovery_bytes = 256 * 1024**2

    def __init__(self, directory: Path, min_free: int = 0, max_used: int = 0):
        self.directory = directory
        self.min_free = min_free
        self.max_used = max_used
        self.reason: str | None = None
        self.next_check = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.min_free or self.max_used)

    def check(self, *, write_bytes: int = 0, refresh: bool = False) -> None:
        if not self.enabled:
            return
        current = monotonic()
        if refresh or write_bytes or current >= self.next_check:
            self.next_check = current + self.check_seconds
            try:
                capacity = shutil.disk_usage(self.directory)
            except OSError:
                self.reason = "DISK_STATUS_UNAVAILABLE"
            else:
                # Scale the reserve for operator-specified ceilings smaller than 15 GiB.
                reserve = min(self.reserve_bytes, self.max_used // 10)
                margin = self.recovery_bytes if self.reason else 0
                stop_used = self.max_used - reserve
                if self.max_used and capacity.used + write_bytes >= stop_used - min(margin, stop_used // 10):
                    self.reason = "DISK_USAGE_LIMIT"
                elif self.min_free and capacity.free - write_bytes <= self.min_free + margin:
                    self.reason = "DISK_FREE_LOW"
                else:
                    self.reason = None
        if self.reason:
            raise DiskCapacityError(f"{self.reason} on {self.directory}")
