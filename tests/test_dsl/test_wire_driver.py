"""``x = m.wire(expr)``: declare a wire named after the variable and drive it."""

import re

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import Module, cat, mux, signed


def _ports(m: Module):
    a = m.input(width=8)
    b = m.input(width=8)
    c = m.input()
    s4 = m.input(width=4, signed=True)
    s6 = m.input(width=6, signed=True)
    mem = m.wire(width=12, depth=4)
    return a, b, c, s4, s6, mem


def _decl(text: str, name: str) -> str:
    return next(
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("wire") and line.rstrip(";").endswith(f" {name}")
    )


def test_same_rtl_as_declare_then_assign():
    one = Module("t")
    a, b, c, s4, s6, mem = _ports(one)
    x = one.wire(mux(c, a, b) & ~a)
    y = one.wire(a + b, width=9)
    o = one.output(width=9)
    o.assign = x + y

    two = Module("t")
    a, b, c, s4, s6, mem = _ports(two)
    x = two.wire(width=8)
    x.assign = mux(c, a, b) & ~a
    y = two.wire(width=9)
    y.assign = a + b
    o = two.output(width=9)
    o.assign = x + y

    assert emit_module(one.build()) == emit_module(two.build())


@pytest.mark.parametrize(
    ("make", "decl"),
    [
        (lambda a, b, c, s4, s6, mem: a == b, "wire t"),
        (lambda a, b, c, s4, s6, mem: (a < b) & c, "wire t"),
        (lambda a, b, c, s4, s6, mem: a & ~b, "wire [7:0] t"),
        (lambda a, b, c, s4, s6, mem: a[3:0] | 0xF, "wire [3:0] t"),
        (lambda a, b, c, s4, s6, mem: a & 0x1FF, "wire [8:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, a[5:0], 0), "wire [5:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, 7, s4), "wire signed [3:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, 8, s4), "wire signed [4:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, -8, s4), "wire signed [3:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, s4, s6), "wire signed [5:0] t"),
        (lambda a, b, c, s4, s6, mem: mux(c, s4, a), "wire [7:0] t"),
        (lambda a, b, c, s4, s6, mem: cat(a, c, b[1:0]), "wire [10:0] t"),
        (lambda a, b, c, s4, s6, mem: cat(s4, s4), "wire [7:0] t"),
        (lambda a, b, c, s4, s6, mem: a >> 3, "wire [7:0] t"),
        (lambda a, b, c, s4, s6, mem: a[2], "wire t"),
        (lambda a, b, c, s4, s6, mem: mem[1], "wire [11:0] t"),
        (lambda a, b, c, s4, s6, mem: signed(a), "wire signed [7:0] t"),
        (lambda a, b, c, s4, s6, mem: c, "wire t"),
    ],
)
def test_width_and_signedness_from_the_driver(make, decl):
    m = Module("t")
    sigs = _ports(m)
    t = m.wire(make(*sigs))
    assert t._width == int(decl.split("[")[1].split(":")[0]) + 1 if "[" in decl else t._width == 1
    assert _decl(emit_module(m.build()), "t") == decl + ";"


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda a, b, c, s4, s6, mem: a + b, "'+' can carry or grow"),
        (lambda a, b, c, s4, s6, mem: a << 1, "'<<' can carry or grow"),
        (lambda a, b, c, s4, s6, mem: a & -1, "negative integer"),
        (lambda a, b, c, s4, s6, mem: mux(c, 1, 2), "integers only"),
    ],
)
def test_growing_or_unbounded_drivers_need_a_width(make, why):
    m = Module("t")
    sigs = _ports(m)
    with pytest.raises(ValueError, match=re.escape(why)):
        t = m.wire(make(*sigs))  # noqa: F841
    m2 = Module("t")
    sigs = _ports(m2)
    t = m2.wire(make(*sigs), width=8)
    assert t._width == 8


def test_explicit_width_and_signed_override():
    m = Module("t")
    a, b, c, s4, s6, mem = _ports(m)
    t = m.wire(a & b, width=12, signed=True)
    assert _decl(emit_module(m.build()), "t") == "wire signed [11:0] t;"


def test_name_comes_from_the_variable_and_attrs_apply():
    m = Module("t")
    a, b, c, s4, s6, mem = _ports(m)
    accept_now = m.wire(a == b, attrs={"keep": "true"}, comment="a and b agree")
    text = emit_module(m.build())
    assert accept_now._name == "accept_now"
    assert "assign accept_now = a == b;" in text
    assert '(* keep = "true" *)' in text


def test_driven_wire_refuses_init_and_depth():
    m = Module("t")
    a, b, c, s4, s6, mem = _ports(m)
    with pytest.raises(TypeError, match="init= or depth="):
        t = m.wire(a & b, init=0)  # noqa: F841


def test_int_first_argument_still_means_a_missing_width_keyword():
    m = Module("t")
    with pytest.raises(TypeError, match="width= by keyword"):
        t = m.wire(8)  # noqa: F841


def test_name_keyword_names_a_driven_wire_in_a_loop():
    one = Module("t")
    a, b, c, s4, s6, mem = _ports(one)
    lanes = [one.wire(a[2 * i + 1 : 2 * i] & b[2 * i + 1 : 2 * i], name=f"lane_w{i}") for i in range(4)]
    o = one.output(width=8)
    o.assign = cat(*reversed(lanes))

    two = Module("t")
    a, b, c, s4, s6, mem = _ports(two)
    lanes = []
    for i in range(4):
        lane = two.wire(f"lane_w{i}", width=2)
        lane.assign = a[2 * i + 1 : 2 * i] & b[2 * i + 1 : 2 * i]
        lanes.append(lane)
    o = two.output(width=8)
    o.assign = cat(*reversed(lanes))

    assert emit_module(one.build()) == emit_module(two.build())


def test_name_keyword_also_names_an_undriven_wire():
    m = Module("t")
    w = m.wire(name="spare", width=3)
    assert w._name == "spare"
    assert _decl(emit_module(m.build()), "spare") == "wire [2:0] spare;"


def test_two_names_are_refused():
    m = Module("t")
    with pytest.raises(TypeError, match="two names"):
        m.wire("x", name="y")
