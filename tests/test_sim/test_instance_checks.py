"""Instance port connections, parameter overrides and defparam, checked against Icarus.

Each of these used to give a wrong answer with no error on all four engines:

- positional port connections (``child u1 (a, y);``) were all dropped, leaving
  every port undriven: ``parse_file()`` never ran ``resolve_port_connections()``,
  the only place positions were mapped to ports;
- ``.a`` (SV for ``.a(a)``) parsed as ``.a()``, unconnected;
- extra positional ports and a port connected twice were accepted;
- ``#(...)`` overrides could name an unknown parameter or a localparam, and
  positional ones counted localparams and body parameters (which a ``#(...)``
  list makes local, IEEE 1800 6.20.1);
- ``defparam`` was parsed and dropped, so ``veriforge format`` deleted it too.

Expected values come from Icarus (``iverilog -g2012``) on the same source.
"""

from __future__ import annotations

import logging

import pytest

from veriforge.codegen import emit_module
from veriforge.codegen.verilog_formatter import format_module
from veriforge.convert.to_dsl import module_to_dsl
from veriforge.dsl import Module as DslModule
from veriforge.model.design import Design
from veriforge.project import parse_file
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_CHILD = "module child #(parameter W = 4) (input [7:0] a, output [7:0] y); assign y = a + W; endmodule\n"
_MID = "module mid #(parameter M = 0) (input [7:0] a, output [7:0] y); child u2 (.a(a), .y(y)); endmodule\n"


def _parse(tmp_path, body: str, extra: str = _CHILD) -> Design:
    path = tmp_path / "dut.sv"
    path.write_text(f"{extra}module top (input [7:0] a, output [7:0] y);\n{body}\nendmodule\n")
    return parse_file(path)


def _y(design: Design, engine: str, a: int = 1):
    sim = Simulator(design.get_module("top"), engine=engine, design=design)
    sim.drive("a", a)
    sim.run(max_time=1)
    return sim.read("y")


def _flatten(design: Design):
    return flatten_module(design.get_module("top"), design=design)


class TestPortConnections:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_positional(self, engine, tmp_path):
        assert _y(_parse(tmp_path, "child u1 (a, y);"), engine) == 5

    @pytest.mark.parametrize("engine", ENGINES)
    def test_implicit_named(self, engine, tmp_path):
        assert _y(_parse(tmp_path, "child u1 (.a, .y);"), engine) == 5

    def test_implicit_named_parses_as_same_name(self, tmp_path):
        conns = _parse(tmp_path, "child u1 (.a, .y());").get_module("top").instances[0].port_connections
        assert [(c.port_name, getattr(c.expression, "name", None)) for c in conns] == [("a", "a"), ("y", None)]

    @pytest.mark.parametrize("body", ["child u1 (a, );", "child u1 (, );"])
    def test_positional_empty_leaves_port_unconnected(self, body, tmp_path):
        assert _y(_parse(tmp_path, body), "reference").is_x

    def test_empty_parens_on_module_without_ports(self, tmp_path):
        design = _parse(tmp_path, "np u0 (); assign y = a;", "module np; endmodule\n")
        assert _y(design, "reference") == 1

    @pytest.mark.parametrize("body", ["wire [7:0] z; child u1 (a, y, z);", "child u1 (a, y, );"])
    def test_too_many_positional(self, body, tmp_path):
        # Icarus: "Wrong number of ports. Expecting at most 2, got 3."
        with pytest.raises(
            ValueError, match=r"Instance 'u1' has 3 positional port connections, but module 'child' has 2 port\(s\)"
        ):
            _flatten(_parse(tmp_path, body))

    def test_connected_twice(self, tmp_path):
        # Icarus: "port ``a'' already bound."
        with pytest.raises(ValueError, match="Instance 'u1' connects port 'a' twice"):
            _flatten(_parse(tmp_path, "child u1 (.a(a), .y(y), .a(a));"))


