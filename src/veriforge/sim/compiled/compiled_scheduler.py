"""Scheduler adapter for the compiled Cython engine.

Wraps a design-specific compiled extension module and provides the same
interface as Scheduler and VMScheduler for use with Simulator.

Phase 4 additions: initial block fallback via reference executor,
always-with-timing coroutine support, $display capture, VCD
recording integration, and ctx wrapper for testbench signal access.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import tempfile
import warnings
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from veriforge.model.expressions import Identifier
from veriforge.sim.evaluator import EvalContext, ExpressionEvaluator
from veriforge.sim.event_queue import CoroutineMixin, EventQueueMixin, SignalDictBase, TimedEvent
from veriforge.sim.executor import StatementExecutor, StopExecution
from veriforge.sim.value import Value

from .codegen import CythonCodegen
from .compiler import (
    CythonCompiler,
    _CACHE_VERSION,
    _cache_key_from_source_hash,
    _cython_version,
    _keyed_module_name,
    _platform_tag,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from veriforge.model.design import Module

log = logging.getLogger(__name__)

_I64_MAX = (1 << 63) - 1
_U64 = 1 << 64


def _identifier_from_name(name: str) -> Identifier:
    parts = name.split(".")
    if len(parts) == 1:
        return Identifier(name)
    return Identifier(name=parts[-1], hierarchy=parts[:-1])


def _to_i64(val: int) -> int:
    """Convert Python int to signed 64-bit range for C long long."""
    val &= _U64 - 1  # mask to 64 bits
    return val - _U64 if val > _I64_MAX else val


def _collect_identifiers(node: object) -> set[str]:
    """Recursively collect all Identifier names referenced in an AST node."""
    names: set[str] = set()
    _walk_for_idents(node, names)
    return names


def _walk_for_idents(node: object, out: set[str]) -> None:
    """Walk AST node tree collecting Identifier.name values."""
    if isinstance(node, Identifier):
        full_name = node.name
        if node.hierarchy:
            full_name = ".".join(node.hierarchy) + "." + node.name
        out.add(full_name)
        parts = full_name.split(".")
        for index in range(1, len(parts)):
            out.add(".".join(parts[:index]))
        stripped_parts = [part.split("[", 1)[0] if "[" in part else part for part in parts]
        stripped_name = ".".join(stripped_parts)
        out.add(stripped_name)
        for index in range(1, len(stripped_parts)):
            out.add(".".join(stripped_parts[:index]))
        for part in full_name.split("."):
            if "[" in part and part.endswith("]"):
                index_text = part[part.find("[") + 1 : -1].strip()
                if not index_text:
                    continue
                try:
                    int(index_text, 0)
                except ValueError:
                    out.add(index_text)
    # Walk all slot attributes dynamically to avoid missing any AST children
    for attr in getattr(node, "__slots__", ()):
        child = getattr(node, attr, None)
        if child is None or isinstance(child, (int, float, str, bool)):
            continue
        if isinstance(child, list):
            for item in child:
                if hasattr(item, "__slots__") and not isinstance(item, (int, float, str, bool)):
                    _walk_for_idents(item, out)
        elif hasattr(child, "__slots__"):
            _walk_for_idents(child, out)


# ── Codegen cache ────────────────────────────────────────────────

_codegen_infra_hash_cache: str | None = None


def _codegen_infra_hash() -> str:
    """SHA-256 over codegen infrastructure files + versions."""
    global _codegen_infra_hash_cache  # noqa: PLW0603
    if _codegen_infra_hash_cache is not None:
        return _codegen_infra_hash_cache
    h = hashlib.sha256()
    compiled_dir = Path(__file__).resolve().parent
    # Hash all codegen source files — captures any codegen logic changes
    for fname in (
        "codegen.py",
        "_expr_emitter.py",
        "_gen_sections.py",
        "_process_compiler.py",
        "_stmt_emitters.py",
        "_wide_emitter.py",
        "_codegen_utils.py",
    ):
        p = compiled_dir / fname
        if p.exists():
            h.update(p.read_bytes())
    # Hash elaborate.py — captures flattening/struct env changes
    elaborate_path = compiled_dir.parent / "elaborate.py"
    if elaborate_path.exists():
        h.update(elaborate_path.read_bytes())
    h.update(_CACHE_VERSION.encode("utf-8"))
    h.update(_cython_version().encode("utf-8"))
    h.update(_platform_tag().encode("utf-8"))
    _codegen_infra_hash_cache = h.hexdigest()
    return _codegen_infra_hash_cache


def _compute_elab_hash(module_name: str, source_files: list[str]) -> str:
    """Compute a hash from source files + codegen infrastructure + module name."""
    h = hashlib.sha256()
    h.update(module_name.encode("utf-8"))
    h.update(_codegen_infra_hash().encode("utf-8"))
    for sf in sorted(source_files):
        try:
            h.update(Path(sf).read_bytes())
        except OSError:
            h.update(sf.encode("utf-8"))
    return h.hexdigest()[:16]


def _elab_cache_path(cache_dir: str, elab_hash: str) -> Path:
    return Path(cache_dir) / f"_elab_{elab_hash}.json"


def _load_elab_cache(cache_dir: str, elab_hash: str) -> dict | None:
    """Load cached elaboration metadata, or None on failure."""
    path = _elab_cache_path(cache_dir, elab_hash)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Validate required keys
        for key in ("keyed_name", "signal_map", "sig_widths", "mem_map", "mem_info", "n_sigs", "n_mems"):
            if key not in data:
                return None
        # sig_signed is optional (added June 2026); default to all-unsigned
        if "sig_signed" not in data:
            data["sig_signed"] = [False] * data["n_sigs"]
        # Convert mem_info from lists back to tuples
        data["mem_info"] = [tuple(x) for x in data["mem_info"]]
        return data
    except Exception:
        log.debug("Elab cache load failed: %s", path, exc_info=True)
        return None


def _save_elab_cache(cache_dir: str, elab_hash: str, data: dict) -> None:
    """Save elaboration metadata to cache."""
    path = _elab_cache_path(cache_dir, elab_hash)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        log.debug("Elab cache save failed: %s", path, exc_info=True)


def _cleanup_elab_cache(cache_dir: str) -> None:
    """Remove stale elab cache entries whose .pyd no longer exists."""
    cache_path = Path(cache_dir)
    if not cache_path.is_dir():
        return
    try:
        for p in cache_path.glob("_elab_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                keyed_name = data.get("keyed_name", "")
                pyd_dir = cache_path / keyed_name
                if not pyd_dir.is_dir():
                    p.unlink(missing_ok=True)
            except Exception:
                log.debug("Failed to check elab cache entry %s", p)
    except Exception:
        log.debug("Elab cache cleanup failed for %s", cache_dir)


class _CodegenMeta:
    """Lightweight holder for codegen metadata loaded from cache.

    Provides the same property interface as CythonCodegen for use by
    _CompEvalContext and the scheduler log messages.
    """

    __slots__ = (
        "_mem_info",
        "_mem_map",
        "_n_mems",
        "_n_sigs",
        "_sig_signed",
        "_sig_widths",
        "_signal_map",
        "_timing_diagnostics",
    )

    def __init__(self, signal_map, sig_widths, sig_signed, mem_map, mem_info, n_sigs, n_mems):  # noqa: PLR0913
        self._signal_map = signal_map
        self._sig_widths = sig_widths
        self._sig_signed = sig_signed
        self._mem_map = mem_map
        self._mem_info = mem_info
        self._n_sigs = n_sigs
        self._n_mems = n_mems
        self._timing_diagnostics: list[str] = []

    @property
    def signal_map(self):
        return self._signal_map

    @property
    def signal_widths(self):
        return self._sig_widths

    @property
    def signal_signed(self):
        return self._sig_signed

    @property
    def n_sigs(self):
        return self._n_sigs

    @property
    def mem_map(self):
        return self._mem_map

    @property
    def mem_info(self):
        return self._mem_info

    @property
    def n_mems(self):
        return self._n_mems

    @property
    def timing_diagnostics(self) -> list[str]:
        return self._timing_diagnostics


class CompiledScheduler(EventQueueMixin, CoroutineMixin):  # cm:f8e1c2
    """Scheduler for the compiled Cython engine.

    Implements the same interface as ``Scheduler`` and ``VMScheduler``
    so that ``Simulator`` can use it as a drop-in replacement.

    Phase 4: initial blocks run through the reference executor, always
    blocks with timing controls use coroutine fallback, and $display
    output is captured from the reference executor.
    """

    __slots__ = (
        "_always_timing_blocks",
        "_always_timing_coroutines",
        "_always_timing_sync_names",
        "_bootstrapped",
        "_codegen",
        "_compiler",
        "_event_queue",
        "_event_seq",
        "_has_drive_snapshot",
        "_initial_blocks",
        "_initial_coroutines",
        "_initial_sync_names",
        "_mem_map",
        "_on_time_step",
        "_ref_ctx",
        "_ref_evaluator",
        "_ref_executor",
        "_sig_signed",
        "_sig_widths",
        "_signal_map",
        "_sim",
        "_stopped",
        "_time",
        "_write_buffer",
        "ctx",
        "delta_limit",
        "display_output",
    )

    def __init__(self, *, compiler: CythonCompiler | None = None, delta_limit: int = 10_000) -> None:
        self._compiler = compiler or CythonCompiler()
        self._codegen: CythonCodegen | None = None
        self._sim: object = None  # CompiledSim instance from generated module
        self._signal_map: dict[str, int] = {}
        self._sig_widths: list[int] = []
        self._sig_signed: list[bool] = []
        self._mem_map: dict[str, int] = {}
        self._time: int = 0
        self._event_queue: list[TimedEvent] = []
        self._event_seq: int = 0
        self.delta_limit: int = delta_limit
        self.display_output: list[str] = []
        self._write_buffer: str = ""

        # Initial block support (Phase 4)
        self._initial_blocks: list = []
        self._initial_coroutines: dict[int, object] = {}
        self._initial_sync_names: dict[int, set[str] | None] = {}

        # Always-with-timing coroutine support
        self._always_timing_blocks: list = []
        self._always_timing_coroutines: dict[int, tuple[object, object]] = {}
        self._always_timing_sync_names: dict[int, set[str] | None] = {}

        # Reference executor for fallback
        self._ref_evaluator: ExpressionEvaluator | None = None
        self._ref_executor: StatementExecutor | None = None
        self._ref_ctx: EvalContext | None = None

        # Time-step callback (VCD recording)
        self._on_time_step: Callable[[CompiledScheduler], None] | None = None

        # Track whether a snapshot has been taken before external drives
        self._has_drive_snapshot: bool = False

        # Stop flag ($finish)
        self._stopped: bool = False

        # Guard: prevent re-executing initial blocks on subsequent run() calls
        self._bootstrapped: bool = False

        # EvalContext-like wrapper for testbench signal access
        self.ctx: _CompEvalContext | None = None

    @property
    def time(self) -> int:
        return self._time

    def _sim_read_signal(self, sid: int) -> tuple[int, int]:
        if self._sig_widths[sid] > 64:
            return self._sim.read_wide(sid)
        return self._sim.read(sid)

    def _sim_drive_signal(self, sid: int, val: int, mask: int) -> None:
        if self._sig_widths[sid] > 64:
            self._sim.drive_wide(sid, val, mask)
        else:
            self._sim.drive(sid, _to_i64(val), _to_i64(mask))

    # ── Elaboration ──────────────────────────────────────────────

    def elaborate(self, module: Module, *, source_files: list[str] | None = None) -> None:
        """Generate .pyx, compile, import, and initialize.

        Args:
            module: The flattened module to compile.
            source_files: Source file paths for codegen caching.
                If provided, codegen results are cached and reused
                when source files haven't changed.
        """
        # Try codegen cache: skip .pyx generation if sources unchanged
        elab_hash: str | None = None
        if source_files:
            elab_hash = _compute_elab_hash(module.name, source_files)
            cached = _load_elab_cache(self._compiler.cache_dir, elab_hash)
            if cached is not None:
                mod = self._compiler.load_cached(cached["keyed_name"])
                if mod is not None:
                    log.info("Codegen cache hit for %s (hash %s)", module.name, elab_hash)
                    self._sim = mod.CompiledSim()
                    self._signal_map = cached["signal_map"]
                    self._sig_widths = cached["sig_widths"]
                    self._sig_signed = cached["sig_signed"]
                    self._mem_map = cached["mem_map"]
                    # Create a lightweight codegen metadata holder for _CompEvalContext
                    self._codegen = _CodegenMeta(
                        cached["signal_map"],
                        cached["sig_widths"],
                        cached["sig_signed"],
                        cached["mem_map"],
                        cached["mem_info"],
                        cached["n_sigs"],
                        cached["n_mems"],
                    )
                    self._setup_fallback(module)
                    return

        # Cache miss — run full codegen + compile (streaming path to cap peak memory)
        self._codegen = CythonCodegen()
        tmp_fd, tmp_pyx_path = tempfile.mkstemp(suffix=".pyx", prefix="veriforge_")
        os.close(tmp_fd)
        try:
            source_hash = self._codegen.generate_to_file(module, tmp_pyx_path, delta_limit=self.delta_limit)
            try:
                mod = self._compiler.compile_pyx_file(tmp_pyx_path, source_hash, f"compiled_{module.name}")
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to compile Cython extension for module '{module.name}'. "
                    f"Ensure a C compiler is available or use engine='vm'. "
                    f"Original error: {exc}"
                ) from exc
        finally:
            try:
                os.unlink(tmp_pyx_path)
            except FileNotFoundError:
                pass

        self._sim = mod.CompiledSim()
        self._signal_map = dict(self._codegen.signal_map)
        self._sig_widths = list(self._codegen.signal_widths)
        self._sig_signed = list(self._codegen.signal_signed)
        self._mem_map = dict(self._codegen.mem_map)

        # Save to codegen cache
        if elab_hash is not None:
            pyx_key = _cache_key_from_source_hash(source_hash)
            keyed_name = _keyed_module_name(f"compiled_{module.name}", pyx_key)
            _save_elab_cache(
                self._compiler.cache_dir,
                elab_hash,
                {
                    "keyed_name": keyed_name,
                    "signal_map": self._signal_map,
                    "sig_widths": self._sig_widths,
                    "sig_signed": self._sig_signed,
                    "mem_map": self._mem_map,
                    "mem_info": list(self._codegen.mem_info),
                    "n_sigs": self._codegen.n_sigs,
                    "n_mems": self._codegen.n_mems,
                },
            )

        self._setup_fallback(module)

    def _setup_fallback(self, module: Module) -> None:
        """Set up reference executor fallback and testbench context."""

        # Collect initial blocks that need fallback execution.
        # Blocks without timing or system tasks are compiled natively into CompiledSim.__init__().
        # Only blocks with timing controls or system tasks need the reference executor.
        self._initial_blocks = [
            block
            for block in module.initial_blocks
            if CythonCodegen._has_timing(block.body) or CythonCodegen._has_system_tasks(block.body)
        ]

        # Collect always blocks with timing controls (skipped by codegen)
        self._always_timing_blocks = [block for block in module.always_blocks if CythonCodegen._has_timing(block.body)]

        # Set up reference executor for fallback
        self._ref_evaluator = ExpressionEvaluator()
        self._ref_executor = StatementExecutor(self._ref_evaluator)
        self._ref_executor._function_map = {f.name: f for f in module.functions}
        self._ref_executor._task_map = {t.name: t for t in module.tasks}
        self._ref_ctx = EvalContext()
        from ..elaborate import _build_struct_env

        _type_map, struct_signal_map = _build_struct_env(module)
        self._ref_ctx._struct_type_map.update(_type_map)
        self._ref_ctx._struct_types.update(struct_signal_map)
        self._sync_ref_ctx()

        # Create EvalContext wrapper for testbench signal access
        self.ctx = _CompEvalContext(self._signal_map, self._sig_widths, self._sim, self._codegen)

        # Pre-compute per-coroutine sync name sets for targeted sync.
        # This avoids syncing all 273+ signals when a timing block only touches a few.
        self._always_timing_sync_names = {}
        for i, block in enumerate(self._always_timing_blocks):
            names = _collect_identifiers(block.body) & set(self._signal_map)
            self._always_timing_sync_names[i] = names if names else None

        # Pre-compute per-initial-block sync name sets for targeted sync.
        self._initial_sync_names = {}
        for i, block in enumerate(self._initial_blocks):
            names = _collect_identifiers(block.body) & set(self._signal_map)
            self._initial_sync_names[i] = names if names else None

        log.info(
            "Compiled engine elaborated: %d signals, %d memories, %d initial blocks, %d timing-fallback always blocks",
            self._codegen.n_sigs,
            self._codegen.n_mems,
            len(self._initial_blocks),
            len(self._always_timing_blocks),
        )
        for diag in self._codegen.timing_diagnostics:
            log.warning("Compiled simulation performance: %s", diag)
            warnings.warn(
                f"Compiled engine preflight (module '{module.name}'): {diag}",
                UserWarning,
                stacklevel=4,
            )

    # ── Signal sync ──────────────────────────────────────────────

    def _sync_ref_ctx(self, names: set[str] | None = None) -> None:
        """Copy signal values from compiled sim → reference EvalContext."""
        if self._ref_ctx is None:
            return
        ref_sigs = self._ref_ctx._signals
        if names is not None:
            for name in names:
                sid = self._signal_map.get(name)
                if sid is not None:
                    v, m = self._sim_read_signal(sid)
                    ref_sigs[name] = Value(v, width=self._sig_widths[sid], mask=m)
        else:
            for name, sid in self._signal_map.items():
                v, m = self._sim_read_signal(sid)
                ref_sigs[name] = Value(v, width=self._sig_widths[sid], mask=m)

    def _sync_from_ref_ctx(self, names: set[str] | None = None) -> None:
        """Copy signal values from reference EvalContext → compiled sim.

        Only drives signals that actually changed, to maintain dirty tracking.
        """
        if self._ref_ctx is None:
            return
        ref_sigs = self._ref_ctx._signals
        if names is not None:
            for name in names:
                v = ref_sigs.get(name)
                if v is not None:
                    sid = self._signal_map.get(name)
                    if sid is not None:
                        old_v, old_m = self._sim_read_signal(sid)
                        if self._sig_widths[sid] > 64:
                            vv = v.val
                            vm = v.mask
                        else:
                            vv = _to_i64(v.val)
                            vm = _to_i64(v.mask)
                        if old_v != vv or old_m != vm:
                            self._sim_drive_signal(sid, vv, vm)
        else:
            for name, sid in self._signal_map.items():
                v = ref_sigs.get(name)
                if v is not None:
                    old_v, old_m = self._sim_read_signal(sid)
                    if self._sig_widths[sid] > 64:
                        vv = v.val
                        vm = v.mask
                    else:
                        vv = _to_i64(v.val)
                        vm = _to_i64(v.mask)
                    if old_v != vv or old_m != vm:
                        self._sim_drive_signal(sid, vv, vm)

    def _sync_mem_to_ref(self) -> None:
        """Copy memory arrays from compiled sim → reference EvalContext.

        Populates ``_ref_ctx._memories`` from the compiled sim so that
        the reference executor can read/write memory elements (e.g. for
        ``$readmemh``).
        """
        if self._ref_ctx is None or self._codegen is None:
            return
        for name, mid in self._mem_map.items():
            elem_w, depth = self._codegen.mem_info[mid]
            mem_data: list[Value] = []
            for addr in range(depth):
                v, m = self._sim.mem_read(mid, addr)
                mem_data.append(Value(v, width=elem_w, mask=m))
            self._ref_ctx._memories[name] = (mem_data, elem_w)
            self._ref_ctx._memory_names.add(name)

    def _sync_mem_from_ref(self) -> None:
        """Copy memory arrays from reference EvalContext → compiled sim.

        Called after the reference executor runs ``$readmemh`` or other
        memory-writing system tasks so the loaded data reaches the
        compiled C arrays.
        """
        if self._ref_ctx is None:
            return
        for name, mid in self._mem_map.items():
            entry = self._ref_ctx._memories.get(name)
            if entry is None:
                continue
            mem_data, _elem_w = entry
            for addr, val in enumerate(mem_data):
                elem_w, _depth = self._codegen.mem_info[mid]
                if elem_w > 64:
                    self._sim.mem_write_wide(mid, addr, int(val.val), int(val.mask))
                else:
                    v = int(val.val) & 0xFFFFFFFFFFFFFFFF
                    m = int(val.mask) & 0xFFFFFFFFFFFFFFFF
                    # Convert to signed long long range for Cython
                    if v >= 0x8000000000000000:
                        v -= 0x10000000000000000
                    if m >= 0x8000000000000000:
                        m -= 0x10000000000000000
                    self._sim.mem_write(mid, addr, v, m)

    def _drain_display(self) -> None:
        """Drain $display output from the reference executor."""
        if self._ref_executor is not None:
            self.display_output.extend(self._ref_executor.display_output)
            self._ref_executor.display_output.clear()

    def _drain_compiled_output(self) -> None:
        """Drain $write/$display output from the compiled engine's buffer."""
        raw = self._sim.drain_output()
        if not raw:
            return
        text = raw.decode("ascii", errors="replace")
        # Split on newlines: each newline produces a display_output line
        # (consistent with reference engine where $display appends newline)
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                # There was a newline after this part
                self.display_output.append(self._write_buffer + part)
                self._write_buffer = ""
            else:
                # Last part — no trailing newline, accumulate in write buffer
                self._write_buffer += part

    # ── Signal access ────────────────────────────────────────────

    def drive_signal(self, name: str, value: Value | int) -> None:
        """Drive a signal from the testbench."""
        sid = self._signal_map.get(name)
        if sid is not None:
            if isinstance(value, int):
                value = Value(value, width=self._sig_widths[sid])
            # Snapshot BEFORE the first drive so edge detection works correctly
            if not self._has_drive_snapshot:
                self._sim.snapshot()
                self._has_drive_snapshot = True
            self._sim_drive_signal(sid, value.val, value.mask)
            return
        if "[" in name and self._codegen is not None and name.endswith("]"):
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._codegen.mem_map.get(mem_name)
            if mid is None:
                return
            ew, depth = self._codegen.mem_info[mid]
            idx = int(name[bracket + 1 : -1])
            if not 0 <= idx < depth:
                return
            if isinstance(value, int):
                value = Value(value, width=ew)
            if not self._has_drive_snapshot:
                self._sim.snapshot()
                self._has_drive_snapshot = True
            if ew > 64:
                self._sim.mem_write_wide(mid, idx, int(value.val), int(value.mask))
            else:
                self._sim.mem_write(mid, idx, _to_i64(value.val), _to_i64(value.mask))

    def settle(self) -> None:
        """Propagate pending external drives through combinational logic at the current time."""
        if not self._has_drive_snapshot:
            return
        # Refresh the data snapshot so non-clock signals driven before the clock
        # edge (e.g. rst=0, i_sum=160 driven before clk=1) are visible in sv[]
        # when seq process bodies evaluate their RHS — matching reference engine
        # behavior where ctx._signals holds post-drive values.  The clock signal's
        # pre-edge value is preserved by refresh_data_snapshot() for correct
        # posedge/negedge detection.
        if hasattr(self._sim, "refresh_data_snapshot"):
            self._sim.refresh_data_snapshot()
        self._sim.step()
        self._drain_compiled_output()
        self._has_drive_snapshot = False

    def read_signal(self, name: str) -> Value:
        """Read a signal value.  Supports ``"MEM[idx]"`` syntax."""
        sid = self._signal_map.get(name)
        if sid is not None:
            v, m = self._sim_read_signal(sid)
            return Value(v, width=self._sig_widths[sid], mask=m)
        # Try memory array element: "MEM[idx]"
        if "[" in name and self._codegen is not None:
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._codegen.mem_map.get(mem_name)
            if mid is not None and name.endswith("]"):
                ew, depth = self._codegen.mem_info[mid]
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < depth:
                    v, m = self._sim.mem_read(mid, idx)
                    return Value(v, width=ew, mask=m)
        if "." in name and self._ref_ctx is not None and self._ref_evaluator is not None:
            self._sync_ref_ctx()
            if self._mem_map:
                self._sync_mem_to_ref()
            return self._ref_evaluator.eval(_identifier_from_name(name), self._ref_ctx)
        return Value.x(1)

    def signal_names(self) -> set[str]:
        """Return the set of all signal names in the simulation."""
        names = set(self._signal_map.keys())
        # Include memory array elements as individual signals
        if self._codegen is not None:
            for mem_name, mid in self._codegen.mem_map.items():
                _ew, depth = self._codegen.mem_info[mid]
                for idx in range(depth):
                    names.add(f"{mem_name}[{idx}]")
        return names

    # ── Event scheduling ─────────────────────────────────────────

    def schedule_at(self, time: int, proc: object) -> None:
        """Schedule a process/event at a specific simulation time."""
        self._schedule_event(time, proc)

    # ── Initial block execution ──────────────────────────────────

    def _execute_initial_blocks(self) -> bool:
        """Execute all initial blocks at t=0.

        Uses the reference executor for all initial blocks (both with
        and without timing). Timing blocks are started as coroutines
        and scheduled for later resumption.

        Returns True if $finish was encountered.
        """
        for i, block in enumerate(self._initial_blocks):
            if self._has_timing_block(block):
                if self._execute_initial_with_timing(block, i):
                    return True
            elif self._execute_initial_simple(block):
                return True

        # Wire VCD callback if $dumpvars was executed in an initial block
        self._wire_vcd_from_ref()

        return False

    def _wire_vcd_from_ref(self) -> None:
        """If the reference executor created a VCD writer and no external
        callback is already registered, wire it into the compiled
        scheduler's time-step callback.

        Uses a compiled-specific fast path that reads raw (value, mask) pairs
        directly from the compiled sim, skips unchanged signals via tuple
        comparison, converts to VCD strings inline (no Value objects), and
        batches all output into a single file write per timestep.
        """
        if self._on_time_step is not None:
            return
        if self._ref_executor is None:
            return
        writer = getattr(self._ref_executor, "_vcd_writer", None)
        if writer is None:
            return

        # Build pre-computed info per VCD signal: (sid, width, ident, fmt)
        # fmt is the binary format string for multi-bit signals (e.g. "032b")
        vcd_info: list[tuple[int, int, str, str]] = []
        for name, sig in writer._signals.items():
            sid = self._signal_map.get(name)
            if sid is not None:
                fmt = f"0{sig.width}b" if sig.width > 1 else ""
                vcd_info.append((sid, sig.width, sig.ident, fmt))

        # Use a flat list for change detection (indexed by sid) instead of dict.
        max_sid = max(sid for sid, _, _, _ in vcd_info) + 1 if vcd_info else 0
        # Sentinel value that won't match any real (v, m) tuple.
        _sentinel = (-1, -1)
        last_v = [_sentinel] * max_sid
        last_m = [_sentinel] * max_sid

        sim_read = self._sim_read_signal
        vcd_file = writer._file

        def _fast_vcd_callback(scheduler) -> None:
            parts: list[str] = []
            ap = parts.append
            for sid, width, ident, fmt in vcd_info:
                v, m = sim_read(sid)
                if v == last_v[sid] and m == last_m[sid]:
                    continue
                last_v[sid] = v
                last_m[sid] = m
                if width == 1:
                    ap("x" + ident + "\n" if m & 1 else ("1" + ident + "\n" if v & 1 else "0" + ident + "\n"))
                elif m == 0:
                    ap("b" + format(v, fmt) + " " + ident + "\n")
                else:
                    # Has x/z bits — per-bit conversion
                    chars: list[str] = []
                    cap = chars.append
                    for i in range(width - 1, -1, -1):
                        if m & (1 << i):
                            cap("x")
                        elif v & (1 << i):
                            cap("1")
                        else:
                            cap("0")
                    ap("b" + "".join(chars) + " " + ident + "\n")
            if parts:
                parts.insert(0, "#" + str(scheduler.time) + "\n")
                vcd_file.write("".join(parts))

        self._on_time_step = _fast_vcd_callback

    def _has_timing_block(self, block) -> bool:
        """Check if an initial block body has timing controls."""
        return CythonCodegen._has_timing(block.body)

    def _execute_initial_simple(self, block) -> bool:
        """Execute an initial block without timing controls.

        Runs synchronously through the reference executor.
        Returns True if $finish was encountered.
        """
        self._sync_ref_ctx()
        self._sync_mem_to_ref()
        self._ref_executor.time = self._time
        try:
            self._ref_executor.execute(block.body, self._ref_ctx)
        except StopExecution:
            self._drain_display()
            self._sync_from_ref_ctx()
            self._sync_mem_from_ref()
            self._event_queue.clear()
            return True
        self._drain_display()
        self._sync_from_ref_ctx()
        self._sync_mem_from_ref()
        return False

    def _execute_initial_with_timing(self, block, proc_id: int) -> bool:
        """Execute an initial block that contains timing controls.

        Delegates to ``CoroutineMixin._run_initial_coro``.
        Returns True if $finish was encountered.
        """
        return self._run_initial_coro(block.body, proc_id)

    # ── Always-with-timing fallback ──────────────────────────────

    def _schedule_always_with_timing(self) -> None:
        """Start all always blocks with timing as coroutines."""
        for i, block in enumerate(self._always_timing_blocks):
            self._start_always_coro(block.body, i)

    # -- CoroutineMixin hooks --------------------------------------------------

    def _coro_get_sync_names(self, proc_id: int) -> set[str] | None:
        """Return signal names for targeted sync of always-with-timing blocks."""
        return self._always_timing_sync_names.get(proc_id)

    def _coro_get_initial_sync_names(self, proc_id: int) -> set[str] | None:
        """Return signal names for targeted sync of initial blocks."""
        return self._initial_sync_names.get(proc_id)

    def _coro_sync_in(self, names: set[str] | None = None) -> None:
        self._sync_ref_ctx(names)
        if self._mem_map:
            self._sync_mem_to_ref()
        self._ref_executor.time = self._time

    def _coro_sync_out(self, names: set[str] | None = None) -> None:
        self._drain_display()
        self._sync_from_ref_ctx(names)
        if self._mem_map:
            self._sync_mem_from_ref()

    def _coro_post_resume(self) -> None:
        self._wire_vcd_from_ref()
        # Refresh the data snapshot so sequential RHS reads see coro-driven values,
        # while preserving pre-timestep clock snapshot for correct edge detection.
        if hasattr(self._sim, "refresh_data_snapshot"):
            self._sim.refresh_data_snapshot()

    # ── Simulation control ───────────────────────────────────────

    def run(self, *, max_time: int = 1_000_000) -> None:
        """Run the full event loop to completion."""
        self._stopped = False

        if not self._bootstrapped:
            # Execute initial blocks at t=0 BEFORE bootstrap
            if self._execute_initial_blocks():
                return

            # Schedule always blocks with timing as coroutines
            self._schedule_always_with_timing()

            self._bootstrapped = True

        # Bootstrap / re-bootstrap: run delta loop to settle continuous assigns.
        # This must run on every run() call so that external drive() changes
        # propagate through combinational logic.
        # If drive_signal() already took a snapshot, reuse it for correct
        # edge detection (stale values captured before the drive).
        if not self._has_drive_snapshot:
            self._sim.snapshot()
        self._has_drive_snapshot = False
        self._sim.step()
        self._drain_compiled_output()
        if self._sim.is_finished():
            self._stopped = True

        # Fire time-step callback at t=0
        if self._on_time_step is not None:
            self._on_time_step(self)

        while self._event_queue and not self._stopped:
            ev = self._event_queue[0]
            if ev.time > max_time:
                break
            self._time = ev.time
            self._sim.set_time(self._time)

            # Snapshot current signal values BEFORE applying events
            self._sim.snapshot()

            # Process all events at current time
            events = self._pop_events_at(self._time)
            for event in events:
                if self._execute_event(event):
                    self._stopped = True
                    break

            if self._stopped:
                # $finish: run final delta loop and VCD snapshot
                self._sim.step()
                self._drain_compiled_output()
                if self._on_time_step is not None:
                    self._on_time_step(self)
                break

            # Run delta loop after events
            self._sim.step()
            self._drain_compiled_output()
            if self._sim.is_finished():
                self._stopped = True
                if self._on_time_step is not None:
                    self._on_time_step(self)
                break

            # Fire time-step callback
            if self._on_time_step is not None:
                self._on_time_step(self)

        # Finalize VCD writer if active
        if self._ref_executor is not None and self._ref_executor._vcd_writer is not None:
            self._ref_executor._vcd_writer.finalize()
            self._ref_executor._vcd_writer = None

    def run_step(self) -> bool:
        """Advance one time step. Returns True if events remain."""
        if not self._event_queue:
            return False

        next_time = self._event_queue[0].time
        self._time = next_time
        self._sim.set_time(self._time)

        # Preserve any pre-drive snapshot from drive_signal(); only take a fresh
        # snapshot if no external drive has happened since the last step.
        if not self._has_drive_snapshot:
            self._sim.snapshot()

        # Process all events at this time
        events = self._pop_events_at(self._time)
        for event in events:
            if self._execute_event(event):
                self._stopped = True
                self._has_drive_snapshot = False
                # Run final delta loop and fire VCD callback so the last
                # blocking-assignment state before $finish is captured.
                self._sim.step()
                self._drain_compiled_output()
                if self._on_time_step is not None:
                    self._on_time_step(self)
                return False

        # Consume the snapshot: clear flag before the delta loop runs
        self._has_drive_snapshot = False

        # Refresh data-signal snapshot so that externally-driven signals
        # (a, b, sh, etc.) are visible to sequential RHS reads at their
        # post-drive values, while the pre-event clock value is preserved
        # for correct posedge/negedge detection.
        if hasattr(self._sim, "refresh_data_snapshot"):
            self._sim.refresh_data_snapshot()

        # Run delta loop
        self._sim.step()
        self._drain_compiled_output()
        if self._sim.is_finished():
            self._stopped = True
            if self._on_time_step is not None:
                self._on_time_step(self)
            return False

        # Fire time-step callback
        if self._on_time_step is not None:
            self._on_time_step(self)

        return True

    def _execute_event(self, payload: object) -> bool:
        """Execute a single event. Returns True if simulation should stop."""
        if isinstance(payload, tuple):
            if len(payload) == 3 and payload[0] == "clock_toggle":  # noqa: PLR2004
                _, sig_name, value = payload
                self.drive_signal(sig_name, value)
                return False

            if len(payload) == 2 and payload[0] == "initial_coro":  # noqa: PLR2004
                return self._resume_initial_coro(payload[1])

            if len(payload) == 2 and payload[0] == "always_coro":  # noqa: PLR2004
                return self._resume_always_coro(payload[1])

        return False

    # ── Bulk memory I/O ──────────────────────────────────────────────

    def load_memory(self, name: str, data) -> None:
        """Bulk-load a named DSL memory from a sequence or numpy array."""
        if self._codegen is None:
            raise RuntimeError("CompiledScheduler not yet elaborated.")
        mid = self._codegen.mem_map.get(name)
        if mid is None:
            raise ValueError(f"Unknown memory {name!r}. Available: {list(self._codegen.mem_map)}")
        ew, _depth = self._codegen.mem_info[mid]
        mask = (1 << ew) - 1
        for i, v in enumerate(data):
            v = int(v) & mask
            if ew > 64:
                self._sim.mem_write_wide(mid, i, v, mask)
            else:
                self._sim.mem_write(mid, i, _to_i64(v), _to_i64(mask))

    def dump_memory(self, name: str, count: int) -> list[int]:
        """Read *count* elements from a named DSL memory and return as a list."""
        if self._codegen is None:
            raise RuntimeError("CompiledScheduler not yet elaborated.")
        mid = self._codegen.mem_map.get(name)
        if mid is None:
            raise ValueError(f"Unknown memory {name!r}. Available: {list(self._codegen.mem_map)}")
        result = []
        for i in range(count):
            v, _ = self._sim.mem_read(mid, i)
            result.append(int(v))
        return result

    def batch_run(
        self,
        cycles: int,
        clock_name: str,
        clock_period: int = 10,
        events: list[tuple[int, str, int]] | None = None,
    ) -> int:
        """Run *cycles* full clock cycles entirely in C.

        Each cycle consists of a posedge (clk→1) + delta-loop then a
        negedge (clk→0) + delta-loop, all executed inside the compiled
        extension with ``nogil``.

        Args:
            cycles: Number of full clock cycles to execute.
            clock_name: Name of the clock signal to toggle.
            clock_period: Period of one full clock cycle in time units.
            events: Optional list of ``(cycle, signal_name, value)`` tuples.
                Applied before the posedge of the given cycle.  Must be
                sorted by cycle number.

        Returns:
            Number of cycles actually completed.
        """
        clk_sid = self._signal_map.get(clock_name)
        if clk_sid is None:
            raise ValueError(f"Unknown clock signal: {clock_name!r}")

        # If drive_signal() took a snapshot since the last step (i.e. there
        # are pending bench-side drives that haven't propagated through
        # continuous assigns yet), settle those drives first using the
        # captured snapshot. This ensures edge detection on the driven
        # signal works correctly (e.g. a posedge on a bench reset reaches
        # any always block sensitive to it) AND that downstream port wiring
        # (e.g. DUT rst port driven by bench rst reg via a cont_assign) is
        # current before the first posedge snapshot inside batch_run.
        if self._has_drive_snapshot:
            self._sim.step()
            self._drain_compiled_output()
            self._has_drive_snapshot = False

        if events:
            import array

            n = len(events)
            ev_cycles = array.array("i", [e[0] for e in events])
            ev_sids = array.array("i", [self._signal_map[e[1]] for e in events])
            ev_vals = array.array("q", [e[2] for e in events])
            completed = self._sim.batch_run(
                cycles,
                clk_sid,
                n_events=n,
                ev_cycles=ev_cycles,
                ev_sids=ev_sids,
                ev_vals=ev_vals,
            )
        else:
            completed = self._sim.batch_run(cycles, clk_sid)
        self._time += completed * clock_period
        self._sim.set_time(self._time)
        return completed


