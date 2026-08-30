"""Tests for parameterized port widths across hierarchy levels.

Validates that parameter-dependent signal widths (e.g. `input [Width-1:0] data`)
are correctly resolved during elaboration so that all engines produce the
right bit-widths at runtime.
"""

import shutil

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range, UnaryOp
from veriforge.model.instances import Instance, ParameterBinding, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    IfStatement,
    NonblockingAssign,
    SensitivityEdge,
)
from veriforge.model.sv_types import StructField, StructType, TypedefDecl
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


# ── Helper ──────────────────────────────────────────────────────────


def _w(n: int) -> Range:
    return Range(Literal(n - 1), Literal(0))


def _param_width(param_name: str) -> Range:
    """Create Range(param_name - 1 : 0) — a parameter-dependent width."""
    return Range(BinaryOp("-", Identifier(param_name), Literal(1)), Literal(0))


# ── Module builders ─────────────────────────────────────────────────


def _make_param_passthrough() -> Module:
    """module pass_through #(parameter Width = 1) (
        input [Width-1:0] data_i,
        output [Width-1:0] data_o
    );
    assign data_o = data_i;
    endmodule
    """
    return Module(
        "pass_through",
        parameters=[Parameter("Width", default_value=Literal(1))],
        ports=[
            Port("data_i", PortDirection.INPUT, width=_param_width("Width")),
            Port("data_o", PortDirection.OUTPUT, width=_param_width("Width")),
        ],
        nets=[
            Net("data_i", NetKind.WIRE, width=_param_width("Width")),
            Net("data_o", NetKind.WIRE, width=_param_width("Width")),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("data_o"), Identifier("data_i")),
        ],
    )


def _make_top_with_param_instance(override_width: int = 16) -> tuple[Module, Design]:
    """Top module that instantiates pass_through with Width=override_width.

    module top(input clk, input [override_width-1:0] in_data, output [override_width-1:0] out_data);
        pass_through #(.Width(override_width)) u_pt (.data_i(in_data), .data_o(out_data));
    endmodule
    """
    sub = _make_param_passthrough()
    w = _w(override_width)

    top = Module(
        "top",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("in_data", PortDirection.INPUT, width=w),
            Port("out_data", PortDirection.OUTPUT, width=w),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("in_data", NetKind.WIRE, width=w),
            Net("out_data", NetKind.WIRE, width=w),
        ],
        instances=[
            Instance(
                "pass_through",
                "u_pt",
                port_connections=[
                    PortConnection(port_name="data_i", expression=Identifier("in_data"), is_named=True),
                    PortConnection(port_name="data_o", expression=Identifier("out_data"), is_named=True),
                ],
                parameter_bindings=[
                    ParameterBinding(name="Width", value=Literal(override_width)),
                ],
            ),
        ],
    )

    design = Design(modules=[top, sub])
    link_instances(design)
    resolve_port_connections(design)
    return top, design


