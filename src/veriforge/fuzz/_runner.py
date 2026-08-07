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
from ..sim.testbench import Simulator
from ..sim.value import Value
from ..codegen.verilog_emitter import emit_module, emit_expression

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
    """

    _engine_names = ("reference", "vm", "vm-fast", "compiled")

    def __init__(
        self,
        output_dir: str | Path = "fuzz_output",
        seed: int = 0,
        *,
        engines: Sequence[str] | None = None,
        icarus: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self._icarus = icarus and bool(shutil.which("iverilog"))
        if icarus and not self._icarus:
            print("[fuzz] iverilog not found — Icarus cross-check disabled")

        self._engines = list(engines) if engines else self._detect_engines()
        print(f"[fuzz] engines: {', '.join(self._engines)}")
        if self._icarus:
            print("[fuzz] Icarus cross-check enabled")

        # Running counters
        self.total_modules = 0
        self.total_mismatches = 0
        self.mismatches_by_engine: dict[str, int] = {}
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

        mod = gen.generate()
        verilog = emit_module(mod)

        self.total_modules += 1

        vectors = self._gen_stimulus(mod, rng)

        result = FuzzResult(
            seed=self.seed,
            module=mod,
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

        # Icarus cross-check
        if self._icarus:
            try:
                icarus_res = self._simulate_icarus(verilog, vectors, mod)
                diffs = self._compare(oracle, icarus_res, "iverilog", vectors)
                if diffs:
                    result.mismatches.extend(diffs)
                    self.mismatches_by_engine["iverilog"] = self.mismatches_by_engine.get("iverilog", 0) + 1
            except Exception as exc:
                result.mismatches.append(f"icarus failed: {exc}")

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

    @staticmethod
    def _compare(
        oracle: list[dict[str, Value]],
        got: list[dict[str, Value]],
        engine: str,
        vectors: list[dict[str, Value]],
    ) -> list[str]:
        """Compare *got* values against *oracle* values.

        Returns a list of mismatch descriptions (human-readable).
        """
        diffs: list[str] = []
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
                    diffs.append(
                        f"[{engine}] vector {vi} signal {sig}: expected={_val_repr(exp)} got={_val_repr(got_val)}"
                    )
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
            "elapsed_seconds": elapsed,
            "rate": self.total_modules / max(elapsed, 0.1),
            "engines": self._engines,
            "icarus_enabled": self._icarus,
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
                    if (value.val >> i) & 1:
                        bits.append("x")
                    else:
                        bits.append("z")
                else:
                    bits.append("1" if (value.val >> i) & 1 else "0")
            return f"{w}'b{''.join(bits)}"
        return f"{w}'d{value.val}"


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
