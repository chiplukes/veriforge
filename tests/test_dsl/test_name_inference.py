"""DSL declarations without a name take it from the variable they're assigned to.

``x = m.wire(width=8)`` declares wire ``x`` (``dsl/_varname.py`` reads the
caller's bytecode). Every case runs plain, under ``sys.settrace``, and under
``sys.monitoring`` (line and instruction events), since debuggers and coverage
tools use those and the bytecode details differ between Python versions.
"""

from __future__ import annotations

import sys

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import Module, posedge
from veriforge.dsl import _varname

_MODES = ["plain", "settrace"] + (["monitoring", "monitoring-instructions"] if hasattr(sys, "monitoring") else [])


@pytest.fixture(params=_MODES, autouse=True)
def tracing(request):
    mode = request.param
    if mode == "settrace":
        previous = sys.gettrace()
        sys.settrace(lambda *a: lambda *b: None)
        yield mode
        sys.settrace(previous)
    elif mode.startswith("monitoring"):
        mon = sys.monitoring
        tool = next(t for t in range(6) if mon.get_tool(t) is None)
        mon.use_tool_id(tool, "veriforge-test")
        events = mon.events.LINE | mon.events.PY_START
        if mode == "monitoring-instructions":
            events |= mon.events.INSTRUCTION
        for event in (mon.events.LINE, mon.events.PY_START, mon.events.INSTRUCTION):
            mon.register_callback(tool, event, lambda *a: None)
        mon.set_events(tool, events)
        yield mode
        mon.set_events(tool, 0)
        mon.free_tool_id(tool)
    else:
        yield mode


def _names(m: Module) -> list[str]:
    return list(m._signals)


# ---------------------------------------------------------------------------
# Inferred
# ---------------------------------------------------------------------------


class TestInferred:
    def test_every_declaration_method(self):
        m = Module("t")
        m.typedef_struct("bus_t", [("data", "logic", 8)], packed=True)
        p_width = m.parameter(default=8)
        depth = m.localparam(value=4)
        clk = m.input()
        data = m.input(width=8)
        y = m.output(width=8)
        q = m.output_reg(width=8, init=0)
        pad = m.inout()
        w = m.wire(width=8)
        r = m.reg(width=8, signed=True)
        i = m.integer()
        bus = m.struct_var(type_name="bus_t")
        assert _names(m) == ["p_width", "depth", "clk", "data", "y", "q", "pad", "w", "r", "i", "bus"]
        assert [s._name for s in (p_width, depth, clk, data, y, q, pad, w, r, i, bus)] == _names(m)

    def test_annotated_and_conditional(self):
        m = Module("t")
        a: object = m.wire(width=3)
        for flag in (True, False):
            mm = Module("u")
            if flag:
                b = mm.wire()
            else:
                b = mm.wire(width=2)
            assert b._name == "b"
        assert a._name == "a"

    def test_returned_variable(self):
        # 3.13+ fuses `x = ...; return x` into STORE_FAST_LOAD_FAST.
        def make(m):
            x = m.wire()
            return x

        def two(m):
            s = m.wire()
            t = m.wire()
            return s, t  # noqa: E702

        assert make(Module("t"))._name == "x"
        assert [sig._name for sig in two(Module("t"))] == ["s", "t"]

    def test_closure_variable(self):
        m = Module("t")
        z = m.wire()

        def inner():
            return z

        assert inner()._name == "z"

    def test_module_level_and_global(self):
        ns: dict = {"Module": Module}
        exec(compile("m = Module('t')\nsig = m.wire()\n", "<module>", "exec"), ns)  # noqa: S102
        assert ns["sig"]._name == "sig"
        exec(compile("def f(m):\n    global g\n    g = m.wire()\nf(Module('t'))\n", "<global>", "exec"), ns)  # noqa: S102
        assert ns["g"]._name == "g"

    def test_class_body(self):
        m = Module("t")

        class Ports:
            ready = m.output()

        assert Ports.ready._name == "ready"

    def test_extended_arg(self):
        # More than 256 locals: the store needs EXTENDED_ARG.
        body = "".join(f"    v{k} = {k}\n" for k in range(300))
        ns: dict = {}
        exec(compile(f"def f(m):\n{body}    big_one = m.wire()\n    return big_one\n", "<big>", "exec"), ns)  # noqa: S102
        assert ns["f"](Module("t"))._name == "big_one"

    def test_many_calls_one_function(self):
        m = Module("t")
        a = m.wire()
        b = m.wire(width=2)
        c = m.reg(width=3)
        assert [s._name for s in (a, b, c)] == ["a", "b", "c"]

    def test_conditional_expression(self):
        # The first branch's call is followed by a forward jump to the store.
        for wide in (True, False):
            m = Module("t")
            d = m.wire(width=8) if wide else m.reg(width=4)
            e = wide and m.wire()
            assert d._name == "d"
            if wide:
                assert e._name == "e"

    def test_multiline_call(self):
        m = Module("t")
        f = m.wire(
            width=8,
            signed=True,
        )
        assert f._name == "f"

    def test_explicit_names_unchanged(self):
        m = Module("t")
        a = m.wire("other", 8)
        b = m.input(name="clk")
        c = m.parameter("W", 8)
        assert [s._name for s in (a, b, c)] == ["other", "clk", "W"]

    def test_same_rtl_as_explicit_names(self):
        def explicit():
            m = Module("counter")
            width = m.parameter("width", 8)
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", width=width)
            nxt = m.wire("nxt", width=width)
            m.assign(nxt, count + 1)
            with m.always(posedge(clk)):
                with m.if_(rst):
                    count <<= 0
                with m.else_():
                    count <<= nxt
            return m.build()

        def inferred():
            m = Module("counter")
            width = m.parameter(default=8)
            clk = m.input()
            rst = m.input()
            count = m.output_reg(width=width)
            nxt = m.wire(width=width)
            m.assign(nxt, count + 1)
            with m.always(posedge(clk)):
                with m.if_(rst):
                    count <<= 0
                with m.else_():
                    count <<= nxt
            return m.build()

        assert emit_module(inferred()) == emit_module(explicit())

    def test_disassembled_once_per_function(self):
        def build():
            m = Module("t")
            a = m.wire()
            b = m.wire()
            return a, b

        build()
        before = _varname._stores_after_calls.cache_info()
        build()
        after = _varname._stores_after_calls.cache_info()
        assert after.misses == before.misses
        assert after.hits == before.hits + 2