def _make_two_level_param() -> tuple[Module, Design]:
    """Two levels of parameterized hierarchy.

    module inner #(parameter W = 1) (input [W-1:0] d, output [W-1:0] q);
        assign q = d;
    endmodule

    module middle #(parameter DataW = 1) (input [DataW-1:0] din, output [DataW-1:0] dout);
        inner #(.W(DataW)) u_inner (.d(din), .q(dout));
    endmodule

    module top(...);
        middle #(.DataW(32)) u_mid (.din(in_val), .dout(out_val));
    endmodule
    """
    inner = Module(
        "inner",
        parameters=[Parameter("W", default_value=Literal(1))],
        ports=[
            Port("d", PortDirection.INPUT, width=_param_width("W")),
            Port("q", PortDirection.OUTPUT, width=_param_width("W")),
        ],
        nets=[
            Net("d", NetKind.WIRE, width=_param_width("W")),
            Net("q", NetKind.WIRE, width=_param_width("W")),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("q"), Identifier("d")),
        ],
    )

    middle = Module(
        "middle",
        parameters=[Parameter("DataW", default_value=Literal(1))],
        ports=[
            Port("din", PortDirection.INPUT, width=_param_width("DataW")),
            Port("dout", PortDirection.OUTPUT, width=_param_width("DataW")),
        ],
        nets=[
            Net("din", NetKind.WIRE, width=_param_width("DataW")),
            Net("dout", NetKind.WIRE, width=_param_width("DataW")),
        ],
        instances=[
            Instance(
                "inner",
                "u_inner",
                port_connections=[
                    PortConnection(port_name="d", expression=Identifier("din"), is_named=True),
                    PortConnection(port_name="q", expression=Identifier("dout"), is_named=True),
                ],
                parameter_bindings=[
                    ParameterBinding(name="W", value=Identifier("DataW")),
                ],
            ),
        ],
    )

    top = Module(
        "top",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("in_val", PortDirection.INPUT, width=_w(32)),
            Port("out_val", PortDirection.OUTPUT, width=_w(32)),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("in_val", NetKind.WIRE, width=_w(32)),
            Net("out_val", NetKind.WIRE, width=_w(32)),
        ],
        instances=[
            Instance(
                "middle",
                "u_mid",
                port_connections=[
                    PortConnection(port_name="din", expression=Identifier("in_val"), is_named=True),
                    PortConnection(port_name="dout", expression=Identifier("out_val"), is_named=True),
                ],
                parameter_bindings=[
                    ParameterBinding(name="DataW", value=Literal(32)),
                ],
            ),
        ],
    )

    design = Design(modules=[top, middle, inner])
    link_instances(design)
    resolve_port_connections(design)
    return top, design


# ── Tests ────────────────────────────────────────────────────────────


class TestParamWidthElaboration:
    """Verify that parameterized widths resolve to concrete values during elaboration."""

    def test_single_level_param_width(self):
        """Port width [Width-1:0] with Width=16 must produce concrete Range(15, 0)."""
        top, design = _make_top_with_param_instance(16)
        flat = flatten_module(top, design=design)

        # Find the prefixed net for u_pt.data_i
        data_i = next((n for n in flat.nets if n.name == "u_pt.data_i"), None)
        assert data_i is not None, "u_pt.data_i not found in flattened nets"
        # The width should be resolved to concrete Literal values
        assert data_i.width is not None
        assert isinstance(data_i.width.msb, Literal), (
            f"Expected Literal msb, got {type(data_i.width.msb).__name__}: {data_i.width.msb}"
        )
        assert isinstance(data_i.width.lsb, Literal), (
            f"Expected Literal lsb, got {type(data_i.width.lsb).__name__}: {data_i.width.lsb}"
        )
        assert int(data_i.width.msb.value) == 15
        assert int(data_i.width.lsb.value) == 0

    def test_two_level_param_width(self):
        """Width must resolve through two levels: top→middle→inner with DataW=32, W=DataW."""
        top, design = _make_two_level_param()
        flat = flatten_module(top, design=design)

        # Find inner's signal through the hierarchy
        d_sig = next((n for n in flat.nets if n.name == "u_mid.u_inner.d"), None)
        assert d_sig is not None, "u_mid.u_inner.d not found in flattened nets"
        assert isinstance(d_sig.width.msb, Literal), f"Expected Literal msb, got {type(d_sig.width.msb).__name__}"
        assert int(d_sig.width.msb.value) == 31

    def test_param_width_multiple_overrides(self):
        """Different instances of the same module with different Width overrides."""
        sub = _make_param_passthrough()
        top = Module(
            "top",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("narrow_in", PortDirection.INPUT, width=_w(4)),
                Port("narrow_out", PortDirection.OUTPUT, width=_w(4)),
                Port("wide_in", PortDirection.INPUT, width=_w(32)),
                Port("wide_out", PortDirection.OUTPUT, width=_w(32)),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("narrow_in", NetKind.WIRE, width=_w(4)),
                Net("narrow_out", NetKind.WIRE, width=_w(4)),
                Net("wide_in", NetKind.WIRE, width=_w(32)),
                Net("wide_out", NetKind.WIRE, width=_w(32)),
            ],
            instances=[
                Instance(
                    "pass_through",
                    "u_narrow",
                    port_connections=[
                        PortConnection(port_name="data_i", expression=Identifier("narrow_in"), is_named=True),
                        PortConnection(port_name="data_o", expression=Identifier("narrow_out"), is_named=True),
                    ],
                    parameter_bindings=[ParameterBinding(name="Width", value=Literal(4))],
                ),
                Instance(
                    "pass_through",
                    "u_wide",
                    port_connections=[
                        PortConnection(port_name="data_i", expression=Identifier("wide_in"), is_named=True),
                        PortConnection(port_name="data_o", expression=Identifier("wide_out"), is_named=True),
                    ],
                    parameter_bindings=[ParameterBinding(name="Width", value=Literal(32))],
                ),
            ],
        )
        design = Design(modules=[top, sub])
        link_instances(design)
        resolve_port_connections(design)
        flat = flatten_module(top, design=design)

        narrow = next(n for n in flat.nets if n.name == "u_narrow.data_i")
        wide = next(n for n in flat.nets if n.name == "u_wide.data_i")
        assert isinstance(narrow.width.msb, Literal)
        assert int(narrow.width.msb.value) == 3
        assert isinstance(wide.width.msb, Literal)
        assert int(wide.width.msb.value) == 31


