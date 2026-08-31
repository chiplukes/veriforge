"""Long-running fuzz loop: generate → simulate → compare → log.

The runner generates random Verilog modules, simulates them with all
available veriforge engines (reference, vm, vm-fast, compiled), compares
results bit-for-bit, cross-checks against Icarus Verilog as an external
oracle, and logs any mismatches for later reduction into dedicated tests.
"""

from __future__ import annotations

import datetime
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import signal

from ..model.design import Module
from ..model.expressions import StreamingConcatenation
from ..sim.testbench import Simulator
from ..sim.value import Value
from ..codegen.verilog_emitter import emit_design, emit_expression

from ._module_gen import ModuleGenerator, Strategy
from ._signal_context import SignalContext


# Default bias toward simpler strategies for faster iteration.
_DEFAULT_STRATEGY_WEIGHTS: dict[Strategy, float] = {
    Strategy.FEEDFORWARD: 3.0,
    Strategy.REGISTERED: 2.0,
    Strategy.MULTI_ALWAYS: 1.5,
    Strategy.CLOCKED_SEQUENTIAL: 1.0,
    Strategy.MIXED: 1.5,
}


@dataclass
class FuzzResult:
    """Result of a single fuzz run."""

    seed: int
    module: Module
    strategy: str
    verilog_source: str
    vectors: list[dict[str, Value]]
    engine_results: dict[str, list[dict[str, Value]]]  # engine → vector_idx → {signal: Value}
    mismatches: list[str]  # human-readable mismatch descriptions


