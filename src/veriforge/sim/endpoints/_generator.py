"""Generator-based endpoint adapter.

Converts a generator function into an endpoint object implementing the
three-phase contract (tick_pre / sample_pre / tick_post), so users can
write endpoint behaviour as a single coroutine with ``yield`` marking
phase boundaries rather than implementing three methods on a class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import Any


class GeneratorEndpoint:
    """Wraps a generator function as an endpoint implementing the phase contract.

    The generator yields twice per clock cycle:

    1. ``yield`` (first) — marks end of ``tick_pre()`` (drive signals).
    2. ``yield`` (second) — marks end of ``tick_post()`` (commit state).

    The framework calls ``tick_pre()`` to advance to the first yield, then
    ``tick_post()`` to advance to the second yield (only on risen domains).

    Wrong-domain edge safety: if ``tick_post()`` is never called (a
    different domain's clock rose), the generator is discarded and
    re-created from the factory on the next ``tick_pre()``, so it
    re-drives the same values — idempotent at the wire level.

    Example::

        @d.generator
        def counter():
            val = 0
            while True:
                d.sim.drive("a", val)
                yield            # ← tick_pre done, wait for edge
                val = (val + 1) & 0xFF
                yield            # ← tick_post done
    """

    def __init__(self, gen_factory: Any) -> None:
        self._gen_factory = gen_factory
        self._gen: Generator[None, None, None] | None = None
        self._post_called: bool = True  # True → gen is at second yield, ready for next tick_pre
        self.sim: Any = None  # set by Domain.register() for strict-mode guard

    def tick_pre(self) -> None:
        """Advance generator to first yield (drive phase)."""
        if not self._post_called:
            # Wrong-domain edge: tick_post was never called.
            # Discard the generator and re-create from factory.
            gen = self._gen
            if gen is not None:
                gen.close()
            self._gen = None

        gen = self._gen
        if gen is None:
            gen = self._gen_factory()
            self._gen = gen

        try:
            next(gen)  # run to first yield → tick_pre
            self._post_called = False
        except StopIteration:
            # Generator exhausted; create a fresh one.
            gen = self._gen_factory()
            self._gen = gen
            next(gen)  # must have at least one yield
            self._post_called = False

    def sample_pre(self) -> None:
        """No-op — generator is paused at first yield."""
        pass

    def tick_post(self) -> None:
        """Advance generator to second yield (commit phase).

        Only called on risen domains.  Calls ``next()`` exactly once so
        the generator advances past the first yield, runs its commit code,
        and stops at the second yield — ready for the next tick_pre.
        """
        gen = self._gen
        if gen is None:
            return
        try:
            next(gen)  # advance past first yield → second yield
            self._post_called = True
        except StopIteration:
            self._gen = None
            self._post_called = True
