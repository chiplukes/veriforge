"""Automatic design reducer: shrink a failing Verilog design to a minimal repro.

Delta debugging (in the style of C-Reduce) on the parsed model rather than
the text. The design is edited in place and re-emitted. An edit is kept
when the emitted text is smaller and a predicate says the failure still
reproduces; otherwise it is undone. Passes, repeated until none makes
progress:

1. **delete** -- remove chunks (halving down to single items) of modules,
   module items (instances, always/initial blocks, assigns, generate blocks,
   declarations, ports, parameters...), block statements, case items,
   generate items, and port/parameter connections;
2. **statements** -- replace a statement with one of its branches/bodies
   (``if`` -> then/else, ``case`` -> an item, loop -> body, one-statement
   ``begin``/``end`` -> the statement) or drop an ``else``;
3. **expressions** -- replace an expression with one of its operands or the
   literal ``0`` (assignment targets are left alone).

Predicates (``veriforge.reduce.predicates``): ``EngineMismatch`` (engines
disagree on an output), ``EngineError`` (an error matching a regex) and
``Command`` (an external command exits 0 -- for Icarus or Verilator checks);
``AllOf(IcarusAccepts(top), p)`` keeps every candidate valid for Icarus
(the CLI does this by default when ``iverilog`` is installed).
Each check runs in a worker process with a timeout, as an edit can make a
design loop forever.

Usage::

    uv run -m veriforge.reduce design.v --top t --mismatch --engines reference,compiled -o min.v

See notes/simulation/debug.md ("Reducing a failing design").
"""

from ._reducer import ITEM_LISTS, Reducer, ReduceError, emit, parse_text
from .predicates import AllOf, Command, EngineError, EngineMismatch, IcarusAccepts

__all__ = [
    "ITEM_LISTS",
    "AllOf",
    "Command",
    "EngineError",
    "EngineMismatch",
    "IcarusAccepts",
    "ReduceError",
    "Reducer",
    "emit",
    "parse_text",
    "regression_test_source",
]


def regression_test_source(predicate: EngineMismatch | EngineError, text: str, *, name: str = "reduced") -> str:
    """A pytest module reproducing *predicate*'s failure on *text* (to adapt into a regression test)."""
    stim = predicate.stimulus
    stim_src = (
        f"Stimulus(cycles={stim.cycles}, seed={stim.seed}, clocks={stim.clocks!r}, "
        f"resets={stim.resets!r}, x_prob={stim.x_prob!r})"
    )
    head = (
        f'"""Reduced by veriforge.reduce. TODO: describe the bug and the expected behaviour."""\n\n'
        "from veriforge.reduce import parse_text\n"
        "from veriforge.sim.diverge import Stimulus, first_divergences, record_trace\n\n"
        f"SOURCE = r'''\n{text}'''\n\n"
    )
    if isinstance(predicate, EngineMismatch):
        body = (
            f"def test_{name}_engines_agree():\n"
            f"    design = parse_text(SOURCE)\n"
            f"    stim = {stim_src}\n"
            f"    traces = [record_trace(design, {predicate.top!r}, e, stim) for e in {predicate.engines!r}]\n"
            f"    for tr in traces[1:]:\n"
            f"        assert not tr.error, tr.error\n"
            f"        assert not first_divergences(traces[0], tr, ignore_x={predicate.ignore_x!r})\n"
        )
    else:
        body = (
            f"def test_{name}_no_error():\n"
            f"    design = parse_text(SOURCE)\n"
            f"    stim = {stim_src}\n"
            f"    for engine in {predicate.engines!r}:\n"
            f"        tr = record_trace(design, {predicate.top!r}, engine, stim)\n"
            f"        assert tr.error is None, tr.error\n"
        )
    return head + body