class TestParamWidthSimulation:
    """Verify that parameterized widths produce correct simulation results."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_param_passthrough_value(self, engine, tmp_path):
        """A 16-bit signal should pass all 16 bits through a Width=16 instance."""
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")

        top, design = _make_top_with_param_instance(16)

        sim = Simulator(top, engine=engine, design=design)
        sim.drive("in_data", Value(0xABCD, width=16))
        sim.run(max_time=0)

        assert sim.read("in_data") == Value(0xABCD, width=16)
        assert sim.read("out_data") == Value(0xABCD, width=16)

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_two_level_param_value(self, engine, tmp_path):
        """32-bit value must survive two levels of parameterized hierarchy."""
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")

        top, design = _make_two_level_param()

        sim = Simulator(top, engine=engine, design=design)
        sim.drive("in_val", Value(0xDEADBEEF, width=32))
        sim.run(max_time=0)

        assert sim.read("out_val") == Value(0xDEADBEEF, width=32)

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_multiple_param_widths(self, engine, tmp_path):
        """Two instances with Width=4 and Width=32 must both work correctly."""
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")

        sub = _make_param_passthrough()
        top = Module(
            "top",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("narrow_in", PortDirection.INPUT, width=_w(4)),
                Port("narrow_out", PortDirection.OUTPUT, width=_w(4)),
                Port("wide_in", PortDirection.INPUT, width=_w(32)),
                Port("wide_out", PortDirection.OUTPUT, width=_w(32)),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("narrow_in", NetKind.WIRE, width=_w(4)),
                Net("narrow_out", NetKind.WIRE, width=_w(4)),
                Net("wide_in", NetKind.WIRE, width=_w(32)),
                Net("wide_out", NetKind.WIRE, width=_w(32)),
            ],
            instances=[
                Instance(
                    "pass_through",
                    "u_narrow",
                    port_connections=[
                        PortConnection(port_name="data_i", expression=Identifier("narrow_in"), is_named=True),
                        PortConnection(port_name="data_o", expression=Identifier("narrow_out"), is_named=True),
                    ],
                    parameter_bindings=[ParameterBinding(name="Width", value=Literal(4))],
                ),
                Instance(
                    "pass_through",
                    "u_wide",
                    port_connections=[
                        PortConnection(port_name="data_i", expression=Identifier("wide_in"), is_named=True),
                        PortConnection(port_name="data_o", expression=Identifier("wide_out"), is_named=True),
                    ],
                    parameter_bindings=[ParameterBinding(name="Width", value=Literal(32))],
                ),
            ],
        )
        design = Design(modules=[top, sub])
        link_instances(design)
        resolve_port_connections(design)

        sim = Simulator(top, engine=engine, design=design)
        sim.drive("narrow_in", Value(0xF, width=4))
        sim.drive("wide_in", Value(0xCAFEBABE, width=32))
        sim.run(max_time=0)

        assert sim.read("narrow_out") == Value(0xF, width=4)
        assert sim.read("wide_out") == Value(0xCAFEBABE, width=32)


# ── Struct through parameterized port ────────────────────────────────


def _make_csr_like_wrapper() -> Module:
    """A simplified ibex_csr-like module: parameterized width pass-through with register.

    module csr_reg #(parameter Width = 1) (
        input              clk_i,
        input              rst_ni,
        input  [Width-1:0] wr_data_i,
        input              wr_en_i,
        output [Width-1:0] rd_data_o
    );
        reg [Width-1:0] rdata_q;
        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) rdata_q <= 0;
            else if (wr_en_i) rdata_q <= wr_data_i;
        end
        assign rd_data_o = rdata_q;
    endmodule
    """
    pw = _param_width("Width")
    mod = Module(
        "csr_reg",
        parameters=[Parameter("Width", default_value=Literal(1))],
        ports=[
            Port("clk_i", PortDirection.INPUT),
            Port("rst_ni", PortDirection.INPUT),
            Port("wr_data_i", PortDirection.INPUT, width=_param_width("Width")),
            Port("wr_en_i", PortDirection.INPUT),
            Port("rd_data_o", PortDirection.OUTPUT, width=_param_width("Width")),
        ],
        nets=[
            Net("clk_i", NetKind.WIRE),
            Net("rst_ni", NetKind.WIRE),
            Net("wr_data_i", NetKind.WIRE, width=_param_width("Width")),
            Net("wr_en_i", NetKind.WIRE),
            Net("rd_data_o", NetKind.WIRE, width=_param_width("Width")),
        ],
        variables=[
            Variable("rdata_q", VariableKind.REG, width=_param_width("Width")),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("rd_data_o"), Identifier("rdata_q")),
        ],
    )
    mod.always_blocks = [
        AlwaysBlock(
            IfStatement(
                UnaryOp("!", Identifier("rst_ni")),
                NonblockingAssign(
                    Identifier("rdata_q"),
                    Literal(0),
                ),
                IfStatement(
                    Identifier("wr_en_i"),
                    NonblockingAssign(
                        Identifier("rdata_q"),
                        Identifier("wr_data_i"),
                    ),
                ),
            ),
            sensitivity_list=[
                SensitivityEdge("posedge", Identifier("clk_i")),
                SensitivityEdge("negedge", Identifier("rst_ni")),
            ],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        ),
    ]
    return mod


def _make_struct_through_csr() -> tuple[Module, Design]:
    """Top module with a struct variable that passes through a parameterized CSR wrapper.

    Mirrors the ibex pattern: cs_registers has a struct dcsr_d that is written
    to a csr_reg #(.Width(32)) instance via wr_data_i, and read back via rd_data_o.

    typedef struct packed {
        logic [3:0] xdebugver;  // bits [31:28]
        logic [11:0] reserved;  // bits [27:16]
        logic        ebreakm;   // bit  [15]
        logic [14:0] lower;     // bits [14:0]
    } dcsr_t;

    module top(input clk, input rst_n, output [31:0] csr_out);
        dcsr_t dcsr_d;
        assign dcsr_d = 32'h40000003;  // xdebugver=4, lower[1:0]=3
        csr_reg #(.Width(32)) u_dcsr (.clk_i(clk), .rst_ni(rst_n),
                                       .wr_data_i(dcsr_d), .wr_en_i(1'b1),
                                       .rd_data_o(csr_out));
    endmodule
    """
    csr = _make_csr_like_wrapper()

    # typedef dcsr_t: packed struct, total 32 bits
    dcsr_struct = StructType(
        [
            StructField("xdebugver", "logic", width=_w(4)),  # bits [31:28]
            StructField("reserved", "logic", width=_w(12)),  # bits [27:16]
            StructField("ebreakm", "logic"),  # bit  [15]
            StructField("lower", "logic", width=_w(15)),  # bits [14:0]
        ],
        packed=True,
    )
    dcsr_typedef = TypedefDecl("dcsr_t", struct_type=dcsr_struct)

    top = Module(
        "top",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst_n", PortDirection.INPUT),
            Port("csr_out", PortDirection.OUTPUT, width=_w(32)),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst_n", NetKind.WIRE),
            Net("csr_out", NetKind.WIRE, width=_w(32)),
        ],
        variables=[
            Variable("dcsr_d", VariableKind.REG, width=_w(32), type_name="dcsr_t"),
        ],
        continuous_assigns=[
            # dcsr_d = 0x40000003 (xdebugver=4 at [31:28], lower[1:0]=3)
            ContinuousAssign(Identifier("dcsr_d"), Literal(0x40000003)),
        ],
        instances=[
            Instance(
                "csr_reg",
                "u_dcsr",
                port_connections=[
                    PortConnection(port_name="clk_i", expression=Identifier("clk"), is_named=True),
                    PortConnection(port_name="rst_ni", expression=Identifier("rst_n"), is_named=True),
                    PortConnection(port_name="wr_data_i", expression=Identifier("dcsr_d"), is_named=True),
                    PortConnection(port_name="wr_en_i", expression=Literal(1), is_named=True),
                    PortConnection(port_name="rd_data_o", expression=Identifier("csr_out"), is_named=True),
                ],
                parameter_bindings=[
                    ParameterBinding(name="Width", value=Literal(32)),
                ],
            ),
        ],
    )
    top.typedefs = [dcsr_typedef]

    design = Design(modules=[top, csr])
    link_instances(design)
    resolve_port_connections(design)
    return top, design


class TestStructThroughParamPort:
    """Verify struct values pass correctly through parameterized-width ports."""

    def test_elaboration_width(self):
        """csr_reg #(.Width(32)) ports must have concrete width=32 after flatten."""
        top, design = _make_struct_through_csr()
        flat = flatten_module(top, design=design)

        wr_data = next(n for n in flat.nets if n.name == "u_dcsr.wr_data_i")
        rd_data = next(n for n in flat.nets if n.name == "u_dcsr.rd_data_o")
        rdata_q = next(v for v in flat.variables if v.name == "u_dcsr.rdata_q")

        assert isinstance(wr_data.width.msb, Literal)
        assert int(wr_data.width.msb.value) == 31
        assert isinstance(rd_data.width.msb, Literal)
        assert int(rd_data.width.msb.value) == 31
        assert isinstance(rdata_q.width.msb, Literal)
        assert int(rdata_q.width.msb.value) == 31

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_struct_value_through_csr(self, engine):
        """0x40000003 must survive: struct var → param port → register → output."""
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")

        top, design = _make_struct_through_csr()

        sim = Simulator(top, engine=engine, design=design)
        sim.drive("rst_n", Value(0, width=1))
        sim.drive("clk", Value(0, width=1))
        sim.run(max_time=0)

        # Release reset
        sim.drive("rst_n", Value(1, width=1))

        # Clock posedge — should latch wr_data_i (= dcsr_d = 0x40000003)
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.run(max_time=15)  # One full clock cycle + setup

        assert sim.read("csr_out") == Value(0x40000003, width=32)


