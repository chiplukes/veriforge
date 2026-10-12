"""Seeded fuzz checks of the DSL translation paths.

* Control flow: random if/elif/else/case trees built with the DSL, emitted,
  re-parsed and simulated, against a direct Python evaluation of the same
  tree on every input combination. (Catches dangling-else style emitter bugs:
  with the 58e03cb fix disabled, ~10% of seeds fail.)
* Emitter fidelity: a fuzz-generated model simulated directly must match its
  emitted text, and emit(parse(.)) must reach a fixed point. (The fuzz runner
  only ever simulates the emitted text, so an emitter bug that changes
  semantics is invisible there -- veriforge and Icarus see the same wrong
  text. This is how `for (int i ...)` losing its `int` was found.)
* Verilog -> DSL -> Verilog: `module_to_dsl` output, executed, must simulate
  like the original. Designs using constructs the DSL can't express must fail
  with the converter's `unsupported(...)` NotImplementedError, not run wrong.
"""

from __future__ import annotations

import contextlib
import copy
import io
import itertools
import random

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.codegen.verilog_emitter import emit_design, emit_module
from veriforge.convert.to_dsl import module_to_dsl
from veriforge.dsl import Module, lnot
from veriforge.fuzz._module_gen import ModuleGenerator
from veriforge.fuzz._runner import FuzzRunner
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------

_INS = ["a", "b", "c", "d"]


def _gen_cond(rng: random.Random) -> tuple:
    k = rng.random()
    v = rng.choice(_INS)
    if k < 0.5:
        return ("in", v)
    if k < 0.7:
        return ("not", v)
    return ("and", v, rng.choice(_INS))


def _gen_block(rng: random.Random, depth: int) -> list:
    if depth > 0 and rng.random() < 0.08:
        return []  # empty body (`if (c) begin end`)
    return [_gen_stmt(rng, depth) for _ in range(rng.randint(1, 2 if depth > 0 else 3))]


def _gen_stmt(rng: random.Random, depth: int) -> tuple:
    k = rng.random()
    if depth >= 3 or k < 0.3:
        return ("set", rng.randint(0, 7))
    if k < 0.75:
        branches = [(_gen_cond(rng), _gen_block(rng, depth + 1))]
        for _ in range(rng.randint(0, 2)):
            branches.append((_gen_cond(rng), _gen_block(rng, depth + 1)))
        els = _gen_block(rng, depth + 1) if rng.random() < 0.5 else None
        return ("if", branches, els)
    items = []
    for v in rng.sample(range(4), rng.randint(1, 3)):
        vals = [v] + ([] if rng.random() < 0.7 else [rng.randint(0, 3)])
        items.append((vals, _gen_block(rng, depth + 1)))
    dflt = _gen_block(rng, depth + 1) if rng.random() < 0.5 else None
    return ("case", items, dflt)


def _ev_cond(c: tuple, env: dict) -> int:
    if c[0] == "in":
        return env[c[1]]
    if c[0] == "not":
        return 1 - env[c[1]]
    return env[c[1]] & env[c[2]]


def _run(block: list, env: dict, st: dict) -> None:
    for s in block:
        if s[0] == "set":
            st["x"] = s[1]
            continue
        arms = s[1] if s[0] == "if" else [(("case", vals), b) for vals, b in s[1]]
        for c, b in arms:
            hit = env["s"] in c[1] if c[0] == "case" else _ev_cond(c, env)
            if hit:
                _run(b, env, st)
                break
        else:
            if s[2] is not None:
                _run(s[2], env, st)


def _build_cf(tree: list) -> Module:
    m = Module("cf")
    sig = {n: m.input(n) for n in _INS}
    sig["s"] = m.input("s", width=2)
    x = m.output_reg("x", width=3)

    def cond(c):
        if c[0] == "in":
            return sig[c[1]]
        if c[0] == "not":
            return lnot(sig[c[1]])
        return sig[c[1]] & sig[c[2]]

    def emit_block(block):
        for s in block:
            if s[0] == "set":
                x.now = s[1]
            elif s[0] == "if":
                (c0, b0), rest = s[1][0], s[1][1:]
                with m.if_(cond(c0)):
                    emit_block(b0)
                for c, b in rest:
                    with m.elif_(cond(c)):
                        emit_block(b)
                if s[2] is not None:
                    with m.else_():
                        emit_block(s[2])
            else:
                with m.case(sig["s"]) as cs:
                    for vals, b in s[1]:
                        with cs.when(*vals):
                            emit_block(b)
                    if s[2] is not None:
                        with cs.default():
                            emit_block(s[2])

    with m.always():
        x.now = 0
        emit_block(tree)
    return m