class FuzzRunner:
    """Main fuzz loop.

    Parameters
    ----------
    output_dir:
        Directory for storing mismatch artifacts and stats.
    seed:
        Starting seed. Incremented after each module.
    engines:
        Veriforge engines to test. Default: all available.
    icarus:
        Whether to cross-check with Icarus Verilog (requires iverilog in PATH).
    verilator:
        Whether to cross-check with Verilator (requires verilator in PATH).
        Off by default: Verilator is ~8-10x slower per module than Icarus
        (compile+link+run vs. a plain interpreted `vvp` run), so it's opt-in
        like the "compiled" engine rather than on-by-default like Icarus.
        Verilator is 2-state only (no `x`/`z`), so its comparison is
        value-only and restricted to vectors/signals the reference engine
        itself reports as fully defined -- see `_compare_verilator`.
    """

    _engine_names = ("reference", "vm", "vm-fast", "compiled")

    def __init__(
        self,
        output_dir: str | Path = "fuzz_output",
        seed: int = 0,
        *,
        engines: Sequence[str] | None = None,
        icarus: bool = True,
        verilator: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self._icarus = icarus and bool(shutil.which("iverilog"))
        if icarus and not self._icarus:
            print("[fuzz] iverilog not found — Icarus cross-check disabled")

        self._verilator = verilator and bool(shutil.which("verilator"))
        if verilator and not self._verilator:
            print("[fuzz] verilator not found — Verilator cross-check disabled")

        self._engines = list(engines) if engines else self._detect_engines()
        print(f"[fuzz] engines: {', '.join(self._engines)}")
        if self._icarus:
            print("[fuzz] Icarus cross-check enabled")
        if self._verilator:
            print("[fuzz] Verilator cross-check enabled")

        # Running counters
        self.total_modules = 0
        self.total_mismatches = 0
        self.mismatches_by_engine: dict[str, int] = {}
        self.icarus_artifacts_filtered = 0
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # Engine detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_engines() -> list[str]:
        """Return all veriforge engines available in this environment."""
        engines = ["reference", "vm"]
        try:
            import Cython  # noqa: F401

            engines.append("vm-fast")
        except ImportError:
            pass
        if os.environ.get("VERIFORGE_DIFF_COMPILED") == "1":
            engines.append("compiled")
        return engines

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, *, max_modules: int | None = None, max_hours: float | None = None) -> None:
        """Run the fuzz loop.

        Args:
            max_modules: Stop after this many modules (None = run forever).
            max_hours: Stop after this many hours (None = run forever).
        """
        deadline = (time.time() + max_hours * 3600) if max_hours is not None else None
        count = 0

        while True:
            if max_modules is not None and count >= max_modules:
                break
            if deadline is not None and time.time() >= deadline:
                break

            count += 1
            self._run_one()
            self.seed += 1

            # Periodic stats
            if count % 10 == 0:
                elapsed = time.time() - self._start_time
                rate = self.total_modules / max(elapsed, 0.1)
                print(f"[fuzz] modules={self.total_modules} mismatches={self.total_mismatches} rate={rate:.1f}/s")

        self._save_stats()

    def _run_one(self) -> None:
        """Generate, simulate, and cross-check one module."""
        rng = random.Random(self.seed)  # noqa: S311
        gen = ModuleGenerator(rng)

        design = gen.generate_design()
        top = design.get_module("t")
        assert top is not None, "generator always names the top module 't'"
        verilog = emit_design(design)

        self.total_modules += 1

        vectors = self._gen_stimulus(top, rng)

        result = FuzzResult(
            seed=self.seed,
            module=top,
            strategy="",  # filled by ModuleGenerator
            verilog_source=verilog,
            vectors=vectors,
            engine_results={},
            mismatches=[],
        )

        # Run veriforge engines
        oracle = None
        for engine in self._engines:
            try:
                res = self._simulate(verilog, engine, vectors)
                result.engine_results[engine] = res
                if engine == "reference":
                    oracle = res
            except Exception as exc:
                result.mismatches.append(f"engine {engine} raised: {exc}")
                continue

        if oracle is None:
            self._log_mismatch(result)
            return

        # Compare non-reference engines to reference
        for engine in self._engines:
            if engine == "reference":
                continue
            if engine not in result.engine_results:
                continue
            diffs = self._compare(oracle, result.engine_results[engine], engine, vectors)
            if diffs:
                result.mismatches.extend(diffs)
                self.mismatches_by_engine[engine] = self.mismatches_by_engine.get(engine, 0) + 1

        # Icarus cross-check -- skipped for a design containing a genuine
        # `{<<{...}}` streaming concatenation ANYWHERE (top module or, for
        # the hierarchical strategy, the child module too -- both are
        # generated by the same expression machinery): Icarus Verilog has
        # no support for the construct at all ("sorry: Streaming
        # concatenation not supported", confirmed directly), so treating a
        # resulting compile failure as a mismatch would just flood
        # mismatch_NNNNN/ with false positives on every such module. `>>`
        # streaming concat is unaffected since it desugars to plain
        # Concatenation before it ever reaches here (see
        # StreamingConcatenation's own docstring) -- only the `<<` node
        # itself needs this carve-out, and cross-engine comparison
        # (reference vs vm/vm-fast/compiled, still run above) remains fully
        # active for it regardless.
        has_streaming_concat = any(any(m.find(StreamingConcatenation)) for m in design.modules)
        if self._icarus and not has_streaming_concat:
            try:
                icarus_res = self._simulate_icarus(verilog, vectors, top)
                diffs = self._compare(oracle, icarus_res, "iverilog", vectors)
                if diffs:
                    result.mismatches.extend(diffs)
                    self.mismatches_by_engine["iverilog"] = self.mismatches_by_engine.get("iverilog", 0) + 1
            except Exception as exc:
                result.mismatches.append(f"icarus failed: {exc}")

        # Verilator cross-check -- same testbench Icarus uses (confirmed by
        # direct testing that it runs correctly under Verilator too), but
        # compared with `_compare_verilator` rather than `_compare`: since
        # Verilator has no `x`/`z` state, only vectors/signals the reference
        # engine itself reports as fully defined (mask=0) are meaningful to
        # check against it (see `_compare_verilator`'s docstring).
        #
        # ALSO skipped for `has_streaming_concat`, for a different reason
        # than Icarus (which rejects the construct outright): Verilator
        # accepts `{<<n{...}}` and agrees with veriforge whenever the
        # combined operand width is an exact multiple of the slice size, but
        # gives a genuinely different (LRM-nonconformant) result whenever it
        # isn't -- confirmed directly and independently of both simulators:
        # `{<<3{8'b11010010}}` hand-derived from IEEE 1800-2017 SS11.4.14.1
        # (chunk from the MSB, incomplete chunk lands at the LSB end, then
        # reverse chunk order) gives `10100110`, matching veriforge
        # (reference/vm/vm-fast all agree) exactly; Verilator instead gives
        # `01001011` -- the result of chunking from the LSB end instead (so
        # the incomplete chunk lands at the MSB end pre-reversal). The
        # evenly-divisible case (`{<<4{...}}` on the same 8-bit operand)
        # gives identical results in both, isolating the gap to specifically
        # the ragged/incomplete-final-chunk case. Since fuzzed slice sizes
        # rarely divide the fuzzed operand width evenly, this needs the same
        # coarse whole-module skip Icarus already gets rather than a
        # per-expression static-width check.
        if self._verilator and not has_streaming_concat:
            try:
                verilator_res = self._simulate_verilator(verilog, vectors, top)
                diffs = self._compare_verilator(oracle, verilator_res, vectors)
                if diffs:
                    result.mismatches.extend(diffs)
                    self.mismatches_by_engine["verilator"] = self.mismatches_by_engine.get("verilator", 0) + 1
            except Exception as exc:
                result.mismatches.append(f"verilator failed: {exc}")

        if result.mismatches:
            self.total_mismatches += 1
            self._log_mismatch(result)

    # ------------------------------------------------------------------
    # Stimulus
    # ------------------------------------------------------------------

    def _gen_stimulus(self, mod: Module, rng: random.Random) -> list[dict[str, Value]]:
        """Generate random stimulus vectors for the module's inputs."""
        n_vecs = min(rng.randint(4, 12), 16)
        vectors: list[dict[str, Value]] = []
        for _ in range(n_vecs):
            vec: dict[str, Value] = {}
            for port in mod.input_ports():
                width = self._port_width(port)
                val = rng.getrandbits(width) if width > 0 else 0
                # 1 in 6 chance of x-contamination on a random input
                mask = 0
                if rng.random() < 0.15:
                    x_signal_width = width
                    mask = rng.getrandbits(x_signal_width) if x_signal_width > 0 else 1
                    mask = mask & ((1 << x_signal_width) - 1)
                vec[port.name] = Value(val, width=max(width, 1), mask=mask)
            vectors.append(vec)
        return vectors

    @staticmethod
    def _port_width(port) -> int:
        """Return the bit width of a port signal."""
        if port.width is None:
            return 1
        # width is a Range(msb, lsb) where msb and lsb are Expression
        try:
            msb = _expr_to_int(port.width.msb)
            lsb = _expr_to_int(port.width.lsb)
            return abs(msb - lsb) + 1
        except (AttributeError, ValueError):
            return 1

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def _simulate(
        self,
        source: str,
        engine: str,
        vectors: list[dict[str, Value]],
    ) -> list[dict[str, Value]]:
        """Run the veriforge simulator for *engine* with *vectors*."""
        from ..analysis.resolver import link_instances, resolve_port_connections
        from ..transforms.tree_to_model import tree_to_design
        from ..verilog_parser import verilog_parser

        vp = verilog_parser(start="source_text")
        tree = vp.build_tree(source)
        design = tree_to_design(tree, source_file="fuzz.v")
        link_instances(design)
        resolve_port_connections(design)
        top = next(m for m in design.modules if m.name == "t")
        sim = Simulator(top, engine=engine, design=design)

        # Drive initial values for all inputs
        for v in vectors[0]:
            sim.drive(v, Value(0, width=max(vectors[0][v].width, 1)))

        has_clock = any(p.name == "clk" for p in top.ports)
        if has_clock:
            sim.drive("clk", Value(0, width=1))

        sim.settle()
        results: list[dict[str, Value]] = []

        for vec in vectors:
            for name, value in vec.items():
                sim.drive(name, value)

            if has_clock:
                sim.settle()
                sim.drive("clk", Value(1, width=1))
                sim.settle()
                sim.drive("clk", Value(0, width=1))

            sim.settle()
            outputs = {p.name: sim.read(p.name) for p in top.output_ports()}
            results.append(outputs)

        return results

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def _compare(
        self,
        oracle: list[dict[str, Value]],
        got: list[dict[str, Value]],
        engine: str,
        vectors: list[dict[str, Value]],
    ) -> list[str]:
        """Compare *got* values against *oracle* values.

        Returns a list of mismatch descriptions (human-readable).
        """
        diffs: list[str] = []
        # Mismatches are grouped per-signal (across all vectors) before being
        # formatted so the iverilog-only artifact filter below can look at a
        # whole signal's mismatch history at once, not just one vector.
        per_signal: dict[str, list[tuple[int, Value, Value]]] = {}

        for vi, (exp_vec, got_vec) in enumerate(zip(oracle, got, strict=True)):
            if len(exp_vec) != len(got_vec):
                diffs.append(
                    f"[{engine}] vector {vi}: output count mismatch expected={len(exp_vec)} got={len(got_vec)}"
                )
                continue
            for sig in sorted(exp_vec):
                exp = exp_vec.get(sig)
                got_val = got_vec.get(sig)
                if exp is None or got_val is None:
                    diffs.append(f"[{engine}] vector {vi} signal {sig}: missing in one result")
                    continue
                if exp != got_val:
                    per_signal.setdefault(sig, []).append((vi, exp, got_val))

        for sig, sig_mismatches in per_signal.items():
            if engine == "iverilog" and _is_icarus_first_activation_artifact(sig_mismatches):
                self.icarus_artifacts_filtered += len(sig_mismatches)
                continue
            for vi, exp, got_val in sig_mismatches:
                diffs.append(f"[{engine}] vector {vi} signal {sig}: expected={_val_repr(exp)} got={_val_repr(got_val)}")
        return diffs

    # ------------------------------------------------------------------
    # Icarus
    # ------------------------------------------------------------------

    def _simulate_icarus(
        self,
        source: str,
        vectors: list[dict[str, Value]],
        mod: Module,
    ) -> list[dict[str, Value]]:
        """Run Icarus Verilog and return per-vector output values.

        Uses $display at each stimulus step to capture output values,
        then parses stdout of the vvp process.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dut_path = tmp / "dut.v"
            tb_path = tmp / "tb.v"
            vvp_path = tmp / "out.vvp"

            dut_path.write_text(source)
            tb_path.write_text(self._build_testbench(mod, vectors))

            try:
                subprocess.run(
                    ["iverilog", "-g2012", "-o", str(vvp_path), str(dut_path), str(tb_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"iverilog: {exc.stderr.strip()}") from exc

            try:
                result = subprocess.run(
                    ["vvp", str(vvp_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=str(tmp),
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("vvp timed out (likely unbounded simulation loop)") from None
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"vvp: {exc.stderr.strip()}") from exc

            return self._parse_display_output(result.stdout, mod)

    def _build_testbench(self, mod: Module, vectors: list[dict[str, Value]]) -> str:
        """Build a Verilog testbench that drives *vectors* and $display's outputs."""
        lines = ["module tb;"]
        input_names = {p.name for p in mod.input_ports()}
        output_names = {p.name for p in mod.output_ports()}

        for port in mod.ports:
            direction = port.direction.value
            width_str = ""
            if port.width:
                width_str = f"[{emit_expression(port.width.msb)}:{emit_expression(port.width.lsb)}]"
            if port.name in input_names:
                lines.append(f"    reg {width_str} {port.name};")
            elif port.name in output_names:
                lines.append(f"    wire {width_str} {port.name};")

        port_names = ", ".join(p.name for p in mod.ports)
        lines.append(f"    {mod.name} dut ({port_names});")

        # `clk` gets an explicit, clean one-pulse-per-vector toggle below,
        # matching `_simulate`'s handling for our own engines EXACTLY
        # (settle, clk=1, settle, clk=0) -- it must NOT be driven from
        # `vec`'s own (randomly generated, since `_gen_stimulus` treats
        # every input port identically) value here. Before this fix, `clk`
        # was driven like any other input -- a random bit per vector, often
        # not even changing between consecutive vectors -- so Icarus's
        # `always @(posedge clk)` blocks fired on a completely different
        # (effectively uncorrelated) schedule than our own engines', which
        # always get exactly one clean edge per vector regardless of what
        # `vec["clk"]` happened to contain. Confirmed as the dominant cause
        # of value mismatches from this fuzzer: 43 of 46 mismatches in one
        # survey were on clocked modules, and none were a genuine engine
        # disagreement (`reference`/`vm`/`vm-fast` always agreed with each
        # other -- only the Icarus comparison, driven by this diverging
        # clock schedule, disagreed).
        has_clock = "clk" in input_names
        lines.append("    initial begin")
        if has_clock:
            lines.append("        clk = 1'b0;")
        for _vi, vec in enumerate(vectors):
            for name in sorted(input_names):
                if name == "clk":
                    continue
                val_repr = self._value_to_verilog(vec.get(name, Value(0)))
                lines.append(f"        {name} = {val_repr};")
            if has_clock:
                lines.append("        #5;")
                lines.append("        clk = 1'b1;")
                lines.append("        #5;")
                lines.append("        clk = 1'b0;")
                lines.append("        #5;")
            else:
                lines.append("        #10;")
            # $display outputs in a fixed order, space-delimited in format
            # so parsing with split() works; signal args use commas.
            display_parts = ", ".join(sorted(output_names))
            fmt_parts = " ".join("%b" for _ in output_names)
            lines.append(f'        $display("{fmt_parts}", {display_parts});')
        lines.append("        $finish;")
        lines.append("    end")
        lines.append("endmodule")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Verilator
    # ------------------------------------------------------------------

    def _simulate_verilator(
        self,
        source: str,
        vectors: list[dict[str, Value]],
        mod: Module,
    ) -> list[dict[str, Value]]:
        """Run Verilator and return per-vector output values.

        Reuses `_build_testbench` unchanged -- confirmed by direct testing
        that the same `$display`-based testbench Icarus consumes also runs
        correctly under Verilator's `--binary` mode, which compiles AND
        links a self-contained executable in one step (no hand-written C++
        harness needed, unlike a plain `verilator --cc` build).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dut_path = tmp / "dut.v"
            tb_path = tmp / "tb.v"
            obj_dir = tmp / "obj_dir"

            dut_path.write_text(source)
            tb_path.write_text(self._build_testbench(mod, vectors))

            try:
                subprocess.run(
                    [
                        "verilator",
                        "--binary",
                        "-Wno-fatal",
                        "--timing",
                        "-Mdir",
                        str(obj_dir),
                        str(dut_path),
                        str(tb_path),
                        "--top-module",
                        "tb",
                        "-o",
                        "sim_exe",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"verilator: {exc.stderr.strip()}") from exc
            except subprocess.TimeoutExpired:
                raise RuntimeError("verilator compile timed out") from None

            try:
                result = subprocess.run(
                    [str(obj_dir / "sim_exe")],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=str(tmp),
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("verilator sim timed out (likely unbounded simulation loop)") from None
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"verilator sim: {exc.stderr.strip()}") from exc

            # Verilator's own status banners ("- Verilator: ...", "- S i m u
            # l a t i o n   R e p o r t: ...") are always prefixed with
            # "- " -- never true for a %b-formatted line of stimulus output
            # (0/1/x/z and spaces only) -- so they're filtered out before
            # reusing the same fixed-token-count parser Icarus's output uses.
            stdout = "\n".join(line for line in result.stdout.splitlines() if not line.lstrip().startswith("-"))
            return self._parse_display_output(stdout, mod)

    def _compare_verilator(
        self,
        oracle: list[dict[str, Value]],
        got: list[dict[str, Value]],
        vectors: list[dict[str, Value]],
    ) -> list[str]:
        """Compare Verilator's *got* values against the reference *oracle*.

        Verilator is a 2-state simulator (confirmed directly: driving
        `4'bxxxx` into a `logic` net and reading it back gives `0000`, not
        `x` -- see notes/known_issues.md). Its output is therefore only
        meaningful where the reference engine itself reports a signal as
        fully defined (mask=0) -- any `(vector, signal)` pair where the
        oracle shows ambiguity is skipped rather than compared, since
        Verilator's answer for a genuinely undefined case is arbitrary and
        can't be judged right or wrong. This filters per-signal-per-vector,
        not per-vector, so a vector's other, fully-defined outputs still get
        checked even when one output is ambiguous. Only `.val` is compared
        (never `.mask`) for the surviving pairs.
        """
        diffs: list[str] = []
        for vi, (exp_vec, got_vec) in enumerate(zip(oracle, got, strict=True)):
            for sig in sorted(exp_vec):
                exp = exp_vec.get(sig)
                got_val = got_vec.get(sig)
                if exp is None or got_val is None:
                    diffs.append(f"[verilator] vector {vi} signal {sig}: missing in one result")
                    continue
                if exp.mask:
                    continue  # oracle itself ambiguous here -- not comparable
                if exp.val != got_val.val:
                    diffs.append(
                        f"[verilator] vector {vi} signal {sig}: expected={_val_repr(exp)} got={_val_repr(got_val)}"
                    )
        return diffs

    def _parse_display_output(self, stdout: str, mod: Module) -> list[dict[str, Value]]:
        """Parse $display output lines into per-vector Value dicts."""
        output_names = sorted(p.name for p in mod.output_ports())
        results: list[dict[str, Value]] = []

        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != len(output_names):
                continue

            vec: dict[str, Value] = {}
            for name, bits_str in zip(output_names, parts):
                val = 0
                mask = 0
                for ch in bits_str:
                    val <<= 1
                    mask <<= 1
                    if ch == "1":
                        val |= 1
                    elif ch in ("x", "z"):
                        mask |= 1
                vec[name] = Value(val, width=len(bits_str), mask=mask)
            results.append(vec)

        return results

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_mismatch(self, result: FuzzResult) -> None:
        """Save mismatch artifact to disk."""
        case_dir = self.output_dir / f"mismatch_{result.seed:05d}"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "module.v").write_text(result.verilog_source)
        (case_dir / "mismatches.txt").write_text("\n".join(result.mismatches))

        info = {
            "seed": result.seed,
            "strategy": result.strategy,
            "timestamp": datetime.datetime.now().isoformat(),
            "engines_tested": list(result.engine_results.keys()),
        }
        (case_dir / "info.json").write_text(json.dumps(info, indent=2))

        print(f"[fuzz] mismatch logged: {case_dir}")
        for m in result.mismatches[:3]:
            print(f"  {m}")
        if len(result.mismatches) > 3:
            print(f"  ... and {len(result.mismatches) - 3} more")

    def _save_stats(self) -> None:
        elapsed = time.time() - self._start_time
        stats = {
            "total_modules": self.total_modules,
            "total_mismatches": self.total_mismatches,
            "mismatches_by_engine": self.mismatches_by_engine,
            "icarus_artifacts_filtered": self.icarus_artifacts_filtered,
            "elapsed_seconds": elapsed,
            "rate": self.total_modules / max(elapsed, 0.1),
            "engines": self._engines,
            "icarus_enabled": self._icarus,
            "verilator_enabled": self._verilator,
            "final_seed": self.seed,
        }
        (self.output_dir / "stats.json").write_text(json.dumps(stats, indent=2))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _value_to_verilog(value: Value | None) -> str:
        """Convert a Value to a Verilog literal string."""
        if value is None:
            return "0"
        w = max(value.width, 1)
        if value.mask:
            bits = []
            for i in range(w - 1, -1, -1):
                if (value.mask >> i) & 1:
                    # `Value.__init__` always zeroes `val` wherever `mask` is
                    # set (`val & ~mask`), so a val-bit-dependent x-vs-z
                    # choice here can never actually select 'x' -- this
                    # simulator's own Value model has no z state distinct
                    # from x (see notes/known_issues.md, "x and z share one
                    # representation"), so a masked bit always means x and
                    # must always be emitted as 'x', not 'z' (Icarus DOES
                    # distinguish the two, so emitting 'z' drives a
                    # different, genuinely tri-state scenario than intended).
                    bits.append("x")
                else:
                    bits.append("1" if (value.val >> i) & 1 else "0")
            return f"{w}'b{''.join(bits)}"
        return f"{w}'d{value.val}"


def _is_icarus_first_activation_artifact(mismatches: list[tuple[int, Value, Value]]) -> bool:
    """Recognize Icarus's own first-activation x-extension quirk.

    See notes/known_issues.md ("Icarus first-activation x-extension
    artifact"): a combinational always block's very first evaluation of an
    ambiguous self-determined-width RHS (comparison/reduction/!/&&/||)
    extended into a wider destination sometimes gives fully-x in Icarus,
    while every later re-evaluation of the identical block (and every
    continuous-assign equivalent) deterministically zero-extends instead --
    confirmed, via minimal isolated repro directly against `iverilog`, to be
    an Icarus-specific artifact rather than a genuine Verilog semantic.

    This recognizes that exact signature for one signal's full mismatch
    history across a fuzz run's vectors: Icarus (`got`) reports the signal
    as FULLY ambiguous every time, our engine (`exp`) reports it as only
    PARTIALLY ambiguous (a strict subset of Icarus's x bits) every time, and
    -- critically -- our engine's value is IDENTICAL across every
    mismatching vector. That last condition is the safety net: it only ever
    holds for a signal driven by something genuinely never externally
    driven (which can't change after the block's first activation); a real
    x-precision regression reacts to the vectors' actually-changing input
    values and would not hold constant like this.
    """
    if not mismatches:
        return False
    _, first_exp, _ = mismatches[0]
    width = first_exp.width
    full_mask = (1 << width) - 1 if width > 0 else 0
    for _vi, exp, got_val in mismatches:
        if exp.width != width or got_val.width != width:
            return False
        if got_val.mask != full_mask:
            return False
        if exp.mask == full_mask:
            return False
        if (exp.val, exp.mask) != (first_exp.val, first_exp.mask):
            return False
    return True


def _expr_to_int(expr) -> int:
    """Try to extract an integer from an Expression."""
    from ..model.expressions import Literal

    if isinstance(expr, Literal):
        v = expr.value
        if isinstance(v, int):
            return v
    return 1


def _val_repr(v: Value) -> str:
    """Compact Value repr for mismatch messages."""
    w = max(v.width, 1)
    if w <= 16:
        return f"0b{v.val:0{w}b}" + (f" x={v.mask:0{w}b}" if v.mask else "")
    return f"<{w}b> val={v.val:#x} mask={v.mask:#x}"
