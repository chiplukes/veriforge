"""Pause/throttle generator for AXI and AXI-Stream endpoint backpressure.

A :class:`PauseGenerator` instance is callable and returns ``True`` on each
cycle where the endpoint should be paused (``tvalid`` held low on sources,
``tready`` held low on sinks, ``awready``/``wready``/``arready`` held low on
responders).

Instances are designed to be assigned directly to any endpoint's ``pause``
attribute::

    from veriforge.sim.endpoints import PauseGenerator

    # Source: pause 1 in every 4 cycles (~75% bandwidth).
    source.pause = PauseGenerator(1, 4)

    # Sink: pause every other cycle (~50% bandwidth).
    sink.pause = PauseGenerator(1, 2)

    # Proxy convenience — forwards to the underlying endpoint.
    proxy.pause = PauseGenerator.duty(0.3, seed=42)

Plain boolean assignments still work (no API change)::

    source.pause = True   # always paused
    source.pause = False  # never paused (default)
"""

from __future__ import annotations

import random


class PauseGenerator:  # cm:b4e6d8
    """Per-cycle pause generator with configurable random duty cycle.

    Each call to an instance returns ``True`` (pause this cycle) or ``False``
    (transmit this cycle). The probability of pausing is ``num_pause / denom``.

    Args:
        num_pause: Expected number of paused cycles out of every *denom*.
        denom: Window size (denominator). Must be > 0.
        seed: Optional RNG seed for reproducible sequences.

    Raises:
        ValueError: If ``num_pause < 0``, ``denom <= 0``, or
            ``num_pause > denom``.

    Examples::

        # Pause 1 out of every 3 cycles (33 % pause rate / 67 % bandwidth).
        gen = PauseGenerator(1, 3)

        # Always pause.
        gen = PauseGenerator.always()

        # Never pause.
        gen = PauseGenerator.never()

        # 40 % pause rate with a fixed seed for reproducibility.
        gen = PauseGenerator.duty(0.4, seed=7)
    """

    def __init__(
        self,
        num_pause: int,
        denom: int,
        *,
        seed: int | None = None,
    ) -> None:
        if num_pause < 0:
            raise ValueError(f"num_pause must be >= 0, got {num_pause}")
        if denom <= 0:
            raise ValueError(f"denom must be > 0, got {denom}")
        if num_pause > denom:
            raise ValueError(f"num_pause ({num_pause}) must be <= denom ({denom})")
        self._n = num_pause
        self._d = denom
        self._rng = random.Random(seed)  # noqa: S311 — not for cryptography

    def __call__(self) -> bool:
        """Return ``True`` if this cycle should be paused."""
        if self._n == 0:
            return False
        if self._n == self._d:
            return True
        return self._rng.random() < self._n / self._d

    @classmethod
    def never(cls) -> "PauseGenerator":
        """Return a generator that never pauses (full bandwidth)."""
        return cls(0, 1)

    @classmethod
    def always(cls) -> "PauseGenerator":
        """Return a generator that always pauses (zero bandwidth)."""
        return cls(1, 1)

    @classmethod
    def duty(cls, fraction: float, *, seed: int | None = None) -> "PauseGenerator":
        """Return a generator with the given pause duty cycle fraction.

        Args:
            fraction: Fraction of cycles to pause; ``0.0`` = never,
                ``1.0`` = always.
            seed: Optional RNG seed for reproducible sequences.

        Raises:
            ValueError: If *fraction* is outside [0.0, 1.0].
        """
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"fraction must be in [0.0, 1.0], got {fraction!r}")
        denom = 1000
        num_pause = round(fraction * denom)
        return cls(num_pause, denom, seed=seed)
