"""Focused regressions for typed SystemVerilog model extraction.

These tests isolate the typed constructs exercised by the upstream-style
``stream_xbar_typed`` import so parser/model regressions are caught without
depending only on the example-level simulation harness.
"""

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, BitSelect, Identifier, Literal
from veriforge.sim.elaborate import _build_struct_env, _resolve_typedef_widths
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str) -> Module:
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="typed_sv_regression.sv")
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


def _get_port(module: Module, name: str):
    return next(port for port in module.ports if port.name == name)


def _get_variable(module: Module, name: str):
    return next(var for var in module.variables if var.name == name)


def _identifier_name(expr: Identifier) -> str:
    if expr.hierarchy:
        return ".".join([*expr.hierarchy, expr.name])
    return expr.name


PARAM_TYPE_SOURCE = """
module typed_ports #(
    parameter int unsigned NumInp = 3,
    parameter type payload_t = logic [7:0]
) (
    input payload_t [NumInp-1:0] data_i
);
endmodule
"""


TYPED_MODEL_SOURCE = """
module typed_model #(
    parameter int unsigned NumInp = 3,
    parameter int unsigned NumOut = 2
) (
    input payload_t [NumInp-1:0] data_i,
    output idx_t [NumOut-1:0] idx_o
);
    typedef logic [7:0] payload_t;
    typedef logic [1:0] idx_t;
    typedef struct packed {
        payload_t data;
        idx_t idx;
    } spill_data_t;

    logic     [NumInp-1:0][NumOut-1:0] inp_valid;
    logic     [NumInp-1:0] arb_req_i;
    payload_t [NumInp-1:0] arb_data_i;
    spill_data_t arb;

    assign arb_req_i[0] = inp_valid[0][1];
    assign arb_data_i[0] = data_i[0];
    assign arb.idx = idx_o[0];
    assign arb.data = data_i[1];
endmodule
"""


def test_parameter_type_port_dimension_is_preserved(parser):
    module = _parse_module(parser, PARAM_TYPE_SOURCE)

    assert [param.name for param in module.parameters] == ["NumInp", "payload_t"]

    port = _get_port(module, "data_i")
    assert port.data_type == "payload_t"
    assert port.width is None
    assert len(port.dimensions) == 1
    assert isinstance(port.dimensions[0].msb, BinaryOp)
    assert port.dimensions[0].msb.op == "-"
    assert isinstance(port.dimensions[0].lsb, Literal)
    assert int(port.dimensions[0].lsb.value) == 0


def test_typed_locals_and_ports_resolve_typedef_widths(parser):
    module = _parse_module(parser, TYPED_MODEL_SOURCE)
    _resolve_typedef_widths(module)

    data_i = _get_port(module, "data_i")
    idx_o = _get_port(module, "idx_o")
    arb_data_i = _get_variable(module, "arb_data_i")
    arb = _get_variable(module, "arb")

    assert data_i.width is not None
    assert int(data_i.width.msb.value) == 7
    assert int(data_i.width.lsb.value) == 0

    assert idx_o.width is not None
    assert int(idx_o.width.msb.value) == 1
    assert int(idx_o.width.lsb.value) == 0

    assert arb_data_i.type_name == "payload_t"
    assert arb_data_i.width is not None
    assert int(arb_data_i.width.msb.value) == 7
    assert int(arb_data_i.width.lsb.value) == 0
    assert len(arb_data_i.dimensions) == 1

    assert arb.type_name == "spill_data_t"
    assert arb.width is not None
    assert int(arb.width.msb.value) == 9
    assert int(arb.width.lsb.value) == 0


def test_struct_layout_for_typed_local_is_built(parser):
    module = _parse_module(parser, TYPED_MODEL_SOURCE)
    _resolve_typedef_widths(module)

    type_map, signal_map = _build_struct_env(module)

    assert "spill_data_t" in type_map
    assert "arb" in signal_map
    assert signal_map["arb"].total_width == 10
    assert signal_map["arb"].fields == {"idx": (0, 2), "data": (2, 8)}


def test_nested_index_and_struct_field_assignments_survive_model_extraction(parser):
    module = _parse_module(parser, TYPED_MODEL_SOURCE)

    nested_assign = next(
        assign
        for assign in module.continuous_assigns
        if isinstance(assign.lhs, BitSelect)
        and isinstance(assign.lhs.target, Identifier)
        and assign.lhs.target.name == "arb_req_i"
    )
    assert isinstance(nested_assign, ContinuousAssign)
    assert isinstance(nested_assign.rhs, BitSelect)
    assert isinstance(nested_assign.rhs.target, BitSelect)
    assert isinstance(nested_assign.rhs.target.target, Identifier)
    assert nested_assign.rhs.target.target.name == "inp_valid"
    assert isinstance(nested_assign.rhs.target.index, Literal)
    assert int(nested_assign.rhs.target.index.value) == 0
    assert isinstance(nested_assign.rhs.index, Literal)
    assert int(nested_assign.rhs.index.value) == 1

    field_targets = {
        _identifier_name(assign.lhs) for assign in module.continuous_assigns if isinstance(assign.lhs, Identifier)
    }
    assert "arb.idx" in field_targets
    assert "arb.data" in field_targets