# ── Signed relational comparison against a localparam, through hierarchy ──
# Regression: `_apply_param_overrides` (in elaborate.py) rebuilt a flattened
# child instance's Parameter nodes without carrying over `signed`/`width`,
# so a `signed=True` localparam silently became unsigned once its owning
# module was instantiated as a child of a composed Design. Per IEEE
# 1364-2005 Table 5-22, a comparison is only evaluated as signed when BOTH
# operands are signed -- one unsigned operand forces the whole comparison
# unsigned, which flips a negative register's two's-complement bit pattern
# into a huge unsigned value. `==`/`!=` don't care about signedness (bit
# patterns compare equal either way), so only `>`/`<`/`>=` were affected.


def _build_signed_cmp_leaf_dsl(name: str, op: str):
    """acc is an 18-bit signed reg that free-runs `acc <= acc - 5` from 0, so
    it only ever goes negative. BOUND is a signed 32-bit localparam of
    32767. `ovf = acc <op> BOUND` must therefore be constant across every
    cycle: always false for `>`/`>=`, always true for `<`."""
    from veriforge.dsl import Module as DslModule, mux, posedge  # noqa: PLC0415

    m = DslModule(name)
    clk = m.input("clk")
    rst = m.input("rst")
    o_val = m.output_reg("o_val", width=16, signed=True)
    acc = m.reg("acc", width=18, signed=True, init=0)
    bound = m.localparam("BOUND", value=32767, width=32, signed=True)

    ovf = m.wire("ovf")
    if op == ">":
        ovf.assign = acc > bound
    elif op == "<":
        ovf.assign = acc < bound
    else:
        ovf.assign = acc >= bound

    with m.always(posedge(clk)):
        with m.if_(rst):
            acc <<= 0
            o_val <<= 0
        with m.else_():
            acc <<= acc - 5
            o_val <<= mux(ovf, 111, acc[15:0])

    return m.build()


