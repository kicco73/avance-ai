"""An event loop whose clock moves only when a test moves it.

Every wait in the product — `asyncio.timeout`, `asyncio.sleep`, a retry
backoff — is a timer on the loop, scheduled through `call_at` against
`loop.time()`. This loop answers `time()` with a number the test owns and
records every timer, so `clock.advance(seconds)` fires, in order and at
the instant each was due, exactly the timers that fall in that span: a
5 s deadline expires at 5.0, not at 4.9, and a backoff scheduled by the
expiry lands at 5.0 + its delay.

Idleness is what the loop itself says: a wait on the selector with a
positive timeout means nothing is runnable and only time can wake it.
The wrapped selector answers that wait by waking whoever is settling the
clock, so `advance` returns once every consequence of the time that
passed has run — not after a fixed number of yields.

    with asyncio.Runner(loop_factory=VirtualClockLoop) as runner:
        clock = runner.get_loop().clock
        runner.run(scenario(clock))
"""
from __future__ import annotations

import asyncio
import selectors


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._timers: list[asyncio.TimerHandle] = []
        self._settling: list[asyncio.Future] = []

    def scheduled(self, handle: asyncio.TimerHandle) -> None:
        self._timers.append(handle)

    def idle(self) -> bool:
        waiters, self._settling = self._settling, []
        for waiter in waiters:
            waiter.set_result(None)
        return bool(waiters)

    async def settle(self) -> None:
        waiter = asyncio.get_running_loop().create_future()
        self._settling.append(waiter)
        await waiter

    async def advance(self, seconds: float) -> None:
        target = self.now + seconds
        await self.settle()
        while (due := self._next_due(target)) is not None:
            self.now = due
            await self.settle()
        self.now = target
        await self.settle()

    def _next_due(self, target: float) -> float | None:
        self._timers = [h for h in self._timers if not h.cancelled() and h.when() > self.now]
        due = [h.when() for h in self._timers if h.when() <= target]
        return min(due, default=None)


class _IdleAwareSelector(selectors.BaseSelector):
    def __init__(self, clock: VirtualClock) -> None:
        self._real = selectors.DefaultSelector()
        self._clock = clock

    def select(self, timeout: float | None = None):
        would_block = timeout is None or timeout > 0
        events = self._real.select(0 if would_block else timeout)
        if would_block and not events and not self._clock.idle():
            return self._real.select(timeout)
        return events

    def register(self, fileobj, events, data=None):
        return self._real.register(fileobj, events, data)

    def unregister(self, fileobj):
        return self._real.unregister(fileobj)

    def modify(self, fileobj, events, data=None):
        return self._real.modify(fileobj, events, data)

    def get_key(self, fileobj):
        return self._real.get_key(fileobj)

    def get_map(self):
        return self._real.get_map()

    def close(self) -> None:
        self._real.close()


class VirtualClockLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self.clock = VirtualClock()
        super().__init__(selector=_IdleAwareSelector(self.clock))

    def time(self) -> float:
        return self.clock.now

    def call_at(self, when, callback, *args, context=None):
        handle = super().call_at(when, callback, *args, context=context)
        self.clock.scheduled(handle)
        return handle
