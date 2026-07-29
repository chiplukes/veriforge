"""Cross-engine assignment-semantics matrix (work plan item 2.1).

Enumerates size-mismatched and signed<->unsigned assignment across all four
engines (reference, vm, vm-fast, compiled) and four assignment kinds
(continuous assign, blocking in `always @(*)`, non-blocking in
`always @(posedge clk)`, and a port-crossing connection through a child
instance).

Curation notes (this is a curated matrix, not a full cartesian product):
  - Widths, signedness combos, and stimulus values follow the tables in
    `notes/plans/architecture_review_2026-07.md` / the work plan verbatim.
  - `$signed()`/`$unsigned()` cast forms are only exercised for
    continuous/blocking/nonblocking kinds, not port-crossing: a cast makes
    sense as "the RHS of an assignment", and the only assignment in the
    port-crossing shape that actually performs the width truncation/
    extension under test is the *port connection itself* (out_port -> dst),
    which has no expression position to wrap in a cast without changing the
    shape being tested. See _build_port's docstring for the exact shape.
  - Each (kind, src_w, dst_w) cell shares ONE compiled module across all of
    its signedness/cast variants (variants are separate port pairs in the
    same module) and across all 5 stimulus values (checked as an internal
    loop, not separate pytest cases) -- this holds the compiled-engine
    compile count to len(KINDS) * len(WIDTH_PAIRS) == 36 rather than one
    compile per pytest case. Results are cached per (engine, kind, src_w,
    dst_w) and reused by every pytest case (variant) that shares that cell.
  - Every non-reference-engine case is checked against both the Python
    oracle and the reference engine's own result for the same stimulus
    (double bookkeeping -- catches oracle bugs as well as engine bugs).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# =====================================================================
# Matrix tables
# =====================================================================

WIDTH_PAIRS: list[tuple[int, int]] = [
    (4, 8),
    (8, 4),
    (8, 8),
    (63, 64),
    (64, 63),
    (64, 65),
    (65, 64),
    (65, 80),
    (80, 65),
]

# Width pairs that additionally get $signed()/$unsigned() cast-form variants.
CAST_WIDTH_PAIRS = {(4, 8), (63, 64), (65, 80)}

# (variant id, src declared-signed, dst declared-signed)
SIGNEDNESS: list[tuple[str, bool, bool]] = [
    ("u", False, False),
    ("s", True, True),
    ("su", True, False),
    ("us", False, True),
]

# (variant id, cast keyword)
CAST_FORMS: list[tuple[str, str]] = [
    ("scast", "signed"),
    ("ucast", "unsigned"),
]

KINDS = ["continuous", "blocking", "nonblocking", "port"]
KIND_ABBREV = {"continuous": "cont", "blocking": "blk", "nonblocking": "nba", "port": "port"}


@dataclass(frozen=True)
class _Variant:
    """One signedness/cast combination sharing a (kind, src_w, dst_w) module."""

    id: str
    src_signed: bool
    dst_signed: bool
    cast: str | None
    sign_extend: bool

    @property
    def src_name(self) -> str:
        return f"src_{self.id}"

    @property
    def dst_name(self) -> str:
        return f"dst_{self.id}"


def _variants_for(src_w: int, dst_w: int, kind: str) -> list[_Variant]:
    variants = [
        _Variant(vid, src_signed, dst_signed, cast=None, sign_extend=src_signed)
        for vid, src_signed, dst_signed in SIGNEDNESS
    ]
    if kind != "port" and (src_w, dst_w) in CAST_WIDTH_PAIRS:
        variants += [
            _Variant(vid, src_signed=False, dst_signed=False, cast=cast, sign_extend=(cast == "signed"))
            for vid, cast in CAST_FORMS
        ]
    return variants


# =====================================================================
# Verilog source generation
# =====================================================================


def _port_decl(width: int, signed: bool, direction: str, name: str) -> str:
    sig = "signed " if signed else ""
    return f"{direction} {sig}[{width - 1}:0] {name}"


def _build_continuous(src_w: int, dst_w: int, variants: list[_Variant]) -> str:
    ports: list[str] = []
    body: list[str] = []
    for v in variants:
        ports.append(_port_decl(src_w, v.src_signed, "input", v.src_name))
        ports.append(_port_decl(dst_w, v.dst_signed, "output", v.dst_name))
        rhs = v.src_name if v.cast is None else f"${v.cast}({v.src_name})"
        body.append(f"    assign {v.dst_name} = {rhs};")
    header = ",\n    ".join(ports)
    return f"module t(\n    {header}\n);\n" + "\n".join(body) + "\nendmodule\n"


def _build_blocking(src_w: int, dst_w: int, variants: list[_Variant]) -> str:
    ports: list[str] = []
    stmts: list[str] = []
    for v in variants:
        ports.append(_port_decl(src_w, v.src_signed, "input", v.src_name))
        ports.append(_port_decl(dst_w, v.dst_signed, "output reg", v.dst_name))
        rhs = v.src_name if v.cast is None else f"${v.cast}({v.src_name})"
        stmts.append(f"        {v.dst_name} = {rhs};")
    header = ",\n    ".join(ports)
    body = "\n".join(stmts)
    return f"module t(\n    {header}\n);\n    always @(*) begin\n{body}\n    end\nendmodule\n"


def _build_nonblocking(src_w: int, dst_w: int, variants: list[_Variant]) -> str:
    ports = ["input clk"]
    stmts: list[str] = []
    for v in variants:
        ports.append(_port_decl(src_w, v.src_signed, "input", v.src_name))
        ports.append(_port_decl(dst_w, v.dst_signed, "output reg", v.dst_name))
        rhs = v.src_name if v.cast is None else f"${v.cast}({v.src_name})"
        stmts.append(f"        {v.dst_name} <= {rhs};")
    header = ",\n    ".join(ports)
    body = "\n".join(stmts)
    return f"module t(\n    {header}\n);\n    always @(posedge clk) begin\n{body}\n    end\nendmodule\n"


def _build_port(src_w: int, dst_w: int, variants: list[_Variant]) -> str:
    """Parent instantiates child; child passes its input port straight through.

    The child's ports are both src_w wide (no truncation/extension happens
    inside the child). The out_port -> dst port *connection* is the only
    place src_w is reconciled against dst_w, so that connection is what is
    under test. out_port's declared signedness stands in for "signedness of
    the value flowing into the connection" (== the src variant's declared
    signedness); dst's declared signedness is present but must NOT affect
    the extension (per IEEE 1364-2005 Sec 5.5, extension follows the RHS/
    source operand, not the target).
    """
    child_ports: list[str] = []
    child_body: list[str] = []
    top_ports: list[str] = []
    inst_conns: list[str] = []
    for v in variants:
        in_name = f"in_port_{v.id}"
        out_name = f"out_port_{v.id}"
        child_ports.append(_port_decl(src_w, v.src_signed, "input", in_name))
        child_ports.append(_port_decl(src_w, v.src_signed, "output", out_name))
        child_body.append(f"    assign {out_name} = {in_name};")
        top_ports.append(_port_decl(src_w, v.src_signed, "input", v.src_name))
        top_ports.append(_port_decl(dst_w, v.dst_signed, "output", v.dst_name))
        inst_conns.append(f".{in_name}({v.src_name})")
        inst_conns.append(f".{out_name}({v.dst_name})")
    child_header = ",\n    ".join(child_ports)
    top_header = ",\n    ".join(top_ports)
    conns = ",\n        ".join(inst_conns)
    return (
        f"module child(\n    {child_header}\n);\n" + "\n".join(child_body) + "\nendmodule\n\n"
        f"module top(\n    {top_header}\n);\n    child u_child(\n        {conns}\n    );\nendmodule\n"
    )


_BUILDERS = {
    "continuous": _build_continuous,
    "blocking": _build_blocking,
    "nonblocking": _build_nonblocking,
    "port": _build_port,
}


def _build_module(kind: str, src_w: int, dst_w: int) -> tuple[str, list[_Variant]]:
    variants = _variants_for(src_w, dst_w, kind)
    source = _BUILDERS[kind](src_w, dst_w, variants)
    return source, variants


# =====================================================================
# Simulation plumbing
# =====================================================================


def _parse_design(source: str) -> Design:
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(source)
    return tree_to_design(tree, source_file="test.v")


def _sim_for(source: str, engine: str, top_name: str) -> Simulator:
    design = _parse_design(source)
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == top_name)
    return Simulator(top, engine=engine, design=design)


def _stimuli(width: int) -> list[tuple[int, int]]:
    """(val, mask) pairs: 0, 1, all-ones, MSB-set, and one x-contaminated value."""
    ones = (1 << width) - 1
    return [
        (0, 0),
        (1, 0),
        (ones, 0),
        (1 << (width - 1), 0),
        (0, 1),  # low bit x-contaminated
    ]


def _oracle(val: int, mask: int, src_w: int, dst_w: int, sign_extend: bool) -> tuple[int, int]:
    """Expected (val, mask) assigning a src_w-bit value into a dst_w-bit target."""
    src_full = (1 << src_w) - 1
    val &= src_full
    mask &= src_full
    if dst_w <= src_w:
        dst_full = (1 << dst_w) - 1
        return val & dst_full, mask & dst_full
    ext_bits = dst_w - src_w
    ext_full = (1 << ext_bits) - 1
    if sign_extend:
        sign_x = (mask >> (src_w - 1)) & 1
        if sign_x:
            ext_val, ext_mask = 0, ext_full
        else:
            sign_val = (val >> (src_w - 1)) & 1
            ext_val, ext_mask = (ext_full if sign_val else 0), 0
    else:
        ext_val, ext_mask = 0, 0
    return val | (ext_val << src_w), mask | (ext_mask << src_w)


def _run_case(source: str, engine: str, kind: str, src_w: int, variants: list[_Variant]) -> list[dict[str, Value]]:
    top_name = "top" if kind == "port" else "t"
    sim = _sim_for(source, engine, top_name)
    is_seq = kind == "nonblocking"
    if is_seq:
        sim.drive("clk", Value(0, width=1))
        for v in variants:
            sim.drive(v.src_name, Value(0, width=src_w))
        sim.settle()

    results: list[dict[str, Value]] = []
    for val, mask in _stimuli(src_w):
        for v in variants:
            sim.drive(v.src_name, Value(val, width=src_w, mask=mask))
        sim.settle()
        if is_seq:
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            sim.drive("clk", Value(0, width=1))
            sim.settle()
        results.append({v.dst_name: sim.read(v.dst_name) for v in variants})
    return results


# Cache of (variants, per-stimulus dst readings) keyed by (engine, kind, src_w,
# dst_w) -- every pytest case (a single variant) sharing a cell reuses the one
# Simulator run for that cell instead of re-running/re-compiling per variant.
_RESULTS_CACHE: dict[tuple[str, str, int, int], tuple[list[_Variant], list[dict[str, Value]]]] = {}


def _get_case_results(engine: str, kind: str, src_w: int, dst_w: int) -> tuple[list[_Variant], list[dict[str, Value]]]:
    key = (engine, kind, src_w, dst_w)
    cached = _RESULTS_CACHE.get(key)
    if cached is not None:
        return cached
    source, variants = _build_module(kind, src_w, dst_w)
    results = _run_case(source, engine, kind, src_w, variants)
    _RESULTS_CACHE[key] = (variants, results)
    return variants, results


# =====================================================================
# Case enumeration
# =====================================================================


@dataclass(frozen=True)
class _Case:
    kind: str
    src_w: int
    dst_w: int
    variant: _Variant

    @property
    def id(self) -> str:
        return f"{self.variant.id}{self.src_w}_to_{self.variant.id}{self.dst_w}_{KIND_ABBREV[self.kind]}"


def _build_cases() -> list[_Case]:
    cases = []
    for kind in KINDS:
        for src_w, dst_w in WIDTH_PAIRS:
            _, variants = _build_module(kind, src_w, dst_w)
            for v in variants:
                cases.append(_Case(kind, src_w, dst_w, v))
    return cases


CASES = _build_cases()


# =====================================================================
# Known compiled-engine bugs (see notes/known_issues.md)
#
# Discovered by this matrix (item 2.1) and fixed in item 2.7 sub-items 1-2
# -- no known bugs left here. Kept as an explicit no-args-needed hook
# (rather than deleted outright) since this is exactly where the next
# discovered compiled-engine bug from this matrix should be filed.
# =====================================================================


def _combo_params() -> list:
    params = []
    for engine in ENGINES:
        for case in CASES:
            pid = f"{engine}-{case.id}"
            params.append(pytest.param(engine, case, id=pid))
    return params


# =====================================================================
# The test
# =====================================================================


@pytest.mark.cross_engine
@pytest.mark.parametrize("engine,case", _combo_params())
def test_assignment_matrix(engine: str, case: _Case) -> None:
    _, results = _get_case_results(engine, case.kind, case.src_w, case.dst_w)
    _, ref_results = _get_case_results("reference", case.kind, case.src_w, case.dst_w)
    v = case.variant
    stimuli = _stimuli(case.src_w)
    for (val, mask), row, ref_row in zip(stimuli, results, ref_results, strict=True):
        exp_val, exp_mask = _oracle(val, mask, case.src_w, case.dst_w, v.sign_extend)
        oracle_value = Value(exp_val, width=case.dst_w, mask=exp_mask)
        actual = row[v.dst_name]
        assert actual == oracle_value, (
            f"{case.kind} {case.src_w}->{case.dst_w} {v.id}: "
            f"stim val={val:#x} mask={mask:#x}: {engine} got {actual}, oracle {oracle_value}"
        )
        assert actual == ref_row[v.dst_name], (
            f"{case.kind} {case.src_w}->{case.dst_w} {v.id}: "
            f"stim val={val:#x} mask={mask:#x}: {engine}={actual} reference={ref_row[v.dst_name]}"
        )
