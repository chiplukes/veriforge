"""Transaction-level interface proxies for the high-level :class:`Testbench`.

These proxies are *thin* wrappers over the existing endpoint helpers in
:mod:`veriforge.sim.endpoints`. They do not reimplement protocol
logic; they translate user-facing transaction calls (``put``, ``get``,
``read``, ``write``) into the underlying endpoint API and step the
correct clock domain.

Each proxy holds a back-reference to its :class:`Domain` so that
cycle-bounded waits know which clock to count edges against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from veriforge.sim.endpoints import (
    AXI4Master,
    AXI4Responder,
    AXILiteMaster,
    AXILiteProtocolError,  # noqa: F401 — re-exported for callers
    AXILiteResponder,
    AXIStreamFrame,
    AXIStreamSink,
    AXIStreamSource,
    MemBusMaster,
    MemBusResponder,
    StreamSink,
    StreamSource,
)

if TYPE_CHECKING:
    from .runtime import Domain


class BenchTimeoutError(TimeoutError):
    """Raised when a transaction exceeds its cycle budget on its domain."""


class AXIStreamProxy:  # cm:f2b9e7
    """High-level proxy for an AXI-Stream interface bundle.

    The role determines which underlying endpoint is wrapped:

    * ``role == "master"``: the DUT *drives* the bundle, so the testbench
      acts as a sink (we *receive* from the DUT) and only ``get`` /
      ``pending`` / ``expect`` are meaningful.
    * ``role == "slave"``: the DUT *consumes* the bundle, so the testbench
      acts as a source (we *send* to the DUT) and only ``put`` /
      ``put_frame`` / ``wait_drain`` are meaningful.

    The proxy exposes the AXI-Stream layout for this interface as
    ``elements_per_beat``, ``element_size_bits``, and ``endian``. Values
    are auto-inferred from the DUT's TDATA / TKEEP widths but can be
    overridden via the ``elements_per_beat``, ``element_size_bits``, and
    ``endian`` constructor kwargs (typically routed via
    :class:`PlannerOverrides.iface_layouts`).

    Sideband signals (TKEEP, TDEST, TID, TUSER) are first-class. Use the
    keyword-only arguments on :meth:`put` / :meth:`expect`, or build a
    fully-shaped :class:`AXIStreamFrame` via :meth:`frame` for full
    control (e.g., setting TUSER only on the TLAST beat).
    """

    def __init__(  # noqa: PLR0913
        self,
        domain: "Domain",
        prefix: str,
        *,
        role: str,
        elements_per_beat: int | None = None,
        element_size_bits: int | None = None,
        endian: str = "little",
        strict: bool = False,
    ):
        if role not in {"master", "slave"}:
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        if endian not in {"little", "big"}:
            raise ValueError(f"endian must be 'little' or 'big', got {endian!r}")
        self.domain = domain
        self.prefix = prefix
        self.role = role
        self.endian = endian
        self._source: AXIStreamSource | None = None
        self._sink: AXIStreamSink | None = None
        if role == "slave":
            # DUT consumes -> testbench produces.
            self._source = AXIStreamSource(domain.sim, prefix)
            domain.register(self._source)
            endpoint = self._source
        else:
            # DUT produces -> testbench consumes.
            self._sink = AXIStreamSink(domain.sim, prefix, strict=strict)
            domain.register(self._sink)
            endpoint = self._sink  # type: ignore[assignment]

        # Apply caller layout overrides on top of auto-inference.
        if elements_per_beat is not None:
            endpoint.elements_per_beat = elements_per_beat
        if element_size_bits is not None:
            endpoint.element_size_bits = element_size_bits
        endpoint.endian = endian

    # ------------------------------------------------------------------ layout

    @property
    def elements_per_beat(self) -> int:
        """Elements packed into a single TDATA beat (1 if no TKEEP)."""
        endpoint = self._source if self._source is not None else self._sink
        assert endpoint is not None  # noqa: S101
        return endpoint.elements_per_beat

    @property
    def element_size_bits(self) -> int:
        """Width of a single element in bits."""
        endpoint = self._source if self._source is not None else self._sink
        assert endpoint is not None  # noqa: S101
        return endpoint.element_size_bits

    @property
    def pause(self):
        """Per-cycle pause setting forwarded to the underlying source/sink endpoint.

        Accepts a :class:`bool` or a callable such as
        :class:`~veriforge.sim.endpoints.PauseGenerator`. When callable,
        the generator is invoked exactly once per clock cycle in ``tick_pre``
        so that the RNG state advances at the correct rate regardless of how
        many tick phases run per cycle. Setting this on a proxy that wraps a
        *source* gates ``tvalid``; setting it on a *sink* proxy gates ``tready``.
        """
        endpoint = self._source if self._source is not None else self._sink
        assert endpoint is not None  # noqa: S101
        return endpoint.pause

    @pause.setter
    def pause(self, value) -> None:
        if self._source is not None:
            self._source.pause = value
        if self._sink is not None:
            self._sink.pause = value

    def frame(  # noqa: PLR0913
        self,
        data: AXIStreamFrame | bytes | bytearray | Iterable[int] | None = None,
        *,
        keep: list[int] | None = None,
        dest: int | list[int] = 0,
        tid: int | list[int] = 0,
        user: int | list[int] = 0,
        last: list[int] | None = None,
        endian: str | None = None,
    ) -> AXIStreamFrame:
        """Build an :class:`AXIStreamFrame` shaped for this interface.

        The frame inherits this proxy's ``elements_per_beat``,
        ``element_size_bits``, and ``endian``. Sideband arguments mirror
        the :class:`AXIStreamFrame` constructor and accept either a
        scalar (broadcast across every element) or a per-element list.
        """
        if not isinstance(data, (AXIStreamFrame, bytes, bytearray, list, type(None))):
            data = list(data)
        return AXIStreamFrame(
            data=data,
            keep=keep,
            dest=dest,
            tid=tid,
            user=user,
            last=last,
            elements_per_beat=self.elements_per_beat,
            element_size_bits=self.element_size_bits,
            endian=endian if endian is not None else self.endian,
        )

    # ------------------------------------------------------------------ source

    def put(  # noqa: PLR0913
        self,
        data: Iterable[int] | bytes | bytearray | AXIStreamFrame,
        *,
        keep: list[int] | None = None,
        dest: int | list[int] = 0,
        tid: int | list[int] = 0,
        user: int | list[int] = 0,
        last: list[int] | None = None,
        last_user: int | None = None,
    ) -> None:
        """Queue ``data`` for transmission as a single AXI-Stream frame.

        Sideband signals (``keep``, ``dest``, ``tid``, ``user``,
        ``last``) are forwarded to the underlying
        :class:`AXIStreamFrame`. ``last_user`` is a convenience that sets
        TUSER only on the elements of the **last beat** (the common
        protocol convention "TUSER@TLAST = packet valid"); it is
        mutually exclusive with ``user``.
        """
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; use get() not put()")
        if last_user is not None and user != 0:
            raise ValueError("pass either `user=` or `last_user=`, not both")

        if isinstance(data, AXIStreamFrame):
            # Caller has already shaped the frame; trust it as-is.
            self._source.send(data)
            return

        elements = list(data) if not isinstance(data, (bytes, bytearray)) else data
        # Build a frame with this proxy's layout so element/beat arithmetic
        # uses the proper widths (matters for keep/dest/tid/user broadcast).
        frame = self.frame(
            data=elements,
            keep=keep,
            dest=dest,
            tid=tid,
            user=user if last_user is None else 0,
            last=last,
        )
        if last_user is not None:
            # Replicate `last_user` across every element of the trailing
            # beat so the per-beat aggregation rule (all elements in a
            # beat must agree) is satisfied.
            n = len(frame.data)
            epb = frame.elements_per_beat
            if n == 0:
                raise ValueError("cannot apply last_user to an empty payload")
            tail_count = n % epb or epb
            for i in range(n - tail_count, n):
                frame.user[i] = last_user
        self._source.send(frame)

    def put_frame(self, frame: AXIStreamFrame) -> None:
        """Queue an already-shaped :class:`AXIStreamFrame`."""
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; use get() not put_frame()")
        self._source.send(frame)

    def wait_drain(self, *, timeout: int = 1000) -> None:
        """Block until every queued beat has been accepted by the DUT."""
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; nothing to drain")
        deadline = self._source.empty
        for _ in range(timeout):
            if deadline():
                return
            self.domain.step()
        raise BenchTimeoutError(f"AXIStreamProxy({self.prefix!r}).wait_drain: queue not empty after {timeout} cycles")

    # -------------------------------------------------------------------- sink

    def pending(self) -> int:
        """Number of complete frames currently buffered on the sink."""
        if self._sink is None:
            raise RuntimeError(f"interface {self.prefix!r} is a source; use wait_drain()")
        return self._sink.count()

    def get(self, *, timeout: int = 1000) -> AXIStreamFrame:
        """Step the domain until at least one frame arrives, then pop it.

        Raises:
            BenchTimeoutError: if no frame arrives within ``timeout``
                rising edges of this proxy's domain clock.
        """
        if self._sink is None:
            raise RuntimeError(f"interface {self.prefix!r} is a source; use put()/wait_drain()")
        for _ in range(timeout):
            if self._sink.count() > 0:
                frame = self._sink.recv()
                assert frame is not None  # noqa: S101 - count>0 guarantees a frame
                return frame
            if not self.domain.step():
                break
        raise BenchTimeoutError(f"AXIStreamProxy({self.prefix!r}).get: no frame after {timeout} cycles")

    def expect(  # noqa: PLR0913
        self,
        expected: AXIStreamFrame | Iterable[int],
        *,
        timeout: int = 1000,
        dest: int | list[int] | None = None,
        tid: int | list[int] | None = None,
        user: int | list[int] | None = None,
        last_user: int | None = None,
    ) -> AXIStreamFrame:
        """Get the next frame and assert its data + sideband match.

        Sideband checks are opt-in: pass ``dest`` / ``tid`` / ``user``
        (scalar or per-element list) to assert per-element values, or
        ``last_user`` to check only the trailing beat's TUSER value.
        """
        frame = self.get(timeout=timeout)
        expected_data = list(expected.data) if isinstance(expected, AXIStreamFrame) else list(expected)
        if list(frame.data) != expected_data:
            raise AssertionError(
                f"AXIStreamProxy({self.prefix!r}).expect mismatch: got {list(frame.data)}, expected {expected_data}"
            )

        def _check(name: str, want: int | list[int], got: list[int]) -> None:
            want_list = [want] * len(got) if isinstance(want, int) else list(want)
            if got != want_list:
                raise AssertionError(
                    f"AXIStreamProxy({self.prefix!r}).expect {name} mismatch: got {got}, expected {want_list}"
                )

        if dest is not None:
            _check("dest", dest, list(frame.dest))
        if tid is not None:
            _check("tid", tid, list(frame.tid))
        if user is not None:
            _check("user", user, list(frame.user))
        if last_user is not None:
            n = len(frame.data)
            epb = frame.elements_per_beat
            tail_count = n % epb or epb
            tail_users = list(frame.user[n - tail_count : n])
            if any(u != last_user for u in tail_users):
                raise AssertionError(
                    f"AXIStreamProxy({self.prefix!r}).expect last_user mismatch: "
                    f"trailing-beat user values were {tail_users}, expected {[last_user] * tail_count}"
                )
        return frame


class AXILiteProxy:  # cm:3c9d5f
    """High-level proxy for an AXI-Lite interface bundle.

    Two roles are supported. ``role`` captures the **DUT-side** role:

    * ``role == "slave"``: the DUT exposes an AXI-Lite slave; the
      testbench acts as master and drives ``read`` / ``write`` calls.
    * ``role == "master"``: the DUT drives an AXI-Lite master; the
      testbench acts as responder, backed by an in-memory store. Use
      :meth:`memory` to seed/inspect the store and :attr:`write_log` /
      :attr:`read_log` to inspect observed transactions.

    For ``role == "slave"`` note that the underlying ``AXILiteMaster``
    drives the simulator on its own clock during a transaction, so other
    domain endpoints will not see ``tick_post`` events while an
    AXI-Lite call is in flight. Avoid issuing AXI-Lite calls while
    expecting parallel activity on other domains.
    """

    def __init__(  # noqa: PLR0913
        self,
        domain: "Domain",
        prefix: str,
        *,
        role: str = "slave",
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        strict: bool = False,
    ):
        if role not in {"master", "slave"}:
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        self.domain = domain
        self.prefix = prefix
        self.role = role
        self._master: AXILiteMaster | None = None
        self._responder: AXILiteResponder | None = None
        if role == "slave":
            self._master = AXILiteMaster(domain.sim, prefix, clock_name=domain.clock_name)
        else:
            self._responder = AXILiteResponder(
                domain.sim,
                prefix,
                clock_name=domain.clock_name,
                initial_memory=initial_memory,
                default_read_value=default_read_value,
                strict=strict,
            )

    def _require_master(self) -> AXILiteMaster:
        if self._master is None:
            raise RuntimeError(
                f"AXILiteProxy(role={self.role!r}): write/read are master-side operations; "
                "use role='slave' (DUT slave) to issue transactions."
            )
        return self._master

    def _require_responder(self) -> AXILiteResponder:
        if self._responder is None:
            raise RuntimeError(
                f"AXILiteProxy(role={self.role!r}): memory/log access requires role='master' "
                "(DUT master, bench responder)."
            )
        return self._responder

    def write(self, addr: int, data: int, **kwargs) -> int:
        """Issue an AXI-Lite single-beat write and return the BRESP value."""
        return self._require_master().write(addr, data, **kwargs)

    def read(self, addr: int, **kwargs) -> int:
        """Issue an AXI-Lite single-beat read and return RDATA."""
        return self._require_master().read(addr, **kwargs)

    def write_then_read(self, addr: int, data: int, **kwargs) -> int:
        """Convenience: write ``data`` to ``addr`` and immediately read it back."""
        self.write(addr, data, **kwargs)
        return self.read(addr, **kwargs)

    @property
    def memory(self) -> dict[int, int]:
        """Backing memory store for the responder (role='master' only)."""
        return self._require_responder().memory

    @property
    def write_log(self) -> list[tuple[int, int, int]]:
        """List of observed (addr, data, strb) writes (role='master' only)."""
        return self._require_responder().write_log

    @property
    def read_log(self) -> list[int]:
        """List of observed read addresses (role='master' only)."""
        return self._require_responder().read_log

    def queue_read_response(self, data: int, *, resp: int = 0) -> None:
        """Queue an explicit read response (role='master' only)."""
        self._require_responder().queue_read_response(data, resp=resp)

    def queue_write_response(self, resp: int) -> None:
        """Queue an explicit write response (role='master' only)."""
        self._require_responder().queue_write_response(resp)

    def close(self) -> None:
        if self._responder is not None:
            self._responder.close()

    @property
    def pause(self):
        """Per-cycle pause setting for the AXI-Lite responder (role='master' only).

        Accepts a :class:`bool` or callable such as
        :class:`~veriforge.sim.endpoints.PauseGenerator`. When paused,
        ``awready``, ``wready``, and ``arready`` are held low, stalling the DUT
        master until the pause is released. Ignored when ``role='slave'``.
        """
        if self._responder is not None:
            return self._responder.pause
        return False

    @pause.setter
    def pause(self, value) -> None:
        if self._responder is not None:
            self._responder.pause = value


class AXI4Proxy:  # cm:1a5c6e
    """High-level proxy for a full AXI4 interface bundle.

    Two roles are supported. ``role`` captures the **DUT-side** role:

    * ``role == "slave"``: the DUT exposes an AXI4 slave; the testbench
      acts as master (burst-capable :meth:`read` / :meth:`write`).
    * ``role == "master"``: the DUT drives an AXI4 master; the testbench
      acts as responder, backed by an in-memory store. INCR bursts are
      modeled (FIXED degenerates to single-beat correctness; WRAP is not
      modeled). Use :attr:`memory` to seed/inspect, :attr:`write_log` /
      :attr:`read_log` for per-beat traces, and :attr:`write_burst_log` /
      :attr:`read_burst_log` for burst-level traces.

    Like :class:`AXILiteProxy`, when in ``role == "slave"`` the underlying
    master drives the simulator on its own clock for the lifetime of a
    transaction, so other-domain endpoints do not see ``tick_post`` events
    while a burst is in flight.
    """

    def __init__(  # noqa: PLR0913
        self,
        domain: "Domain",
        prefix: str,
        *,
        role: str = "slave",
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        strict: bool = False,
    ):
        if role not in {"master", "slave"}:
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        self.domain = domain
        self.prefix = prefix
        self.role = role
        self._master: AXI4Master | None = None
        self._responder: AXI4Responder | None = None
        if role == "slave":
            self._master = AXI4Master(domain.sim, prefix, clock_name=domain.clock_name)
        else:
            self._responder = AXI4Responder(
                domain.sim,
                prefix,
                clock_name=domain.clock_name,
                initial_memory=initial_memory,
                default_read_value=default_read_value,
                strict=strict,
            )

    def _require_master(self) -> AXI4Master:
        if self._master is None:
            raise RuntimeError(
                f"AXI4Proxy(role={self.role!r}): write/read are master-side operations; "
                "use role='slave' (DUT slave) to issue transactions."
            )
        return self._master

    def _require_responder(self) -> AXI4Responder:
        if self._responder is None:
            raise RuntimeError(
                f"AXI4Proxy(role={self.role!r}): memory/log access requires role='master' "
                "(DUT master, bench responder)."
            )
        return self._responder

    def write(self, addr: int, data, **kwargs) -> int:
        """Issue an AXI4 INCR burst write. ``data`` is an int or list of ints."""
        return self._require_master().write(addr, data, **kwargs)

    def read(self, addr: int, *, length: int = 1, **kwargs) -> list[int]:
        """Issue an AXI4 INCR burst read of ``length`` beats; returns the data list."""
        return self._require_master().read(addr, length=length, **kwargs)

    def write_then_read(self, addr: int, data, **kwargs) -> list[int]:
        """Convenience: write ``data`` then read back the same number of beats."""
        if not isinstance(data, (list, tuple)):
            beats = [data]
        else:
            beats = list(data)
        self.write(addr, beats, **kwargs)
        return self.read(addr, length=len(beats), **kwargs)

    @property
    def memory(self) -> dict[int, int]:
        """Backing memory store for the responder (role='master' only)."""
        return self._require_responder().memory

    @property
    def write_log(self) -> list[tuple[int, int, int]]:
        """List of observed (addr, data, strb) writes per-beat (role='master' only)."""
        return self._require_responder().write_log

    @property
    def read_log(self) -> list[int]:
        """List of observed read addresses per-beat (role='master' only)."""
        return self._require_responder().read_log

    @property
    def write_burst_log(self) -> list[tuple[int, int, int]]:
        """List of observed (addr, beats, txn_id) write bursts (role='master' only)."""
        return self._require_responder().write_burst_log

    @property
    def read_burst_log(self) -> list[tuple[int, int, int]]:
        """List of observed (addr, beats, txn_id) read bursts (role='master' only)."""
        return self._require_responder().read_burst_log

    def close(self) -> None:
        if self._responder is not None:
            self._responder.close()

    @property
    def pause(self):
        """Per-cycle pause setting for the AXI4 responder (role='master' only).

        Accepts a :class:`bool` or callable such as
        :class:`~veriforge.sim.endpoints.PauseGenerator`. When paused,
        ``awready``, ``wready``, and ``arready`` are held low, stalling the DUT
        master until the pause is released. Ignored when ``role='slave'``.
        """
        if self._responder is not None:
            return self._responder.pause
        return False

    @pause.setter
    def pause(self, value) -> None:
        if self._responder is not None:
            self._responder.pause = value


class StreamProxy:  # cm:4f3e9b
    """High-level proxy for a generic ready/valid stream bundle.

    This is the Pulp-style sibling of :class:`AXIStreamProxy`: it has no
    notion of frame boundaries (there is no ``tlast``), no per-element
    packing, and no AXIS sideband. One ``put`` / ``get`` corresponds to
    exactly one accepted ready/valid handshake on the bus.

    The role determines which underlying endpoint is wrapped:

    * ``role == "master"``: DUT drives the bundle; testbench sinks it.
      Use :meth:`get` / :meth:`expect` / :meth:`pending`.
    * ``role == "slave"``: DUT consumes; testbench drives. Use
      :meth:`put` / :meth:`wait_drain`.

    ``signals`` is the role-keyed signal map produced by the detector
    (``{"valid": "valid_i", "ready": "ready_o", "data": "data_i", ...}``).
    Optional same-direction sideband signals (anything beyond ``valid``,
    ``ready``, ``data``) can be driven on a slave bundle by passing the
    ``sideband`` kwarg to :meth:`put`, and are sampled on master bundles
    and returned alongside the data value via :meth:`get`.
    """

    def __init__(
        self,
        domain: "Domain",
        prefix: str,
        *,
        role: str,
        signals: dict[str, str],
    ):
        if role not in {"master", "slave"}:
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        self.domain = domain
        self.prefix = prefix
        self.role = role
        self._source: StreamSource | None = None
        self._sink: StreamSink | None = None
        if role == "slave":
            self._source = StreamSource(domain.sim, signals)
            domain.register(self._source)
        else:
            self._sink = StreamSink(domain.sim, signals)
            domain.register(self._sink)

    # ------------------------------------------------------------------ source

    @property
    def pause(self):
        """Per-cycle pause setting forwarded to the underlying source/sink endpoint.

        Accepts a :class:`bool` or callable such as
        :class:`~veriforge.sim.endpoints.PauseGenerator`. Gates ``valid``
        on a slave (source) bundle; gates ``ready`` on a master (sink) bundle.
        """
        endpoint = self._source if self._source is not None else self._sink
        assert endpoint is not None  # noqa: S101
        return endpoint.pause

    @pause.setter
    def pause(self, value) -> None:
        if self._source is not None:
            self._source.pause = value
        if self._sink is not None:
            self._sink.pause = value

    def put(self, data: int = 0, *, sideband: dict[str, int] | None = None) -> None:
        """Queue a single beat for transmission to the DUT."""
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; use get() not put()")
        self._source.send(data, sideband=sideband)

    def write(self, items) -> None:
        """Queue many beats. ``items`` may be ints or ``(int, sideband_dict)`` tuples."""
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; use get() not write()")
        self._source.write(items)

    def wait_drain(self, *, timeout: int = 1000) -> None:
        """Block until every queued beat has been accepted by the DUT."""
        if self._source is None:
            raise RuntimeError(f"interface {self.prefix!r} is a sink; nothing to drain")
        for _ in range(timeout):
            if self._source.empty():
                return
            self.domain.step()
        raise BenchTimeoutError(f"StreamProxy({self.prefix!r}).wait_drain: queue not empty after {timeout} cycles")

    # -------------------------------------------------------------------- sink

    def pending(self) -> int:
        """Number of accepted handshakes currently buffered on the sink."""
        if self._sink is None:
            raise RuntimeError(f"interface {self.prefix!r} is a source; use wait_drain()")
        return self._sink.count()

    def get(self, *, timeout: int = 1000) -> tuple[int, dict[str, int]]:
        """Step the domain until a beat arrives, then pop it.

        Returns ``(data, sideband_dict)`` where the dict contains any
        non-``valid``/``ready``/``data`` signals that were detected as
        part of this bundle. The dict is empty for plain valid/ready/
        data bundles.
        """
        if self._sink is None:
            raise RuntimeError(f"interface {self.prefix!r} is a source; use put()/wait_drain()")
        for _ in range(timeout):
            if self._sink.count() > 0:
                rec = self._sink.recv()
                assert rec is not None  # noqa: S101 - count>0 guarantees a record
                return rec
            if not self.domain.step():
                break
        raise BenchTimeoutError(f"StreamProxy({self.prefix!r}).get: no beat after {timeout} cycles")

    def get_data(self, *, timeout: int = 1000) -> int:
        """Convenience: return only the data value of the next beat."""
        data, _ = self.get(timeout=timeout)
        return data

    def expect(self, expected: int, *, timeout: int = 1000) -> int:
        """Get the next beat and assert that ``data`` equals ``expected``."""
        data, _ = self.get(timeout=timeout)
        if data != expected:
            raise AssertionError(f"StreamProxy({self.prefix!r}).expect mismatch: got {data:#x}, expected {expected:#x}")
        return data

    def expect_sequence(self, expected, *, timeout: int = 1000) -> list[int]:
        """Get N beats and assert they match ``expected`` in order."""
        expected = list(expected)
        out: list[int] = []
        for want in expected:
            out.append(self.expect(want, timeout=timeout))
        return out


class MemBusProxy:  # cm:d8c8a3
    """High-level proxy for a simple synchronous memory bus (SRAM/BRAM style).

    Two roles:

    * ``role == "slave"``: DUT has a memory-bus slave port. The bench drives
      ``addr``/``wdata``/``wen`` and reads ``rdata``.  Use :meth:`write` and
      :meth:`read`.
    * ``role == "master"``: DUT drives ``addr``/``wdata``/``wen`` and the
      bench acts as a responder (backing memory store).  A
      :class:`~veriforge.sim.endpoints.MemBusResponder` auto-ticks via a
      registered callback.  Access the backing store via :attr:`memory` and
      the transaction logs via :attr:`write_log` / :attr:`read_log`.

    ``signals`` is the canonical-role → actual-port-name mapping produced by
    the detector (e.g. ``{"addr": "mem_addr", "wdata": "mem_wdata", ...}``).
    """

    def __init__(  # noqa: PLR0913
        self,
        domain: "Domain",
        prefix: str,
        *,
        role: str,
        signals: dict[str, str],
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        strict: bool = False,
    ) -> None:
        if role not in {"master", "slave"}:
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        self.domain = domain
        self.prefix = prefix
        self.role = role
        self._master: MemBusMaster | None = None
        self._responder: MemBusResponder | None = None
        if role == "slave":
            self._master = MemBusMaster(domain.sim, signals, clock_name=domain.clock_name)
        else:
            self._responder = MemBusResponder(
                domain.sim,
                signals,
                clock_name=domain.clock_name,
                initial_memory=initial_memory,
                default_read_value=default_read_value,
            )

    # ------------------------------------------------------------------ master side

    def _require_master(self) -> MemBusMaster:
        if self._master is None:
            raise RuntimeError(
                f"MemBusProxy({self.prefix!r}) is a responder (role='master'); "
                "use .memory/.write_log/.read_log instead of write()/read()"
            )
        return self._master

    def _require_responder(self) -> MemBusResponder:
        if self._responder is None:
            raise RuntimeError(
                f"MemBusProxy({self.prefix!r}) is a master driver (role='slave'); use write()/read() instead of .memory"
            )
        return self._responder

    def write(self, addr: int, data: int, *, be: int | None = None) -> None:
        """Write *data* to *addr* on the DUT slave port (role='slave' only)."""
        self._require_master().write(addr, data, be=be)

    def read(self, addr: int, *, timeout_cycles: int | None = None) -> int:
        """Read from *addr* on the DUT slave port (role='slave' only). Returns int."""
        return self._require_master().read(addr, timeout_cycles=timeout_cycles)

    # ------------------------------------------------------------------ responder side

    @property
    def memory(self) -> dict[int, int]:
        """Backing store for the auto-responder (role='master' only)."""
        return self._require_responder().memory

    @property
    def write_log(self) -> list[tuple[int, int, int]]:
        """Log of ``(addr, data, strobe)`` tuples written by the DUT (role='master' only)."""
        return self._require_responder().write_log

    @property
    def read_log(self) -> list[int]:
        """Log of addresses read by the DUT (role='master' only)."""
        return self._require_responder().read_log