def _build_signed_cmp_wrapper_dsl(name: str, leaf_name: str):
    """Trivial pass-through parent: instantiates `leaf_name`, no logic of its own."""
    from veriforge.dsl import Module as DslModule  # noqa: PLC0415

    m = DslModule(name)
    clk = m.input("clk")
    rst = m.input("rst")
    o_val = m.wire("o_val", width=16, signed=True)
    o_val_out = m.output("o_val_out", width=16, signed=True)
    o_val_out.assign = o_val
    m.instance(leaf_name, "u_leaf", ports={"clk": clk, "rst": rst, "o_val": o_val})
    return m.build()


def _run_signed_cmp(sim, out_name: str, n: int = 8) -> list[int]:
    """Reset, then step `n` clock cycles, returning `out_name` as signed 16-bit ints."""

    def s16(v: Value) -> int:
        raw = int(v) & 0xFFFF
        return raw - 65536 if raw >= 32768 else raw

    def step():
        sim.drive("clk", 1)
        sim.settle()
        sim.drive("clk", 0)
        sim.settle()

    sim.drive("clk", 0)
    sim.drive("rst", 1)
    step()
    sim.drive("rst", 0)

    vals = []
    for _ in range(n):
        step()
        vals.append(s16(sim.read(out_name)))
    return vals