# ---------------------------------------------------------------------------
# Refused: anything but `plain_name = m.<decl>(...)` asks for an explicit name
# ---------------------------------------------------------------------------


class _Obj:
    pass


def _list_comprehension(m):
    return [m.wire() for _ in range(1)]


def _tuple(m):
    t = (m.wire(), 1)
    return t


def _chain(m):
    a = b = m.wire()
    return a, b


def _attribute(m):
    o = _Obj()
    o.s = m.wire()


def _subscript(m):
    d = {}
    d["k"] = m.wire()


def _walrus(m):
    if w := m.wire():
        return w


def _argument(m):
    return list([m.wire()])


def _returned(m):
    return m.wire()


def _unpacking(m):
    a, b = m.wire(), m.wire()
    return a, b


def _bare(m):
    m.wire()


def _expression(m):
    x = m.wire() + 1
    return x


_REFUSED = [
    _list_comprehension,
    _tuple,
    _chain,
    _attribute,
    _subscript,
    _walrus,
    _argument,
    _returned,
    _unpacking,
    _bare,
    _expression,
]


class TestRefused:
    @pytest.mark.parametrize("case", _REFUSED, ids=lambda f: f.__name__.lstrip("_"))
    def test_needs_explicit_name(self, case):
        m = Module("t")
        with pytest.raises(ValueError, match=r"m\.wire\(\) at .*test_name_inference\.py:\d+: can't infer a name"):
            case(m)
        assert _names(m) == []

    def test_positional_width_is_a_type_error(self):
        m = Module("t")
        with pytest.raises(
            TypeError, match=r"m\.wire\(\): the name must be a string, got 8; .* pass width= by keyword"
        ):
            m.wire(8)
        with pytest.raises(TypeError, match=r"pass default= by keyword"):
            m.parameter(8)
        with pytest.raises(TypeError, match=r"m\.integer\(\): the name must be a string, got 3$"):
            m.integer(3)

    def test_struct_var_needs_type_name(self):
        m = Module("t")
        with pytest.raises(TypeError, match="needs type_name="):
            bus = m.struct_var()  # noqa: F841

    def test_loop_duplicate(self):
        m = Module("t")
        with pytest.raises(ValueError, match=r"Signal 'tap' already declared .* in a loop or helper, pass a name"):
            for _ in range(2):
                tap = m.wire()  # noqa: F841
        assert _names(m) == ["tap"]

    def test_verilog_keyword(self):
        m = Module("t")
        with pytest.raises(ValueError, match=r"the variable name 'output' is a Verilog keyword"):
            output = m.output()  # noqa: F841
        with pytest.raises(ValueError, match=r"'logic' is a Verilog keyword"):
            logic = m.wire()  # noqa: F841
        assert _names(m) == []

    def test_non_cpython(self, monkeypatch):
        monkeypatch.setattr(_varname, "SUPPORTED", False)
        m = Module("t")
        with pytest.raises(ValueError, match="name inference needs CPython"):
            x = m.wire()  # noqa: F841
        assert m.wire("x")._name == "x"
