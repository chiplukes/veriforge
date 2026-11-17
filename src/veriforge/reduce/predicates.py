"""Built-in reduction predicates: "is this Verilog text still interesting?".

Each is a picklable dataclass called with the emitted Verilog text. Any
exception it does not itself look for makes the text uninteresting, so a
reduction never wanders from the failure it started with into a different one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace

from ..sim.diverge import Stimulus, first_divergences, record_trace
from ._reducer import parse_text


def _outputs(design, top: str) -> list[str] | None:
    module = design.get_module(top)
    if module is None:
        return None
    return [p.name for p in module.output_ports()]


# cm:6e2b18
@dataclass
class EngineMismatch:
    """Interesting while two or more engines disagree on an observed signal.

    *signals* limits the observed signals (default: the top's output ports):
    outputs or internal signals by flattened name (``u.q`` -- say, the root
    ``veriforge.sim.diverge`` reported). Names the design no longer has are
    skipped. A parse, elaboration or run error on any engine is not interesting.
    """

    top: str
    engines: tuple[str, ...] = ("reference", "compiled")
    stimulus: Stimulus = field(default_factory=Stimulus)
    signals: tuple[str, ...] | None = None
    ignore_x: bool = False

    def _traces(self, text: str):
        design = parse_text(text)
        outs = _outputs(design, self.top)
        if not outs:
            return None
        observe = list(self.signals) if self.signals else outs
        traces = []
        for engine in self.engines:
            tr = record_trace(design, self.top, engine, self.stimulus, signals=observe)
            if tr.error:
                return None
            traces.append(tr)
        return traces

    def __call__(self, text: str) -> bool:
        try:
            traces = self._traces(text)
        except Exception:  # noqa: BLE001
            return False
        if not traces:
            return False
        return any(first_divergences(traces[0], tr, ignore_x=self.ignore_x) for tr in traces[1:])

    def narrowed(self, text: str) -> EngineMismatch:
        """A copy whose stimulus stops at the cycle of the first divergence (fewer cycles per check)."""
        traces = self._traces(text)
        if not traces:
            return self
        first = min(
            (d.time for tr in traces[1:] for d in first_divergences(traces[0], tr, ignore_x=self.ignore_x).values()),
            default=None,
        )
        if first is None:
            return self
        return replace(self, stimulus=replace(self.stimulus, cycles=first // 3 + 1))


@dataclass
class EngineError:
    """Interesting while parsing, elaborating or running on any of *engines*
    raises an error whose ``"Type: message"`` matches the regex *pattern*."""

    top: str
    pattern: str
    engines: tuple[str, ...] = ("reference",)
    stimulus: Stimulus = field(default_factory=Stimulus)

    def error(self, text: str) -> str | None:
        """The first matching error message, or None."""
        rx = re.compile(self.pattern)
        msg: str | None
        try:
            design = parse_text(text)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            return msg if rx.search(msg) else None
        for engine in self.engines:
            try:
                tr = record_trace(design, self.top, engine, self.stimulus, signals=_outputs(design, self.top) or [])
                msg = tr.error
            except Exception as exc:  # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
            if msg and rx.search(msg):
                return f"[{engine}] {msg}"
        return None

    def __call__(self, text: str) -> bool:
        return self.error(text) is not None


@dataclass
class IcarusAccepts:
    """Interesting while ``iverilog -g2012`` compiles the text (with *top* as root).

    Used as a validity guard (``AllOf(IcarusAccepts(top), predicate)``) so a
    reduction does not drift into Verilog only veriforge accepts -- an
    undeclared parameter, a deleted declaration of a used variable (an
    implicit net, warned about by ``-Wimplicit``, also counts as invalid).
    """

    top: str
    timeout: float = 30.0
    iverilog: str = "iverilog"

    def __call__(self, text: str) -> bool:
        tmp = tempfile.mkdtemp(prefix="vf_reduce_")
        src = os.path.join(tmp, "design.sv")
        try:
            with open(src, "w", encoding="utf-8") as f:
                f.write(text)
            res = subprocess.run(  # noqa: S603
                [self.iverilog, "-g2012", "-Wimplicit", "-s", self.top, "-o", os.path.join(tmp, "a.vvp"), src],
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            # (Implicit nets are legal, but a repro should declare what it uses.)
            return res.returncode == 0 and b"implicit definition" not in res.stderr
        except (OSError, subprocess.TimeoutExpired):
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class AllOf:
    """Interesting while every predicate is (checked in order; put cheap ones first)."""

    predicates: tuple

    def __call__(self, text: str) -> bool:
        return all(p(text) for p in self.predicates)

    @property
    def main(self):
        """The last predicate: the failure being reduced (the others are guards)."""
        return self.predicates[-1]


@dataclass
class Command:
    """Interesting while the shell command exits 0. ``{file}`` in *cmd* is
    replaced by the path of a temporary file holding the Verilog text."""

    cmd: str
    timeout: float | None = None

    def __call__(self, text: str) -> bool:
        fd, path = tempfile.mkstemp(suffix=".sv", prefix="vf_reduce_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            try:
                res = subprocess.run(  # noqa: S602 -- the user's own command line
                    self.cmd.replace("{file}", path),
                    shell=True,
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False
            return res.returncode == 0
        finally:
            os.unlink(path)
