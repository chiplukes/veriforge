"""Cross-simulator validation (cosimulation).

Compare our simulator against Icarus Verilog on arbitrary Verilog designs.
Supports both single-source and multi-file projects.

Quickstart — single file::

    from veriforge.sim.cosim import IcarusCosim

    verilog_src = '''
    module test;
        reg clk = 0;
        always #5 clk = ~clk;
        initial begin
            $dumpfile("test.vcd");
            $dumpvars(0, test);
            #100 $finish;
        end
    endmodule
    '''
    cosim = IcarusCosim(verilog_src=verilog_src)
    diffs = cosim.run()
    assert not diffs, "\\n".join(diffs)

Quickstart — multi-file project::

    from veriforge.sim.cosim import IcarusCosim

    cosim = IcarusCosim(
        files=["rtl/top.v", "rtl/sub.v", "sim/testbench.v"],
        top_module="testbench",
        defines={"SIM": "1"},
        work_dir="sim/",       # cwd for $readmemh etc.
    )
    diffs = cosim.run(engine="reference", max_time=5000)
    for d in diffs:
        print(d)

Step-by-step comparison::

    cosim = IcarusCosim(files=[...], top_module="testbench")
    mismatch = cosim.run_cycle_by_cycle(
        engine="reference",
        max_cycles=300,
        reset_cycles=10,
        clock_name="clk",
    )
    if mismatch:
        print(f"First mismatch at cycle {mismatch.cycle}:")
        for sig, ic_val, ref_val in mismatch.signals:
            print(f"  {sig}: icarus={ic_val} ref={ref_val}")
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import logging

from .trace import attach_vcd
from .vcd_compare import compare_vcd, parse_vcd

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .testbench import Simulator


# ── Engine helpers ───────────────────────────────────────────────────


def available_engines() -> list[str]:
    """Return the engine names that can be used for cosimulation on this machine.

    Always includes ``"reference"``, ``"vm"``, and ``"vm-fast"``.
    Adds ``"compiled"`` when the Cython toolchain is available.
    """
    engines = ["reference", "vm", "vm-fast"]
    try:
        from .compiled.compiler import CythonCompiler  # noqa: PLC0415

        compiler = CythonCompiler()
        compiler.compile_pyx("cpdef int _probe(): return 1", "_cosim_probe")
        engines.append("compiled")
    except Exception:
        pass
    return engines


# ── Icarus discovery ─────────────────────────────────────────────────

_WINDOWS_SEARCH_DIRS: list[str] = [
    r"C:\iverilog\bin",
    r"C:\Program Files\Icarus Verilog\bin",
    r"C:\Program Files (x86)\Icarus Verilog\bin",
]


def find_icarus(tool: str = "iverilog") -> str | None:
    """Locate an Icarus Verilog executable.

    Search order:
      1. Environment variable (``IVERILOG`` or ``VVP``).
      2. System ``PATH`` via ``shutil.which()``.
      3. (Windows) Well-known install directories.

    Returns the path to the executable, or ``None``.
    """
    env_val = os.environ.get(tool.upper())
    if env_val and os.path.isfile(env_val):
        return env_val

    found = shutil.which(tool)
    if found:
        return found

    if sys.platform == "win32":
        exe = f"{tool}.exe"
        for d in _WINDOWS_SEARCH_DIRS:
            candidate = os.path.join(d, exe)
            if os.path.isfile(candidate):
                return candidate

    return None


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class CycleMismatch:
    """Result of a cycle-by-cycle comparison mismatch."""

    cycle: int
    signals: list[tuple[str, str, str]]
    """List of ``(signal_name, icarus_value, ref_value)`` tuples."""


@dataclass
class CosimResult:
    """Full result from a cosimulation run."""

    diffs: list[str]
    """Human-readable difference strings (empty = match)."""

    icarus_signal_count: int = 0
    ref_signal_count: int = 0
    compared_signal_count: int = 0
    icarus_vcd: str = ""
    """Raw VCD text from Icarus (for further analysis if needed)."""


# ── VCD Recording helper ────────────────────────────────────────────


def record_vcd(sim: Simulator, *, max_time: int = 1000) -> str:
    """Run a simulator and capture VCD output as a string.

    This is a convenience wrapper that sets up VCD recording via the
    scheduler's time-step callback, runs the simulation, and returns
    the VCD file content.

    Args:
        sim: An already-constructed :class:`Simulator` instance.
        max_time: Maximum simulation time.

    Returns:
        VCD file content as a string.
    """
    vcd_buf = io.StringIO()
    with attach_vcd(sim, vcd_buf):
        sim.run(max_time=max_time)
    return vcd_buf.getvalue()


# ── Main cosim class ─────────────────────────────────────────────────


class IcarusCosim:  # cm:9d7c3f
    """Cross-simulator validation against Icarus Verilog.

    Provides two comparison modes:

    * **VCD comparison** (``run()``) — run both simulators, compare VCD
      output at every time step using :func:`compare_vcd`.
    * **Cycle-by-cycle** (``run_cycle_by_cycle()``) — step both
      simulators one clock at a time and report the first cycle where
      signals diverge.

    Args:
        verilog_src: Single Verilog source string (for simple designs).
        files: List of Verilog file paths (for multi-file designs).
        top_module: Name of the top-level module. Required for multi-file
            designs; auto-detected for single-file.
        defines: Preprocessor defines (``{name: value}``).
        work_dir: Working directory for simulation (for ``$readmemh`` etc.).
            Defaults to the directory of the first file.
        iverilog_path: Path to ``iverilog`` executable. Auto-detected if
            not provided.
        vvp_path: Path to ``vvp`` executable. Auto-detected if not
            provided.
        iverilog_flags: Extra flags passed to ``iverilog`` compilation.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        verilog_src: str | None = None,
        files: list[str] | None = None,
        top_module: str | None = None,
        defines: dict[str, str] | None = None,
        work_dir: str | None = None,
        iverilog_path: str | None = None,
        vvp_path: str | None = None,
        iverilog_flags: list[str] | None = None,
    ) -> None:
        if verilog_src is None and files is None:
            raise ValueError("Either verilog_src or files must be provided")
        if verilog_src is not None and files is not None:
            raise ValueError("Provide either verilog_src or files, not both")

        self._verilog_src = verilog_src
        self._files = [str(Path(f).resolve()) for f in files] if files else None
        self._top_module = top_module
        self._defines = defines or {}
        self._work_dir = work_dir
        self._iverilog_flags = iverilog_flags or []

        # Discover Icarus tools
        self._iverilog = iverilog_path or find_icarus("iverilog")
        self._vvp = vvp_path or find_icarus("vvp")
        if self._iverilog is None or self._vvp is None:
            raise RuntimeError(
                "Icarus Verilog not found. Install it and ensure 'iverilog' and "
                "'vvp' are on your PATH, or pass iverilog_path/vvp_path."
            )

    # ── Icarus side ──────────────────────────────────────────────────

    def _run_icarus_src(self, tmpdir: str) -> str:
        """Run Icarus on single-source Verilog, return VCD text."""
        src_path = os.path.join(tmpdir, "test.v")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(self._verilog_src)  # type: ignore[arg-type]
        return self._compile_and_run([src_path], tmpdir)

    def _run_icarus_files(self, tmpdir: str) -> str:
        """Run Icarus on multi-file project, return VCD text."""
        return self._compile_and_run(self._files, tmpdir)  # type: ignore[arg-type]

    def _compile_and_run(self, src_files: list[str], tmpdir: str) -> str:
        """Compile and run through Icarus, return VCD text."""
        out_path = os.path.join(tmpdir, "sim.vvp")
        vcd_path = os.path.join(tmpdir, "dump.vcd")

        # Also check for test.vcd (single-file mode uses $dumpfile("test.vcd"))
        cmd = [self._iverilog, "-o", out_path, "-g2005"]  # type: ignore[list-item]
        cmd.extend(self._iverilog_flags)

        for name, value in self._defines.items():
            if value:
                cmd.append(f"-D{name}={value}")
            else:
                cmd.append(f"-D{name}")

        cmd.extend(src_files)

        result = subprocess.run(  # noqa: S603
            cmd,  # type: ignore[arg-type]
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmpdir,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"iverilog compilation failed:\n{result.stderr}")

        # Determine work_dir for vvp: prefer explicit, then first file's dir
        cwd = tmpdir
        if self._work_dir:
            cwd = str(Path(self._work_dir).resolve())
        elif self._files:
            cwd = str(Path(self._files[0]).resolve().parent)

        result = subprocess.run(  # noqa: S603
            [self._vvp, out_path],  # type: ignore[list-item]
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd,
            check=False,
        )

        # Find VCD file — may be dump.vcd or test.vcd depending on $dumpfile
        for candidate in [
            vcd_path,
            os.path.join(tmpdir, "test.vcd"),
            os.path.join(cwd, "dump.vcd"),
            os.path.join(cwd, "test.vcd"),
        ]:
            if os.path.isfile(candidate):
                with open(candidate) as f:
                    return f.read()

        raise RuntimeError(f"Icarus did not produce a VCD file.\nstdout: {result.stdout}\nstderr: {result.stderr}")

    def run_icarus(self) -> str:
        """Run Icarus Verilog and return VCD text.

        This is useful if you want to run Icarus separately and inspect
        the VCD, or feed it to :func:`compare_vcd` yourself.

        Returns:
            VCD file content as a string.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy supporting files (firmware.hex etc.) into tmpdir
            self._copy_support_files(tmpdir)

            if self._verilog_src is not None:
                return self._run_icarus_src(tmpdir)
            else:
                return self._run_icarus_files(tmpdir)

    def _copy_support_files(self, tmpdir: str) -> None:
        """Copy supporting files (firmware.hex etc.) to temp dir."""
        if self._work_dir:
            work = Path(self._work_dir).resolve()
        elif self._files:
            work = Path(self._files[0]).resolve().parent
        else:
            return

        # Copy common support files
        for pat in ["*.hex", "*.mem", "*.dat"]:
            for f in work.glob(pat):
                dst = Path(tmpdir) / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)

    # ── Our simulator side ───────────────────────────────────────────

    def _build_simulator(self, engine: str, *, override_files: list[str] | None = None) -> "Simulator":
        """Parse and elaborate the design, return a Simulator."""
        from ..project import parse_files  # noqa: PLC0415
        from .testbench import Simulator  # noqa: PLC0415

        if self._verilog_src is not None:
            from ..transforms import tree_to_design  # noqa: PLC0415
            from ..verilog_parser import verilog_parser  # noqa: PLC0415

            parser = verilog_parser(start="source_text")
            tree = parser.build_tree(self._verilog_src)
            design = tree_to_design(tree)
            if not design.modules:
                raise RuntimeError("No modules found in Verilog source")
            module = design.modules[0]
            return Simulator(module, engine=engine)
        else:
            files = override_files or self._files
            design = parse_files(
                files,  # type: ignore[arg-type]
                preprocess=True,
                defines=self._defines if self._defines else None,
            )
            top_name = self._top_module
            if top_name is None:
                raise ValueError("top_module is required for multi-file designs")
            module = design.get_module(top_name)  # type: ignore[assignment]
            if module is None:
                raise ValueError(f"module {top_name!r} not found in design")
            return Simulator(module, engine=engine, design=design)

    # ── VCD-based comparison ─────────────────────────────────────────

    def run(
        self,
        *,
        engine: str = "reference",
        max_time: int = 1000,
        signals: list[str] | None = None,
        ignore_signals: set[str] | None = None,
        verbose: bool = False,
    ) -> CosimResult:
        """Run both simulators and compare VCD output.

        This uses the existing :func:`compare_vcd` infrastructure to
        compare all signal values at every timestamp.

        Args:
            engine: Our simulation engine (``"reference"``, ``"vm"``,
                ``"compiled"``).
            max_time: Maximum simulation time.
            signals: Compare only these signals (by name). ``None`` means
                compare all common signals.
            ignore_signals: Skip these signal names.
            verbose: Print progress messages.

        Returns:
            :class:`CosimResult` with differences and statistics.
        """
        if verbose:
            print("Running Icarus Verilog...", flush=True)
        icarus_vcd_text = self.run_icarus()

        if verbose:
            print("Running our simulator...", flush=True)

        # Change to work_dir if specified (for $readmemh etc.)
        old_cwd = os.getcwd()
        if self._work_dir:
            os.chdir(self._work_dir)
        elif self._files:
            os.chdir(Path(self._files[0]).resolve().parent)

        try:
            sim = self._build_simulator(engine)
            our_vcd_text = record_vcd(sim, max_time=max_time)
        finally:
            os.chdir(old_cwd)

        if verbose:
            print("Comparing VCD output...", flush=True)

        icarus_vcd = parse_vcd(icarus_vcd_text, strip_hierarchy=True)
        our_vcd = parse_vcd(our_vcd_text, strip_hierarchy=True)

        diffs = compare_vcd(
            icarus_vcd,
            our_vcd,
            signals=signals,
            ignore_signals=ignore_signals,
            max_time=max_time,
        )

        return CosimResult(
            diffs=diffs,
            icarus_signal_count=len(icarus_vcd.signal_names),
            ref_signal_count=len(our_vcd.signal_names),
            compared_signal_count=len(icarus_vcd.signal_names & our_vcd.signal_names),
            icarus_vcd=icarus_vcd_text,
        )

    def run_all_engines(
        self,
        *,
        engines: list[str] | None = None,
        max_time: int = 1000,
        signals: list[str] | None = None,
        ignore_signals: set[str] | None = None,
    ) -> dict[str, CosimResult]:
        """Run all available engines and compare each against Icarus VCD.

        Icarus is run once; each engine is compared against that single
        reference VCD.  Engines that are unavailable or raise
        :class:`NotImplementedError` are silently skipped.

        Args:
            engines: Engine names to run.  Defaults to
                :func:`available_engines` (reference, vm, vm-fast, and
                compiled when the toolchain is present).
            max_time: Maximum simulation time passed to each engine.
            signals: Compare only these signals.  ``None`` = all common.
            ignore_signals: Signal names to skip.

        Returns:
            ``dict`` mapping engine name → :class:`CosimResult`.
            Only engines that were actually attempted appear as keys.
        """
        if engines is None:
            engines = available_engines()

        icarus_vcd_text = self.run_icarus()
        icarus_vcd = parse_vcd(icarus_vcd_text, strip_hierarchy=True)

        old_cwd = os.getcwd()
        if self._work_dir:
            os.chdir(self._work_dir)
        elif self._files:
            os.chdir(Path(self._files[0]).resolve().parent)

        results: dict[str, CosimResult] = {}
        try:
            for engine in engines:
                try:
                    sim = self._build_simulator(engine)
                    our_vcd_text = record_vcd(sim, max_time=max_time)
                    our_vcd = parse_vcd(our_vcd_text, strip_hierarchy=True)
                    diffs = compare_vcd(
                        icarus_vcd,
                        our_vcd,
                        signals=signals,
                        ignore_signals=ignore_signals,
                        max_time=max_time,
                    )
                    results[engine] = CosimResult(
                        diffs=[f"[{engine}] {d}" for d in diffs],
                        icarus_signal_count=len(icarus_vcd.signal_names),
                        ref_signal_count=len(our_vcd.signal_names),
                        compared_signal_count=len(icarus_vcd.signal_names & our_vcd.signal_names),
                        icarus_vcd=icarus_vcd_text,
                    )
                except NotImplementedError as exc:
                    log.info("Engine %r skipped: %s", engine, exc)
        finally:
            os.chdir(old_cwd)

        return results

    # ── Cycle-by-cycle comparison ────────────────────────────────────

    def run_cycle_by_cycle(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        *,
        engine: str = "reference",
        max_cycles: int = 300,
        reset_cycles: int = 10,
        clock_name: str = "clk",
        reset_name: str = "rst",
        clock_period: int = 10,
        ignore_signals: set[str] | None = None,
        ignore_x: bool = True,
        verbose: bool = False,
        sim_files: list[str] | None = None,
        reset_active_high: bool = True,
    ) -> CycleMismatch | None:
        """Step both simulators cycle-by-cycle and find the first mismatch.

        This provides finer-grained feedback than :meth:`run` — it shows
        which cycle and which signals first diverge, making it easy to
        trace backward to the root cause.

        The Icarus side is run in advance (full simulation → VCD → parse
        snapshots at posedge). Our simulator is stepped one cycle at a
        time, and signal values are compared after each posedge.

        Args:
            engine: Our simulation engine.
            max_cycles: Total clock cycles (including reset).
            reset_cycles: How many cycles to keep reset asserted.
            clock_name: Name of the clock signal in the testbench.
            reset_name: Name of the reset signal.
            reset_active_high: If True (default), reset is driven high
                during reset and released low. If False, reset is driven
                low during reset and released high (active-low reset).
            clock_period: Clock period in time units.
            ignore_signals: Signal names to skip.
            ignore_x: If True, skip signals that have X bits in either
                simulator (avoids false positives from uninitialized state).
            verbose: Print progress messages.
            sim_files: Alternative file list for our simulator (e.g. for
                using an externally-clocked testbench while Icarus uses a
                self-clocking one). If ``None``, uses the same files.

        Returns:
            :class:`CycleMismatch` describing the first divergent cycle,
            or ``None`` if all cycles match.
        """
        ignore = ignore_signals or set()
        ignore.add(clock_name)  # Clock is driven differently; skip it
        half = clock_period // 2

        # Step 1: Run Icarus, get VCD, extract posedge snapshots
        if verbose:
            print("Running Icarus...", flush=True)
        icarus_vcd_text = self.run_icarus()
        icarus_vcd = _parse_vcd_full(icarus_vcd_text)

        # Find the clock in VCD (with testbench prefix)
        vcd_clk = None
        for name in icarus_vcd.all_names:
            norm = _strip_top(name)
            if norm == clock_name:
                vcd_clk = name
                break
        if vcd_clk is None:
            raise RuntimeError(
                f"Clock signal '{clock_name}' not found in Icarus VCD. Available: {sorted(icarus_vcd.all_names)[:20]}"
            )

        icarus_snapshots = _posedge_snapshots(icarus_vcd, vcd_clk)
        if verbose:
            print(f"  {len(icarus_snapshots)} posedge snapshots from Icarus", flush=True)

        # Step 2: Build and step our simulator
        if verbose:
            print("Building our simulator...", flush=True)

        old_cwd = os.getcwd()
        if self._work_dir:
            os.chdir(self._work_dir)
        elif self._files:
            os.chdir(Path(self._files[0]).resolve().parent)

        try:
            sim = self._build_simulator(engine, override_files=sim_files)
            sim.run(max_time=0)
            all_signals = sorted(sim.signals())

            # Build name mapping: VCD name → our name
            name_map: dict[str, str] = {}
            our_signal_set = set(all_signals)
            for vcd_name in icarus_vcd.all_names:
                our_name = _strip_top(vcd_name)
                if our_name in our_signal_set:
                    name_map[vcd_name] = our_name

            compare_names = {
                vcd: ours for vcd, ours in name_map.items() if ours not in ignore and "memory[" not in ours
            }

            if verbose:
                print(f"  {len(compare_names)} signals to compare", flush=True)

            min_cycles = min(max_cycles, len(icarus_snapshots))

            for cyc in range(min_cycles):
                if cyc == reset_cycles:
                    sim.drive(reset_name, 0 if reset_active_high else 1)

                sim.drive(clock_name, 0)
                sim.run(max_time=sim.time + half)
                sim.drive(clock_name, 1)
                sim.run(max_time=sim.time + half)

                # Compare
                ic_snap = icarus_snapshots[cyc]
                mismatches = []

                for vcd_name, our_name in compare_names.items():
                    # Get Icarus value
                    ic_val = ic_snap.get(vcd_name)
                    if ic_val is None:
                        continue

                    # Get our value
                    try:
                        val = sim.read(our_name)
                    except Exception:  # noqa: S112
                        continue

                    our_str = _value_to_binstr(val)

                    if ignore_x and ("x" in ic_val.lower() or "x" in our_str.lower()):
                        continue

                    if not _bin_match(ic_val, our_str):
                        mismatches.append((our_name, ic_val, our_str))

                if mismatches and cyc > reset_cycles:
                    if verbose:
                        print(f"  Mismatch at cycle {cyc}:", flush=True)
                        for name, ic, ours in sorted(mismatches)[:10]:
                            print(f"    {name}: icarus={ic} ref={ours}")
                    return CycleMismatch(cycle=cyc, signals=sorted(mismatches))

                if verbose and (cyc + 1) % 50 == 0:
                    print(f"  Cycle {cyc + 1}/{min_cycles} OK", flush=True)

        finally:
            os.chdir(old_cwd)

        if verbose:
            print(f"  All {min_cycles} cycles match!", flush=True)
        return None


# ── Internal VCD helpers ─────────────────────────────────────────────


@dataclass
class _FullVcd:
    """Parsed VCD with full hierarchical names preserved."""

    all_names: set[str] = field(default_factory=set)
    id_to_names: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    time_values: dict[int, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))


def _parse_vcd_full(text: str) -> _FullVcd:  # noqa: PLR0912
    """Parse VCD preserving full hierarchical names.

    Unlike :func:`parse_vcd` which strips hierarchy, this keeps the
    full scope path for each signal so we can map between Icarus
    (``testbench.uut.cpu.state.ibus_cyc``) and our naming convention
    (``uut.cpu.state.ibus_cyc``).
    """
    data = _FullVcd()
    scope_stack: list[str] = []
    current_time = 0
    in_defs = True

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        if in_defs:
            if line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:  # noqa: PLR2004
                    scope_stack.append(parts[2])
            elif line.startswith("$upscope"):
                if scope_stack:
                    scope_stack.pop()
            elif line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:  # noqa: PLR2004
                    vcd_id = parts[3]
                    name = re.sub(r"\[.*\]$", "", parts[4])
                    hier_name = ".".join([*scope_stack, name])
                    data.id_to_names[vcd_id].append(hier_name)
                    data.all_names.add(hier_name)
            elif line.startswith("$enddefinitions"):
                in_defs = False
        elif line.startswith("#"):
            m = re.match(r"#(\d+)", line)
            if m:
                current_time = int(m.group(1))
        elif line.startswith(("b", "B")):
            space = line.index(" ")
            bval = line[1:space]
            vid = line[space + 1 :]
            if vid in data.id_to_names:
                for hname in data.id_to_names[vid]:
                    data.time_values[current_time][hname] = bval
        elif len(line) >= 2 and line[0] in "01xXzZ":  # noqa: PLR2004
            val = line[0]
            vid = line[1:]
            if vid in data.id_to_names:
                for hname in data.id_to_names[vid]:
                    data.time_values[current_time][hname] = val

    return data


def _posedge_snapshots(vcd: _FullVcd, clk_name: str) -> list[dict[str, str]]:
    """Extract signal snapshots at each posedge of clk."""
    sorted_times = sorted(vcd.time_values.keys())
    current: dict[str, str] = {}
    snapshots: list[dict[str, str]] = []
    prev_clk = None

    for t in sorted_times:
        current.update(vcd.time_values[t])
        clk_val = current.get(clk_name, "x")
        if clk_val == "1" and prev_clk == "0":
            snapshots.append(dict(current))
        prev_clk = clk_val

    return snapshots


def _strip_top(name: str) -> str:
    """Strip the top-level module prefix from a VCD hierarchical name.

    ``'testbench.uut.cpu.state.ibus_cyc'`` → ``'uut.cpu.state.ibus_cyc'``
    """
    dot = name.find(".")
    if dot >= 0:
        return name[dot + 1 :]
    return name


def _value_to_binstr(val) -> str:
    """Convert a simulator Value to a binary string for comparison."""
    width = val.width if hasattr(val, "width") else 1
    mask = val.mask if hasattr(val, "mask") else 0
    ival = val.val if hasattr(val, "val") else int(val)

    if mask != 0:
        bits = []
        for b in range(width - 1, -1, -1):
            if (mask >> b) & 1:
                bits.append("x")
            elif (ival >> b) & 1:
                bits.append("1")
            else:
                bits.append("0")
        return "".join(bits)

    if width == 1:
        return "1" if ival else "0"
    return format(ival, f"0{width}b")


def _bin_match(a: str, b: str) -> bool:
    """Compare two binary value strings, normalizing leading zeros."""
    a = a.lower().strip().lstrip("0") or "0"
    b = b.lower().strip().lstrip("0") or "0"
    return a == b
