"""DSL declarations without a name take it from the variable they're assigned to.

``x = m.wire(width=8)`` declares wire ``x`` (``dsl/_varname.py`` reads the
caller's bytecode). Every case runs plain, under ``sys.settrace``, and under
``sys.monitoring`` (line and instruction events), since debuggers and coverage
tools use those and the bytecode details differ between Python versions.
"""

from __future__ import annotations

import re
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

    def test_comment_and_attrs_keywords(self):
        # comment=/attrs= instead of chaining .comment()/.attr(), which hides the store.
        def chained():
            m = Module("t")
            clk = m.input("clk").comment("System clock")
            state = m.reg("state", width=3).attr("fsm_encoding", "one_hot").comment("FSM state")
            m.assign(m.output("y"), clk)
            with m.always(posedge(clk)):
                state <<= state + 1
            return m.build()

        def keywords():
            m = Module("t")
            clk = m.input(comment="System clock")
            state = m.reg(width=3, attrs={"fsm_encoding": "one_hot"}, comment="FSM state")
            y = m.output()
            m.assign(y, clk)
            with m.always(posedge(clk)):
                state <<= state + 1
            return m.build()

        assert emit_module(keywords()) == emit_module(chained())

    def test_bulk_declaration_for_unpacking(self):
        # `a, b = m.wire(), m.wire()` is refused; the bulk form names both.
        m = Module("t")
        a, b = m.inputs("a b:8")
        assert [a._name, b._name] == ["a", "b"]

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

    def test_disassembled_once_per_function(self, monkeypatch):
        calls = []
        real = _varname._stores_after_calls
        monkeypatch.setattr(_varname, "_stores_after_calls", lambda code: calls.append(code) or real(code))
        monkeypatch.setattr(_varname, "_CACHE", {})

        def build():
            m = Module("t")
            a = m.wire()
            b = m.wire()
            return a, b

        for _ in range(3):
            assert [s._name for s in build()] == ["a", "b"]
        assert calls == [build.__code__]


# ---------------------------------------------------------------------------
# Loops: refused on the first pass, with the line rewritten to a per-pass name
# ---------------------------------------------------------------------------


def _for_loop(m):
    for i in range(1):  # noqa: B007 -- one pass is enough: the result mustn't depend on the count
        lane = m.wire(width=8)  # noqa: F841


def _while_loop(m):
    k = 0
    while k < 1:
        tap = m.wire()  # noqa: F841
        k += 1


def _comprehension(m):
    return [m.reg(width=8, init=0) for j in range(1)]


def _append(m):
    taps = []
    for lane in range(1):  # noqa: B007
        taps.append(m.wire())


def _nested(m):
    for row in range(1):  # noqa: B007
        for col in range(1):  # noqa: B007
            cell = m.reg()  # noqa: F841


def _tuple_target(m):
    for k, v in [(0, 1)]:  # noqa: B007
        both = m.wire()  # noqa: F841


_LOOPS = [
    (
        _for_loop,
        "the loop variable 'i'",
        "Every pass through the loop would declare another signal named 'lane'",
        'lane = m.wire(f"lane_{i}", width=8)',
    ),
    (_while_loop, "a counter", "named 'tap'", 'tap = m.wire(f"tap_{n}")'),
    (
        _comprehension,
        "the loop variable 'j'",
        "runs once per pass",
        'return [m.reg(f"sig_{j}", width=8, init=0) for j in range(1)]',
    ),
    (_append, "the loop variable 'lane'", "runs once per pass", 'taps.append(m.wire(f"taps_{lane}"))'),
    (_nested, "the loop variable 'col'", "named 'cell'", 'cell = m.reg(f"cell_{col}")'),
    (_tuple_target, "a counter", "named 'both'", 'both = m.wire(f"both_{n}")'),
]