class TestSignedComparisonThroughHierarchy:
    """`>`/`<`/`>=` against a signed localparam must agree standalone vs. as
    a Design child -- see the module-level comment above for the bug this
    guards against."""

    @pytest.mark.parametrize("op", [">", "<", ">="])
    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_signed_localparam_compare_standalone_matches_flattened(self, engine, op):
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")
        tag = {">": "gt", "<": "lt", ">=": "ge"}[op]

        leaf = _build_signed_cmp_leaf_dsl(f"leaf_{tag}_{engine}", op)
        sim_standalone = Simulator(leaf, engine=engine)
        standalone = _run_signed_cmp(sim_standalone, "o_val")

        leaf_c = _build_signed_cmp_leaf_dsl(f"leaf_{tag}_{engine}_c", op)
        wrapper = _build_signed_cmp_wrapper_dsl(f"wrapper_{tag}_{engine}", f"leaf_{tag}_{engine}_c")
        design = Design(modules=[wrapper, leaf_c])
        sim_composed = Simulator(wrapper, engine=engine, design=design)
        composed = _run_signed_cmp(sim_composed, "o_val_out")

        # acc only ever decreases from 0, so the comparison's outcome must
        # be constant across every cycle: never the 111 sentinel for `>`/
        # `>=`, always the sentinel for `<`.
        expected_always_true = op == "<"
        for label, vals in (("standalone", standalone), ("composed", composed)):
            for cycle, v in enumerate(vals):
                is_sentinel = v == 111
                assert is_sentinel == expected_always_true, (
                    f"[{engine}, op={op}, {label}] cycle {cycle}: got {v}, "
                    f"expected {'111 (sentinel)' if expected_always_true else 'acc passthrough'}"
                )
        assert standalone == composed, f"[{engine}, op={op}] standalone={standalone} != composed={composed}"