class TestParameterOverrides:
    _LOCAL_FIRST = "module child (input [7:0] a, output [7:0] y); localparam L = 2; parameter W = 4; assign y = a + W + L; endmodule\n"
    _BODY_PARAM = "module child #(parameter W = 4) (input [7:0] a, output [7:0] y); parameter B = 2; assign y = a + W + B; endmodule\n"

    def test_unknown_name(self, tmp_path):
        # Icarus: "parameter `WW` not found in `top.u1`."
        with pytest.raises(
            ValueError, match=r"overrides parameter 'WW', which module 'child' doesn't have \(its parameters: W\)"
        ):
            _flatten(_parse(tmp_path, "child #(.WW(9)) u1 (.a(a), .y(y));"))

    def test_localparam_name(self, tmp_path):
        # Icarus: "Cannot override localparam"
        with pytest.raises(ValueError, match="overrides 'L', which is local to module 'child'"):
            _flatten(_parse(tmp_path, "child #(.L(10)) u1 (.a(a), .y(y));", self._LOCAL_FIRST))

    def test_body_parameter_with_header_list_is_local(self, tmp_path):
        params = _parse(tmp_path, "", self._BODY_PARAM).get_module("child").parameters
        assert [(p.name, p.is_local) for p in params] == [("W", False), ("B", True)]
        with pytest.raises(ValueError, match="overrides 'B', which is local to module 'child'"):
            _flatten(_parse(tmp_path, "child #(.B(20)) u1 (.a(a), .y(y));", self._BODY_PARAM))

    def test_body_parameter_without_header_list_stays_overridable(self, tmp_path):
        params = _parse(tmp_path, "", self._LOCAL_FIRST).get_module("child").parameters
        assert [(p.name, p.is_local) for p in params] == [("L", True), ("W", False)]

    @pytest.mark.parametrize("engine", ENGINES)
    def test_positional_skips_localparam(self, engine, tmp_path):
        # W = 10, L = 2: Icarus prints 13 (it was 15: the value went to L).
        assert _y(_parse(tmp_path, "child #(10) u1 (.a(a), .y(y));", self._LOCAL_FIRST), engine) == 13

    def test_positional_extra_values_ignored_with_warning(self, tmp_path, caplog):
        # Icarus: "warning: ignoring 1 extra parameter override(s)"; W = 10, B = 2 -> 13 (it was 31).
        design = _parse(tmp_path, "child #(10, 20) u1 (.a(a), .y(y));", self._BODY_PARAM)
        with caplog.at_level(logging.WARNING, logger="veriforge.sim.elaborate"):
            assert _y(design, "reference") == 13
        assert [r.getMessage() for r in caplog.records].count(
            "Instance 'u1': ignoring 1 extra parameter value(s); module 'child' has 1 overridable parameter(s)"
        ) == 1

    def test_named_twice(self, tmp_path):
        with pytest.raises(ValueError, match="overrides parameter 'W' twice"):
            _flatten(_parse(tmp_path, "child #(.W(1), .W(2)) u1 (.a(a), .y(y));"))

    def test_dsl_typo(self):
        with DslModule("child") as c:
            w = c.parameter(default=4)
            a = c.input(width=8)
            y = c.output(width=8)
            c.assign(y, a + w)
        with DslModule("top") as t:
            a = t.input(width=8)
            y = t.output(width=8)
            t.instance("child", "u1", ports={"a": a, "y": y}, parameters={"WDITH": 9})
        top = t.build()
        with pytest.raises(ValueError, match="overrides parameter 'WDITH'"):
            flatten_module(top, design=Design(modules=[top, c.build()]))


