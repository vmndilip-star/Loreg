"""Rate limits for signup and sign-in. In memory: they reset when the app restarts (a stated limit). The clock is passed in.

- `Window`: at most `limit` events per key within `window_s` seconds (signup per client address).
- `Backoff`: after `free` failures per key within `window_s`, each further try must wait base x 2^(failures - free) seconds, up to `max_s`,
  counted from the last failure (sign-in per client address and per email). A success clears the email's count.
"""

from collections import defaultdict, deque
from threading import Lock
from typing import Callable


class Window:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float]):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Counts this attempt and says whether it is within the limit."""
        now = self.clock()
        with self._lock:
            q = self._events[key]
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class Backoff:
    def __init__(self, free: int, base_s: float, max_s: float, window_s: float, clock: Callable[[], float]):
        self.free, self.base_s, self.max_s, self.window_s, self.clock = free, base_s, max_s, window_s, clock
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and now - q[0] >= self.window_s:
            q.popleft()
        return q

    def wait_s(self, key: str) -> float:
        """Seconds until this key may try again; 0 means now."""
        now = self.clock()
        with self._lock:
            q = self._recent(key, now)
            if len(q) < self.free:
                return 0.0
            wait = min(self.base_s * 2 ** (len(q) - self.free), self.max_s)
            return max(0.0, wait - (now - q[-1]))

    def fail(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