class TestLoops:
    @pytest.mark.parametrize(
        ("case", "built_from", "why", "fixed"), _LOOPS, ids=lambda v: getattr(v, "__name__", "")[1:] or None
    )
    def test_refused_with_a_rewritten_line(self, case, built_from, why, fixed):
        m = Module("t")
        with pytest.raises(ValueError) as info:
            case(m)
        msg = str(info.value)
        assert re.match(r"m\.(wire|reg)\(\) at .*test_name_inference\.py:\d+ is inside a loop, so it can't take", msg)
        assert why in msg
        assert f"built from {built_from}" in msg
        assert f"\n\n    {fixed}\n\n" in msg
        assert "keep them in a list or dict" in msg
        assert _names(m) == []

    def test_fresh_module_each_pass(self):
        # A loop that creates its module on each pass declares into a new module
        # every time, so names don't repeat.
        built = []
        for width in (8, 16):
            m = Module(f"adder{width}")
            a = m.input(width=width)
            total = m.output(width=width + 1)
            m.assign(total, a + 1)
            built.append(m)
        for _ in range(2):
            with Module("w") as w:
                q = w.reg(width=4)
        assert [_names(b) for b in built] == [["a", "total"], ["a", "total"]]
        assert q._name == "q"

    def test_module_from_outside_the_loop(self):
        m = Module("t")
        with pytest.raises(ValueError, match="is inside a loop"):
            for _ in range(1):
                x = m.wire()  # noqa: F841
        # Created in the outer loop, declared in the inner one: repeats within a module.
        with pytest.raises(ValueError, match="built from the loop variable 'col'"):
            for _row in range(1):
                mm = Module("u")
                for col in range(1):  # noqa: B007
                    cell = mm.wire()  # noqa: F841

    def test_explicit_names_in_loops(self):
        m = Module("t")
        lanes = [m.wire(f"lane_{i}", width=8) for i in range(3)]
        for i in range(2):
            tap = m.reg(f"tap_{i}")
        assert [s._name for s in lanes] == ["lane_0", "lane_1", "lane_2"]
        assert tap._name == "tap_1"

    def test_code_after_blocks_is_not_a_loop(self):
        # with/try handlers jump back to the code after the block (out of line on
        # 3.11+); only normal-flow back jumps are loops.
        m = Module("t")
        clk = m.input()
        q = m.reg()
        with m.always(posedge(clk)):
            q <<= ~q
        a = m.wire()
        try:
            b = m.wire()
        except KeyError:
            pass
        c = m.wire()
        try:
            d = m.wire()
        finally:
            e = m.wire()
        for _ in range(2):
            pass
        f = m.wire()
        assert [s._name for s in (a, b, c, d, e, f)] == ["a", "b", "c", "d", "e", "f"]

    def test_code_after_await_and_yield_from(self):
        import asyncio

        async def ready():
            return None

        async def build_async(m):
            await ready()
            a = m.wire()
            return a

        def gen():
            yield from ()

        def build_gen(m):
            yield from gen()
            b = m.wire()
            yield b

        assert asyncio.run(build_async(Module("t")))._name == "a"
        assert next(build_gen(Module("t")))._name == "b"


# ---------------------------------------------------------------------------
# Refused: anything but `plain_name = m.<decl>(...)` asks for an explicit name
# ---------------------------------------------------------------------------


class _Obj:
    pass


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


def _unpacking_swap(m):
    other = Module("other")
    c, d = other.wire("c"), other.wire("d")
    a, b = c, d  # fine (no declaration here) ...
    a, b = m.wire(), d  # ... but a declaration inside a tuple is refused
    return a, b


def _bare(m):
    m.wire()


def _expression(m):
    x = m.wire() + 1
    return x


_REFUSED = [
    _tuple,
    _chain,
    _attribute,
    _subscript,
    _walrus,
    _argument,
    _returned,
    _unpacking,
    _unpacking_swap,
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
            TypeError, match=r"m\.wire\(8\): an int is not a name\. .* pass width= by keyword: x = m\.wire\(width=8\)"
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

    def test_helper_called_twice(self):
        def make(m):
            tap = m.wire()
            return tap

        m = Module("t")
        make(m)
        with pytest.raises(ValueError, match=r"Signal 'tap' already declared .* this line runs more than once"):
            make(m)
        assert _names(m) == ["tap"]

    def test_verilog_keyword(self):
        m = Module("t")
        with pytest.raises(ValueError, match=r"the variable name 'output' is a Verilog keyword"):
            output = m.output()  # noqa: F841
        with pytest.raises(ValueError, match=r"'logic' is a Verilog keyword"):
            logic = m.wire()  # noqa: F841
        assert _names(m) == []

    def test_chained_comment_hint(self):
        m = Module("t")
        with pytest.raises(
            ValueError, match=r"goes to \.comment\(\) first \(use x = m\.input\(\.\.\., comment=\.\.\.\)"
        ):
            clk = m.input().comment("System clock")  # noqa: F841
        with pytest.raises(ValueError, match=r"goes to \.attr\(\) first \(use x = m\.reg\(\.\.\., attrs=\{\.\.\.\}\)"):
            state = m.reg(width=3).attr("fsm_encoding", "one_hot")  # noqa: F841
        assert _names(m) == []

    def test_non_cpython(self, monkeypatch):
        monkeypatch.setattr(_varname, "SUPPORTED", False)
        m = Module("t")
        with pytest.raises(ValueError, match="name inference needs CPython"):
            x = m.wire()  # noqa: F841
        assert m.wire("x")._name == "x"