# ── EvalContext wrapper ──────────────────────────────────────────────


class _CompEvalContext:
    """EvalContext-like wrapper over compiled sim for testbench access.

    The testbench code and validation harnesses access ``sched.ctx._signals``
    as a dict. This wrapper translates dict operations to compiled sim calls.
    """

    __slots__ = ("_codegen", "_sig_widths", "_signal_map", "_signals", "_sim")

    def __init__(self, signal_map: dict[str, int], sig_widths: list[int], sim, codegen=None) -> None:
        self._signal_map = signal_map
        self._sig_widths = sig_widths
        self._sim = sim
        self._codegen = codegen
        self._signals = _CompSignalDict(signal_map, sig_widths, sim)

    def read_signal(self, name: str) -> Value:
        sid = self._signal_map.get(name)
        if sid is not None:
            if self._sig_widths[sid] > 64:
                v, m = self._sim.read_wide(sid)
            else:
                v, m = self._sim.read(sid)
            return Value(v, width=self._sig_widths[sid], mask=m)
        # Try memory array element: "MEM[idx]"
        if "[" in name and self._codegen is not None:
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._codegen.mem_map.get(mem_name)
            if mid is not None and name.endswith("]"):
                ew, depth = self._codegen.mem_info[mid]
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < depth:
                    v, m = self._sim.mem_read(mid, idx)
                    return Value(v, width=ew, mask=m)
        return Value.x(1)

    def write_signal(self, name: str, value: Value) -> None:
        sid = self._signal_map.get(name)
        if sid is not None:
            if self._sig_widths[sid] > 64:
                self._sim.drive_wide(sid, value.val, value.mask)
            else:
                self._sim.drive(sid, _to_i64(value.val), _to_i64(value.mask))
            return
        if "[" in name and self._codegen is not None and name.endswith("]"):
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._codegen.mem_map.get(mem_name)
            if mid is None:
                return
            ew, depth = self._codegen.mem_info[mid]
            idx = int(name[bracket + 1 : -1])
            if not 0 <= idx < depth:
                return
            if ew > 64:
                self._sim.mem_write_wide(mid, idx, int(value.val), int(value.mask))
            else:
                self._sim.mem_write(mid, idx, _to_i64(value.val), _to_i64(value.mask))


class _CompSignalDict(SignalDictBase):
    """Dict-like wrapper over compiled sim signal arrays.

    Provides the ``_signals`` interface expected by VCD recording and
    validation harnesses that access ``sched.ctx._signals``.
    """

    __slots__ = ("_sig_widths", "_signal_map", "_sim")

    def __init__(self, signal_map: dict[str, int], sig_widths: list[int], sim) -> None:
        self._signal_map = signal_map
        self._sig_widths = sig_widths
        self._sim = sim

    def _sig_map(self) -> dict[str, int]:
        return self._signal_map

    def _read_sid(self, sid: int) -> tuple[int, int, int]:
        if self._sig_widths[sid] > 64:
            v, m = self._sim.read_wide(sid)
        else:
            v, m = self._sim.read(sid)
        return (v, m, self._sig_widths[sid])

    def _write_sid(self, sid: int, val: int, mask: int) -> None:
        if self._sig_widths[sid] > 64:
            self._sim.drive_wide(sid, val, mask)
        else:
            self._sim.drive(sid, _to_i64(val), _to_i64(mask))