def _cf_text(seed: int, via_to_dsl: bool) -> tuple[list, str]:
    tree = _gen_block(random.Random(seed), 0)
    text = emit_module(_build_cf(tree).build())
    if via_to_dsl:
        # ... -> Verilog -> DSL code -> Verilog. (Nested cases all bound
        # `as _c`, so an outer case's later items joined the inner one.)
        code = module_to_dsl(_parse(text).modules[0])
        namespace: dict = {}
        exec(code, namespace)  # noqa: S102
        text = emit_module(namespace["module"])
    return tree, text


@pytest.mark.parametrize("via_to_dsl", [False, True], ids=["emit", "to_dsl"])
@pytest.mark.parametrize("seed", range(40))
def test_dsl_control_flow(seed, via_to_dsl):
    tree, text = _cf_text(seed, via_to_dsl)
    design = tree_to_design(verilog_parser(start="source_text").build_tree(text))
    sim = Simulator(design.modules[0], engine="reference", design=design)
    for bits in itertools.product((0, 1), repeat=4):
        for sv in range(4):
            env = dict(zip(_INS, bits, strict=True))
            env["s"] = sv
            for k, v in env.items():
                sim.drive(k, v)
            sim.settle()
            st = {"x": 0}
            _run(tree, env, st)
            got = sim.read("x")
            assert not got.is_x and int(got) == st["x"], f"inputs {env}: expected {st['x']}, got {got}\n{text}"


# ---------------------------------------------------------------------------
# Fuzz-design round trips
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    with contextlib.redirect_stdout(io.StringIO()):
        return FuzzRunner(
            output_dir=str(tmp_path_factory.mktemp("dslfuzz")), seed=0, engines=["reference"], icarus=False
        )


def _first_diff(a: list, b: list) -> str:
    i = next(k for k in range(len(a)) if a[k] != b[k])
    sig = next(s for s in a[i] if a[i][s] != b[i][s])
    return f"vector {i}, {sig}: {a[i][sig]} vs {b[i][sig]}"


def _parse(text: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(text), source_file="f.v")


@pytest.mark.parametrize("seed", range(25))
def test_emitter_fidelity(runner, seed):
    rng = random.Random(seed)
    design = ModuleGenerator(rng).generate_design(None)
    top = design.get_module("t")
    text = emit_design(design)
    vectors = runner._gen_stimulus(top, rng)
    # (The first round trip may reorder declarations; from then on it's fixed.)
    text2 = emit_design(_parse(text))
    assert emit_design(_parse(text2)) == text2
    model = copy.deepcopy(design)
    link_instances(model)
    resolve_port_connections(model)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        direct = runner._simulate_design(model, "reference", vectors)
        emitted = runner._simulate(text, "reference", vectors)
    assert direct == emitted, _first_diff(direct, emitted)


@pytest.mark.parametrize("seed", range(40))
def test_verilog_to_dsl_round_trip(runner, seed):
    rng = random.Random(seed)
    design = ModuleGenerator(rng).generate_design(None)
    if len(design.modules) != 1:
        pytest.skip("multi-module design")
    top = design.get_module("t")
    text = emit_design(design)
    vectors = runner._gen_stimulus(top, rng)
    code = module_to_dsl(_parse(text).modules[0])
    namespace: dict = {}
    try:
        exec(code, namespace)  # noqa: S102
    except NotImplementedError as e:
        assert "The DSL can't express" in str(e)
        pytest.skip(str(e))
    text2 = emit_module(namespace["module"])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        original = runner._simulate(text, "reference", vectors)
        round_trip = runner._simulate(text2, "reference", vectors)
    assert original == round_trip, _first_diff(original, round_trip)