class TestDefparam:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("defparam u1.W = 10; child u1 (.a(a), .y(y));", 11),
            # defparam beats #(...)
            ("defparam u1.W = 10; child #(.W(20)) u1 (.a(a), .y(y));", 11),
            # the last of two wins
            ("defparam u1.W = 10, u1.W = 30; child u1 (.a(a), .y(y));", 31),
            # the value is evaluated where the defparam is
            ("localparam K = 6; defparam u1.W = K + 1; child u1 (.a(a), .y(y));", 8),
            # upward reference from the module's own name
            ("defparam top.u1.W = 10; child u1 (.a(a), .y(y));", 11),
        ],
    )
    @pytest.mark.parametrize("engine", ENGINES)
    def test_direct(self, engine, body, expected, tmp_path):
        assert _y(_parse(tmp_path, body), engine) == expected

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("defparam u1.u2.W = 10; mid u1 (.a(a), .y(y));", 11),
            ("mid #(.M(1)) u1 (.a(a), .y(y)); defparam u1.u2.W = 2 * 7;", 15),
        ],
    )
    @pytest.mark.parametrize("engine", ENGINES)
    def test_deeper(self, engine, body, expected, tmp_path):
        assert _y(_parse(tmp_path, body, _CHILD + _MID), engine) == expected

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            (
                "genvar i; for (i = 0; i < 1; i = i + 1) begin : g defparam u1.W = 10 + i; child u1 (.a(a), .y(y)); end",
                11,
            ),
            (
                "genvar i; for (i = 0; i < 1; i = i + 1) begin : g child u1 (.a(a), .y(y)); end defparam g[0].u1.W = 12;",
                13,
            ),
        ],
    )
    @pytest.mark.parametrize("engine", ENGINES)
    def test_generate(self, engine, body, expected, tmp_path):
        assert _y(_parse(tmp_path, body), engine) == expected

    def test_unknown_parameter(self, tmp_path):
        with pytest.raises(
            ValueError, match=r"defparam u1.WW: module 'child' has no parameter 'WW' \(its parameters: W\)"
        ):
            _flatten(_parse(tmp_path, "defparam u1.WW = 9; child u1 (.a(a), .y(y));"))

    def test_localparam(self, tmp_path):
        leaf = "module child (input [7:0] a, output [7:0] y); localparam L = 1; assign y = a + L; endmodule\n"
        with pytest.raises(ValueError, match=r"defparam u1\.L: 'L' is local to module 'child'"):
            _flatten(_parse(tmp_path, "defparam u1.L = 10; child u1 (.a(a), .y(y));", leaf))

    @pytest.mark.parametrize(
        ("body", "path"),
        [
            ("defparam u9.W = 3; child u1 (.a(a), .y(y));", "u9.W"),
            ("defparam u1.u9.W = 3; mid u1 (.a(a), .y(y));", "u9.W"),
            ("defparam W2 = 3; assign y = a;", "W2"),
        ],
    )
    def test_unreachable_path(self, body, path, tmp_path):
        with pytest.raises(ValueError, match=rf"defparam {path} in module '\w+' doesn't reach a parameter"):
            _flatten(_parse(tmp_path, body, _CHILD + _MID))

    def test_emit_and_format_keep_it(self, tmp_path):
        top = _parse(tmp_path, "child u1 (.a(a), .y(y));\ndefparam u1.W = 4 + 6;").get_module("top")
        assert "defparam u1.W = 4 + 6;" in emit_module(top)
        assert "defparam u1.W = 4 + 6;" in format_module(top)

    def test_emit_inside_generate(self, tmp_path):
        body = "genvar i; for (i = 0; i < 1; i = i + 1) begin : g defparam u1.W = 10; child u1 (.a(a), .y(y)); end"
        assert "defparam u1.W = 10;" in emit_module(_parse(tmp_path, body).get_module("top"))

    def test_to_dsl_refuses(self, tmp_path):
        top = _parse(tmp_path, "child u1 (.a(a), .y(y));\ndefparam u1.W = 10;").get_module("top")
        assert 'unsupported("defparam u1.W")' in module_to_dsl(top)


def test_body_parameter_emitted_as_localparam(tmp_path):
    # The header holds every non-local parameter, so emitting B as `parameter`
    # there would have made it overridable.
    child = _parse(tmp_path, "", TestParameterOverrides._BODY_PARAM).get_module("child")
    text = emit_module(child)
    assert "localparam B = 2;" in text
    assert "#(parameter W = 4)" in text