class TestSignedParameterSignExtension:
    """`parameter signed [msb:lsb] name = value;` must sign-extend correctly
    when read into a wider signal, on every engine.

    Regression for a real bug found via the grammar-driven fuzzer's own
    parameter generation (`notes/roadmap.md`): `_extract_parameters`
    (`transforms/_declarations.py`) only populated `Parameter.signed`/
    `.width` from a `parameter_type` wrapper node -- but
    `parameter_declaration`'s own grammar rule (`KW_PARAMETER KW_SIGNED?
    range? ...`) puts `KW_SIGNED`/`range` directly as this node's own
    children, with no such wrapper. Every plain `signed`/ranged parameter
    silently got `signed=False`, `width=None` regardless of its own
    declaration, so a parameter whose value's own top bit was set (>= half
    its declared range) zero-extended instead of sign-extended when read
    into a wider signal -- on all four simulation engines identically
    (confirmed via direct inspection that they all share this one upstream
    parser gap, not four independent engine bugs). This class builds the
    module from real Verilog *text* (unlike the rest of this file, which
    constructs `Parameter` objects directly in Python and so never
    exercised the parser at all) specifically to catch a regression in the
    parser step itself.
    """

    @staticmethod
    def _build(msb_value: int) -> tuple[Module, Design]:
        """A parameter whose value's own top bit (at its declared width)
        is set -- must sign-extend, not zero-extend, when copied to the
        wider output below.
        """
        from veriforge.transforms.tree_to_model import tree_to_design
        from veriforge.verilog_parser import verilog_parser

        src = f"""
        module signed_param_sign_ext #(
            parameter signed [63:0] P = 64'sd{msb_value}
        ) (output signed [67:0] o);
            assign o = P;
        endmodule
        """
        vp = verilog_parser(start="source_text")
        tree = vp.build_tree(src)
        design = tree_to_design(tree, source_file="signed_param_sign_ext.v")
        top = next(m for m in design.modules if m.name == "signed_param_sign_ext")
        return top, design

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast", "compiled"])
    def test_large_value_sign_extends_not_zero_extends(self, engine):
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")
        if engine == "vm-fast":
            pytest.importorskip("Cython")

        # 2**63 has bit 63 set -- a negative 64-bit two's-complement value.
        top, design = self._build(2**63)
        sim = Simulator(top, engine=engine, design=design)
        sim.settle()

        got = sim.read("o")
        # Correct: sign-extend -- top 4 bits of the 68-bit result are 1s,
        # matching Icarus Verilog's behavior for the identical module
        # (confirmed directly; this was the oracle disagreement that found
        # the bug). Wrong (the bug): top 4 bits come back 0.
        expected = Value((2**63) | (0xF << 64), width=68)
        assert got == expected, f"[{engine}] expected sign-extended {expected}, got {got}"

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast", "compiled"])
    def test_small_value_still_zero_extends(self, engine):
        """A small positive value (top bit clear) must still zero-extend --
        this isn't "always sign-extend", it's "sign-extend based on the
        value's own top bit", so a small positive signed parameter must
        NOT come back with its upper bits incorrectly set to 1.
        """
        if engine == "compiled" and not _has_compiler:
            pytest.skip("No C compiler available")
        if engine == "vm-fast":
            pytest.importorskip("Cython")

        top, design = self._build(5)
        sim = Simulator(top, engine=engine, design=design)
        sim.settle()

        assert sim.read("o") == Value(5, width=68)
