"""Transform Lark parse trees into Verilog semantic model objects.

This module provides `tree_to_design()` which takes a Lark Tree from
the parser and returns a Design containing Module objects with their
ports, parameters, nets, variables, instances, continuous assigns,
always blocks, and initial blocks.

Phase 1: structural elements (module, ports, params, nets, variables).
Phase 2: instances and continuous assigns.
Phase 3: behavioral (always/initial blocks, statements).
Phase 4: connectivity & analysis (separate module).
Phase 5: functions, tasks, generate constructs.
"""

from __future__ import annotations

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.base import Comment, SourceLocation
from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.design import Design, Module
from ..model.expressions import (
    AssignmentPattern,
    Concatenation,
    Expression,
    Identifier,
    Range,
    Replication,
    TernaryOp,
)
from ..model.functions import FunctionDecl, TaskDecl
from ..model.generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.specify import SpecifyBlock
from ..model.sv_types import EnumMember, EnumType, StructField, StructType, TypedefDecl, UnionType
from ..model.interface import Interface, Modport, ModportPort
from ..model.package import ImportDecl, Package
from ..model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DisableStatement,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from ..model.variables import Variable, VariableKind
from ._assignments import (
    _LvalueCallbacks,
    _build_net_lvalue as _build_net_lvalue_from_tree,
    _build_variable_lvalue as _build_variable_lvalue_from_tree,
    _extract_continuous_assign as _extract_continuous_assign_from_tree,
    _extract_net_assignment as _extract_net_assignment_from_tree,
)
from ._declarations import (
    _build_dimension as _build_dimension_from_tree,
    _build_range as _build_range_from_tree,
    _direction_from_rule,
    _extract_dimension_range as _extract_dimension_range_from_tree,
    _extract_dimensions as _extract_dimensions_from_tree,
    _extract_identifiers,
    _extract_import_declaration,
    _extract_enum_member as _extract_enum_member_from_tree,
    _extract_enum_type as _extract_enum_type_from_tree,
    _extract_net_declaration as _extract_net_declaration_from_tree,
    _extract_net_ids_with_dims as _extract_net_ids_with_dims_from_tree,
    _extract_parameters as _extract_parameters_from_tree,
    _extract_parameter_type as _extract_parameter_type_from_tree,
    _extract_ports_from_declarations as _extract_ports_from_declarations_from_tree,
    _extract_package_import_declaration,
    _extract_port_identifiers_with_dimensions as _extract_port_identifiers_with_dimensions_from_tree,
    _extract_port_names,
    _extract_reg_declaration as _extract_reg_declaration_from_tree,
    _extract_struct_field as _extract_struct_field_from_tree,
    _extract_struct_type as _extract_struct_type_from_tree,
    _extract_sv_type_declaration as _extract_sv_type_declaration_from_tree,
    _extract_typed_variable as _extract_typed_variable_from_tree,
    _extract_typedef_declaration as _extract_typedef_declaration_from_tree,
    _extract_union_type as _extract_union_type_from_tree,
    _parse_enum_base_type as _parse_enum_base_type_from_tree,
)
from ._design_builder import (
    _DesignBuilderCallbacks,
    _ModuleItems,
    _build_design as _build_design_from_tree,
    _build_interface as _build_interface_from_tree,
    _build_module as _build_module_from_tree,
    _build_package as _build_package_from_tree,
    _extract_interface_item as _extract_interface_item_from_tree,
    _extract_modport_declaration as _extract_modport_declaration_from_tree,
    _extract_modport_port as _extract_modport_port_from_tree,
    _extract_module_items as _extract_module_items_from_tree,
    _extract_package_item as _extract_package_item_from_tree,
)
from ._expressions import (
    _ExpressionCallbacks,
    _build_assignment_pattern as _build_assignment_pattern_from_tree,
    _build_concatenation as _build_concatenation_from_tree,
    _build_const_expr_child as _build_const_expr_child_from_tree,
    _build_const_expr_inner as _build_const_expr_inner_from_tree,
    _build_const_function_call as _build_const_function_call_from_tree,
    _build_constant_expression as _build_constant_expression_from_tree,
    _build_constant_primary as _build_constant_primary_from_tree,
    _build_conditional_expression as _build_conditional_expression_from_tree,
    _build_expr_child as _build_expr_child_from_tree,
    _build_expr_inner as _build_expr_inner_from_tree,
    _build_expression as _build_expression_from_tree,
    _build_genvar_expr_child as _build_genvar_expr_child_from_tree,
    _build_genvar_expression as _build_genvar_expression_from_tree,
    _build_genvar_primary as _build_genvar_primary_from_tree,
    _build_function_call as _build_function_call_from_tree,
    _build_hierarchical_identifier,
    _build_inside_open_value_range as _build_inside_open_value_range_from_tree,
    _build_multiple_concatenation as _build_multiple_concatenation_from_tree,
    _build_number,
    _build_primary as _build_primary_from_tree,
    _build_range_select as _build_range_select_from_tree,
    _build_scoped_identifier,
    _build_string_literal,
    _token_to_expression,
    _walk_for_expression as _walk_for_expression_from_tree,
)
from ._functions_tasks import (
    _FunctionTaskCallbacks,
    _extract_block_item_variables as _extract_block_item_variables_from_tree,
    _extract_function_block_items as _extract_function_block_items_from_tree,
    _extract_function_body as _extract_function_body_from_tree,
    _extract_function_declaration as _extract_function_declaration_from_tree,
    _extract_function_range_or_type as _extract_function_range_or_type_from_tree,
    _extract_task_declaration as _extract_task_declaration_from_tree,
    _extract_task_port_type as _extract_task_port_type_from_tree,
    _extract_tf_ports as _extract_tf_ports_from_tree,
)
from ._generate import (
    _GenerateCallbacks,
    _extract_case_generate as _extract_case_generate_from_tree,
    _extract_case_generate_item as _extract_case_generate_item_from_tree,
    _extract_generate_block as _extract_generate_block_from_tree,
    _extract_generate_block_items as _extract_generate_block_items_from_tree,
    _extract_genvar_declaration as _extract_genvar_declaration_from_tree,
    _extract_genvar_init as _extract_genvar_init_from_tree,
    _extract_genvar_iteration as _extract_genvar_iteration_from_tree,
    _extract_if_generate as _extract_if_generate_from_tree,
    _extract_loop_generate as _extract_loop_generate_from_tree,
)
from ._instances import (
    _PrimitiveCallbacks,
    _extract_gate_instantiation as _extract_gate_instantiation_from_tree,
    _extract_module_instantiation as _extract_module_instantiation_from_tree,
    _extract_named_port_connection as _extract_named_port_connection_from_tree,
    _extract_ordered_port_connection as _extract_ordered_port_connection_from_tree,
    _extract_parameter_value_assignment as _extract_parameter_value_assignment_from_tree,
    _extract_port_connections as _extract_port_connections_from_tree,
    _extract_udp_as_instance as _extract_udp_as_instance_from_tree,
)
from ._statements import (
    _StatementCallbacks,
    _build_delay_value as _build_delay_value_from_tree,
    _classify_sensitivity as _classify_sensitivity_from_edges,
    _collect_sensitivity_edges as _collect_sensitivity_edges_from_tree,
    _extract_always_comb_construct as _extract_always_comb_construct_from_tree,
    _extract_always_construct as _extract_always_construct_from_tree,
    _extract_always_ff_construct as _extract_always_ff_construct_from_tree,
    _extract_always_latch_construct as _extract_always_latch_construct_from_tree,
    _extract_blocking_assignment as _extract_blocking_assignment_from_tree,
    _extract_case_item as _extract_case_item_from_tree,
    _extract_case_statement as _extract_case_statement_from_tree,
    _extract_conditional_statement as _extract_conditional_statement_from_tree,
    _extract_delay_control_value as _extract_delay_control_value_from_tree,
    _extract_disable_statement as _extract_disable_statement_from_tree,
    _extract_event_control as _extract_event_control_from_tree,
    _extract_event_trigger as _extract_event_trigger_from_tree,
    _extract_for_loop as _extract_for_loop_from_tree,
    _extract_for_variable_declaration as _extract_for_variable_declaration_from_tree,
    _extract_forever_loop as _extract_forever_loop_from_tree,
    _extract_if_else_if_statement as _extract_if_else_if_statement_from_tree,
    _extract_initial_construct as _extract_initial_construct_from_tree,
    _extract_loop_statement as _extract_loop_statement_from_tree,
    _extract_nonblocking_assignment as _extract_nonblocking_assignment_from_tree,
    _extract_par_block as _extract_par_block_from_tree,
    _extract_procedural_continuous_assignment as _extract_procedural_continuous_assignment_from_tree,
    _extract_procedural_timing_control_statement as _extract_procedural_timing_control_statement_from_tree,
    _extract_repeat_loop as _extract_repeat_loop_from_tree,
    _extract_sensitivity_from_timing_control as _extract_sensitivity_from_timing_control_from_tree,
    _extract_seq_block as _extract_seq_block_from_tree,
    _extract_statement_from_tree as _extract_statement_from_tree_from_tree,
    _extract_system_task_enable as _extract_system_task_enable_from_tree,
    _extract_task_enable as _extract_task_enable_from_tree,
    _extract_variable_assignment as _extract_variable_assignment_from_tree,
    _extract_wait_statement as _extract_wait_statement_from_tree,
    _extract_while_loop as _extract_while_loop_from_tree,
    _unwrap_statement as _unwrap_statement_from_tree,
)
from ._tree_utils import _loc_from_tree
from .comment_extractor import attach_comments


def _expression_callbacks() -> _ExpressionCallbacks:
    return _ExpressionCallbacks(
        build_constant_expression=_build_constant_expression,
        build_expression=_build_expression,
        build_expr_inner=_build_expr_inner,
    )


def _lvalue_callbacks() -> _LvalueCallbacks:
    return _LvalueCallbacks(
        build_constant_expression=_build_constant_expression,
        build_expression=_build_expression,
        build_hierarchical_identifier=_build_hierarchical_identifier,
        build_range=_build_range,
        build_range_select=_build_range_select,
        token_to_expression=_token_to_expression,
    )


def _function_task_callbacks() -> _FunctionTaskCallbacks:
    return _FunctionTaskCallbacks(
        build_range=_build_range,
        build_dimension=_build_dimension,
        build_expression=_build_expression,
        build_scoped_identifier=_build_scoped_identifier,
        extract_identifiers=_extract_identifiers,
        extract_statement=_extract_statement_from_tree,
        unwrap_statement=_unwrap_statement,
    )


def _generate_callbacks() -> _GenerateCallbacks:
    return _GenerateCallbacks(
        build_constant_expression=_build_constant_expression,
        build_genvar_expression=_build_genvar_expression,
        extract_module_items=_extract_module_items,  # type: ignore[arg-type]
    )


def _statement_callbacks() -> _StatementCallbacks:
    return _StatementCallbacks(
        build_expression=_build_expression,
        build_hierarchical_identifier=_build_hierarchical_identifier,
        build_net_lvalue=_build_net_lvalue,
        build_variable_lvalue=_build_variable_lvalue,
        extract_block_item_variables=_extract_block_item_variables,
        extract_net_assignment=_extract_net_assignment,
        token_to_expression=_token_to_expression,
    )


def _design_builder_callbacks() -> _DesignBuilderCallbacks:
    return _DesignBuilderCallbacks(
        extract_parameters=_extract_parameters,
        extract_package_import_declaration=_extract_package_import_declaration,
        extract_ports_from_declarations=_extract_ports_from_declarations,
        extract_port_names=_extract_port_names,
        extract_net_declaration=_extract_net_declaration,
        extract_reg_declaration=_extract_reg_declaration,
        extract_typed_variable=_extract_typed_variable,
        extract_module_instantiation=_extract_module_instantiation,
        extract_udp_as_instance=_extract_udp_as_instance,
        extract_gate_instantiation=_extract_gate_instantiation,
        extract_continuous_assign=_extract_continuous_assign,
        extract_always_construct=_extract_always_construct,
        extract_always_comb_construct=_extract_always_comb_construct,
        extract_always_ff_construct=_extract_always_ff_construct,
        extract_always_latch_construct=_extract_always_latch_construct,
        extract_initial_construct=_extract_initial_construct,
        extract_function_declaration=_extract_function_declaration,
        extract_task_declaration=_extract_task_declaration,
        extract_genvar_declaration=_extract_genvar_declaration,
        extract_loop_generate=_extract_loop_generate,
        extract_if_generate=_extract_if_generate,
        extract_case_generate=_extract_case_generate,
        extract_typedef_declaration=_extract_typedef_declaration,
        extract_import_declaration=_extract_import_declaration,
        extract_sv_type_declaration=_extract_sv_type_declaration,
    )


def tree_to_design(  # noqa: PLR0912  # cm:6d5a2c
    tree: Tree,
    source_file: str | None = None,
    comments: list[Comment] | None = None,
    source_text: str | None = None,
) -> Design:
    """Convert a Lark parse tree to a Design model object.

    Args:
        tree: Lark parse tree (start='module_declaration' or 'verilog').
        source_file: Optional source file path for location tracking.
        comments: Optional list of Comment objects from extract_comments().
            If provided, comments are attached to the nearest model nodes.
        source_text: Optional original Verilog source text. When provided,
            opaque constructs (e.g. specify blocks) can extract their
            verbatim text for faithful round-trip emission.

    Returns:
        Design containing the parsed modules.
    """
    design = _build_design_from_tree(tree, source_file, source_text, _design_builder_callbacks())

    # Attach comments to model nodes if provided
    if comments:
        attach_comments(design, comments)

    return design


def _build_module(tree: Tree, source_file: str | None, source_text: str | None = None) -> Module:  # noqa: PLR0912, PLR0915
    """Build a Module from a module_declaration tree node."""
    return _build_module_from_tree(tree, source_file, source_text, _design_builder_callbacks())


def _build_interface(tree: Tree, source_file: str | None) -> Interface:  # noqa: PLR0912
    """Build an Interface from an interface_declaration tree node."""
    return _build_interface_from_tree(tree, source_file, _design_builder_callbacks())


def _extract_interface_item(  # noqa: PLR0913
    tree: Tree,
    source_file: str | None,
    parameters: list[Parameter],
    nets: list[Net],
    variables: list[Variable],
    continuous_assigns: list[ContinuousAssign],
    modports: list[Modport],
    typedefs: list[TypedefDecl],
    imports: list[ImportDecl] | None = None,
) -> None:
    """Extract declarations from an interface_item node."""
    from ._design_builder import _InterfaceContext

    ctx = _InterfaceContext()
    ctx.parameters = parameters
    ctx.nets = nets
    ctx.variables = variables
    ctx.continuous_assigns = continuous_assigns
    ctx.modports = modports
    ctx.typedefs = typedefs
    ctx.imports = imports if imports is not None else []
    _extract_interface_item_from_tree(tree, source_file, ctx, _design_builder_callbacks())


def _extract_modport_declaration(tree: Tree, source_file: str | None) -> Modport | None:
    """Extract a Modport from a modport_declaration tree node."""
    return _extract_modport_declaration_from_tree(tree, source_file)


def _extract_modport_port(tree: Tree) -> tuple[PortDirection, str]:
    """Extract direction and signal name from a modport_port_declaration node."""
    return _extract_modport_port_from_tree(tree)


def _build_package(tree: Tree, source_file: str | None) -> Package:
    """Build a Package from a package_declaration tree node."""
    return _build_package_from_tree(tree, source_file, _design_builder_callbacks())


def _extract_package_item(  # noqa: PLR0913
    tree: Tree,
    source_file: str | None,
    parameters: list[Parameter],
    typedefs: list[TypedefDecl],
    functions: list[FunctionDecl],
    tasks: list[TaskDecl],
    imports: list[ImportDecl],
) -> None:
    """Extract declarations from a package_item node."""
    from ._design_builder import _PackageContext

    ctx = _PackageContext()
    ctx.parameters = parameters
    ctx.typedefs = typedefs
    ctx.functions = functions
    ctx.tasks = tasks
    ctx.imports = imports
    _extract_package_item_from_tree(tree, source_file, ctx, _design_builder_callbacks())


def _extract_parameters(tree: Tree, source_file: str | None, is_local: bool) -> list[Parameter]:
    """Extract parameters from a parameter port list or declaration."""
    return _extract_parameters_from_tree(tree, source_file, is_local, _build_constant_expression)


def _extract_parameter_type(tree: Tree, source_file: str | None) -> tuple[str | None, Range | None, bool]:
    """Extract shared type metadata from a parameter_type node."""
    return _extract_parameter_type_from_tree(tree, source_file, _build_constant_expression)


def _extract_ports_from_declarations(tree: Tree, source_file: str | None) -> list[Port]:
    """Extract ports from list_of_port_declarations (ANSI-style)."""
    return _extract_ports_from_declarations_from_tree(tree, source_file, _build_constant_expression)


def _extract_port_identifiers_with_dimensions(
    tree: Tree,
    source_file: str | None,
) -> list[tuple[str, list[Range]]]:
    """Extract port identifiers and unpacked dimensions from a port identifier list."""
    return _extract_port_identifiers_with_dimensions_from_tree(tree, source_file, _build_constant_expression)


def _extract_module_items(  # noqa: PLR0912, PLR0915
    tree: Tree,
    source_file: str | None,
    items: _ModuleItems,
    source_text: str | None = None,
) -> None:
    """Extract nets, variables, parameters, instances, and assigns from non_port_module_item nodes."""
    _extract_module_items_from_tree(tree, source_file, items, source_text, _design_builder_callbacks())


def _extract_typedef_declaration(tree: Tree, source_file: str | None) -> TypedefDecl | None:
    """Extract a TypedefDecl from a typedef_declaration tree node."""
    return _extract_typedef_declaration_from_tree(tree, source_file, _build_constant_expression)


def _extract_struct_type(tree: Tree, source_file: str | None) -> StructType:
    """Extract a StructType from a struct_declaration tree node."""
    return _extract_struct_type_from_tree(tree, source_file, _build_constant_expression)


def _extract_union_type(tree: Tree, source_file: str | None) -> UnionType:
    """Extract a UnionType from a union_declaration tree node."""
    return _extract_union_type_from_tree(tree, source_file, _build_constant_expression)


def _extract_struct_field(tree: Tree, source_file: str | None) -> StructField | None:
    """Extract a StructField from a struct_member tree node."""
    return _extract_struct_field_from_tree(tree, source_file, _build_constant_expression)


def _extract_enum_type(tree: Tree, source_file: str | None) -> EnumType:
    """Extract an EnumType from an enum_declaration tree node."""
    return _extract_enum_type_from_tree(tree, source_file, _build_constant_expression)


def _extract_enum_member(tree: Tree, source_file: str | None) -> EnumMember | None:
    """Extract an EnumMember from an enum_name_declaration tree node."""
    return _extract_enum_member_from_tree(tree, source_file, _build_constant_expression)


def _parse_enum_base_type(tree: Tree, source_file: str | None) -> tuple[str | None, Range | None, bool]:
    """Parse an enum_base_type node and return (base_type, width, signed)."""
    return _parse_enum_base_type_from_tree(tree, source_file, _build_constant_expression)


def _extract_net_declaration(tree: Tree, source_file: str | None) -> tuple[list[Net], list[ContinuousAssign]]:
    """Extract nets (and implicit continuous assigns) from a net_declaration subtree.

    In Verilog, ``wire foo = bar;`` is a net-declaration-assignment that
    implicitly creates ``assign foo = bar;``.  We return both the Net
    objects and the implied ContinuousAssign objects.
    """
    return _extract_net_declaration_from_tree(tree, source_file, _build_constant_expression, _build_expression)


def _extract_net_ids_with_dims(tree: Tree, source_file: str | None) -> list[tuple[str, list[Range]]]:
    """Extract (name, dimensions) pairs from a list_of_net_identifiers node."""
    return _extract_net_ids_with_dims_from_tree(tree, source_file, _build_constant_expression)


def _extract_reg_declaration(
    tree: Tree, source_file: str | None, kind: VariableKind = VariableKind.REG
) -> list[Variable]:
    """Extract variables from a reg_declaration (or logic/bit declaration) subtree."""
    return _extract_reg_declaration_from_tree(tree, source_file, kind, _build_constant_expression)


def _extract_typed_variable(tree: Tree, source_file: str | None, kind: VariableKind) -> list[Variable]:
    """Extract variables from integer/real/realtime/time/event declarations."""
    return _extract_typed_variable_from_tree(tree, source_file, kind, _build_constant_expression)


def _extract_sv_type_declaration(tree: Tree, source_file: str | None) -> list[Variable]:
    """Extract variables from sv_type_declaration (user-defined type declarations).

    Grammar:
        sv_type_declaration: IDENTIFIER sv_type_var_id ("," sv_type_var_id)* ";"
            | scoped_identifier sv_type_var_id ("," sv_type_var_id)* ";"
        sv_type_var_id: VARIABLE_IDENTIFIER dimension*
    """
    return _extract_sv_type_declaration_from_tree(tree, source_file, _build_constant_expression)


def _extract_dimension_range(tree: Tree, source_file: str | None) -> Range | None:
    """Extract a Range from a dimension node."""
    return _extract_dimension_range_from_tree(tree, source_file, _build_constant_expression)


def _extract_dimensions(tree: Tree, source_file: str | None) -> list[Range]:
    """Extract array dimensions from a variable_type node."""
    return _extract_dimensions_from_tree(tree, source_file, _build_constant_expression)


# ---- Expression building ----


def _build_range(tree: Tree, source_file: str | None) -> Range | None:
    """Build a Range from a range tree node containing msb/lsb expressions."""
    return _build_range_from_tree(tree, source_file, _build_constant_expression)


def _build_dimension(tree: Tree, source_file: str | None) -> Range | None:
    """Build a Range from a dimension tree node."""
    return _build_dimension_from_tree(tree, source_file, _build_constant_expression)


def _build_constant_expression(tree: Tree, source_file: str | None) -> Expression:
    """Build an Expression from a constant_expression or constant_mintypmax_expression tree."""
    return _build_constant_expression_from_tree(tree, source_file, _expression_callbacks())


def _build_const_expr_inner(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from a constant_expression node.

    Handles: primary, unary, binary operations.
    """
    return _build_const_expr_inner_from_tree(tree, source_file, _expression_callbacks())


def _build_const_expr_child(node: Tree | Token, source_file: str | None) -> Expression:
    """Build expression from a child of constant_expression (which could be Tree or Token)."""
    return _build_const_expr_child_from_tree(node, source_file, _expression_callbacks())


def _build_const_function_call(tree: Tree, source_file: str | None) -> Expression:
    """Build a FunctionCall from constant_system_function_call or constant_function_call."""
    return _build_const_function_call_from_tree(tree, source_file, _expression_callbacks())


def _build_constant_primary(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from a constant_primary node."""
    return _build_constant_primary_from_tree(tree, source_file, _expression_callbacks())


def _walk_for_expression(tree: Tree, source_file: str | None) -> Expression:
    """Fallback: walk a tree and return the first expression-like thing found."""
    return _walk_for_expression_from_tree(tree, source_file, _expression_callbacks())


# ---- Instance extraction ----


def _extract_module_instantiation(tree: Tree, source_file: str | None) -> list[Instance]:
    """Extract Instance objects from a module_instantiation subtree.

    Grammar:
        module_instantiation: MODULE_IDENTIFIER parameter_value_assignment?
                              module_instance ("," module_instance)* ";"
    """
    return _extract_module_instantiation_from_tree(
        tree,
        source_file,
        _build_range,
        _build_expression,
        _token_to_expression,
    )


def _extract_parameter_value_assignment(tree: Tree, source_file: str | None) -> list[ParameterBinding]:
    """Extract parameter bindings from parameter_value_assignment.

    Grammar: "#" "(" list_of_parameter_assignments? ")"
    """
    return _extract_parameter_value_assignment_from_tree(tree, source_file, _build_expression, _token_to_expression)


def _extract_port_connections(tree: Tree, source_file: str | None) -> list[PortConnection]:
    """Extract port connections from list_of_port_connections."""
    return _extract_port_connections_from_tree(tree, source_file, _build_expression)


def _extract_named_port_connection(tree: Tree, source_file: str | None) -> PortConnection:
    """Extract a named port connection: .port_name(expression)"""
    return _extract_named_port_connection_from_tree(tree, source_file, _build_expression)


def _extract_ordered_port_connection(tree: Tree, source_file: str | None) -> PortConnection:
    """Extract an ordered (positional) port connection: expression"""
    return _extract_ordered_port_connection_from_tree(tree, source_file, _build_expression)


def _extract_udp_as_instance(tree: Tree, source_file: str | None) -> list[Instance]:
    """Extract Instance from udp_instantiation (Earley parses ordered-port modules as UDP).

    Grammar:
        udp_instantiation: UDP_IDENTIFIER ... udp_instance ("," udp_instance)* ";"
        udp_instance: name_of_udp_instance "(" output_terminal "," input_terminal ("," input_terminal)* ")"
    """
    return _extract_udp_as_instance_from_tree(
        tree,
        source_file,
        _PrimitiveCallbacks(
            build_range=_build_range,
            build_expression=_build_expression,
            build_net_lvalue=_build_net_lvalue,
            token_to_expression=_token_to_expression,
        ),
    )


def _extract_gate_instantiation(tree: Tree, source_file: str | None) -> list[Instance]:
    """Extract Instance(s) from gate_instantiation (Verilog primitives like and, buf, etc.)."""
    return _extract_gate_instantiation_from_tree(
        tree,
        source_file,
        _PrimitiveCallbacks(
            build_range=_build_range,
            build_expression=_build_expression,
            build_net_lvalue=_build_net_lvalue,
            token_to_expression=_token_to_expression,
        ),
    )


# ---- Continuous assign extraction ----


def _extract_continuous_assign(tree: Tree, source_file: str | None) -> list[ContinuousAssign]:
    """Extract ContinuousAssign objects from a continuous_assign subtree.

    Grammar: continuous_assign: KW_ASSIGN drive_strength? delay3? list_of_net_assignments ";"
    """
    return _extract_continuous_assign_from_tree(tree, source_file, _lvalue_callbacks())


def _extract_net_assignment(tree: Tree, source_file: str | None) -> ContinuousAssign | None:
    """Extract a single ContinuousAssign from net_assignment.

    Grammar: net_assignment: net_lvalue "=" expression
    """
    return _extract_net_assignment_from_tree(tree, source_file, _lvalue_callbacks())


def _build_net_lvalue(tree: Tree, source_file: str | None) -> Expression:
    """Build an Expression from a net_lvalue subtree.

    Handles simple identifiers, bit/range selects, and concatenation lvalues.
    Grammar: net_lvalue: hierarchical_net_identifier constant_range_expression?
           | "{" net_lvalue {"," net_lvalue} "}"
    """
    return _build_net_lvalue_from_tree(tree, source_file, _lvalue_callbacks())


# ---- General expression building ----


def _build_expression(tree: Tree, source_file: str | None) -> Expression:
    """Build an Expression from a general expression tree.

    Handles the full expression grammar (not just constant expressions).
    This is the main entry point for building expressions from parse tree nodes.
    """
    return _build_expression_from_tree(tree, source_file, _expression_callbacks())


def _build_expr_inner(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from an expression node.

    Handles: primary, unary, binary, ternary operations.
    """
    return _build_expr_inner_from_tree(tree, source_file, _expression_callbacks())


def _build_inside_open_value_range(
    lhs: Expression,
    range_child: Tree,
    source_file: str | None,
) -> Expression | None:
    """Lower one ``inside`` open-value-range item into comparisons."""
    return _build_inside_open_value_range_from_tree(lhs, range_child, source_file, _expression_callbacks())


def _build_expr_child(node: Tree | Token, source_file: str | None) -> Expression:
    """Build expression from a child node (Tree or Token)."""
    return _build_expr_child_from_tree(node, source_file, _expression_callbacks())


def _build_primary(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from a primary node.

    A primary can be:
      - number
      - hierarchical_identifier [range_expression | expression]  (bit/range/part select)
      - concatenation
      - multiple_concatenation (replication)
      - mintypmax_expression (parenthesised expression)
      - function_call
    """
    return _build_primary_from_tree(tree, source_file, _expression_callbacks())


def _build_concatenation(tree: Tree, source_file: str | None) -> Concatenation:
    """Build a Concatenation from a concatenation-like tree node."""
    return _build_concatenation_from_tree(tree, source_file, _expression_callbacks())


def _build_assignment_pattern(tree: Tree, source_file: str | None) -> AssignmentPattern:
    """Build an AssignmentPattern from an assignment_pattern tree node.

    Grammar alternatives:
      '{default: expr}
      '{expr, expr, ...}
      '{IDENT: expr, IDENT: expr, ..., default: expr?}
    """
    return _build_assignment_pattern_from_tree(tree, source_file, _expression_callbacks())


def _build_multiple_concatenation(tree: Tree, source_file: str | None) -> Replication:
    """Build a Replication from a multiple_concatenation tree node.

    Grammar: multiple_concatenation ::= { constant_expression concatenation }
    Grammar: constant_multiple_concatenation ::= { constant_expression constant_concatenation }
    """
    return _build_multiple_concatenation_from_tree(tree, source_file, _expression_callbacks())


def _build_conditional_expression(tree: Tree, source_file: str | None) -> TernaryOp:
    """Build a TernaryOp from a conditional_expression tree node.

    Grammar: conditional_expression ::= binary_expression ? expression : expression
    """
    return _build_conditional_expression_from_tree(tree, source_file, _expression_callbacks())


def _build_range_select(base: Expression, range_tree: Tree, source_file: str | None) -> Expression:
    """Build a RangeSelect or PartSelect from a range_expression tree node.

    Grammar: range_expression ::= msb_constant_expression : lsb_constant_expression
              (or part_select variants with +: / -:)
    """
    return _build_range_select_from_tree(base, range_tree, source_file, _expression_callbacks())


def _build_function_call(tree: Tree, source_file: str | None) -> Expression:
    """Build a FunctionCall from a function_call or system_function_call tree."""
    return _build_function_call_from_tree(tree, source_file, _expression_callbacks())


# ---- Behavioral extraction (Phase 3) ----


def _extract_always_construct(tree: Tree, source_file: str | None) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_construct parse tree.

    Parse tree shape:
        always_construct → Token("always") + Tree("statement")
        The statement is usually procedural_timing_control_statement with event_control.
    """
    return _extract_always_construct_from_tree(tree, source_file, _statement_callbacks())


def _extract_always_comb_construct(tree: Tree, source_file: str | None) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_comb_construct parse tree.

    Parse tree shape: always_comb_construct → KW_ALWAYS_COMB + statement
    Implicit sensitivity to all reads — modelled as empty sensitivity list with COMBINATIONAL type.
    """
    return _extract_always_comb_construct_from_tree(tree, source_file, _statement_callbacks())


def _extract_always_ff_construct(tree: Tree, source_file: str | None) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_ff_construct parse tree.

    Parse tree shape: always_ff_construct → KW_ALWAYS_FF "@" "(" event_expression ")" statement
    """
    return _extract_always_ff_construct_from_tree(tree, source_file, _statement_callbacks())


def _extract_always_latch_construct(tree: Tree, source_file: str | None) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_latch_construct parse tree.

    Parse tree shape: always_latch_construct → KW_ALWAYS_LATCH + statement
    Implicit sensitivity to all reads — modelled as empty sensitivity list with LATCH type.
    """
    return _extract_always_latch_construct_from_tree(tree, source_file, _statement_callbacks())


def _extract_initial_construct(tree: Tree, source_file: str | None) -> InitialBlock | None:
    """Extract an InitialBlock from an initial_construct parse tree.

    Parse tree shape:
        initial_construct → Token("initial") + Tree("statement")
    """
    return _extract_initial_construct_from_tree(tree, source_file, _statement_callbacks())


# ---- Phase 5: Function, Task, and Generate extraction ----


def _extract_function_declaration(tree: Tree, source_file: str | None) -> FunctionDecl | None:
    """Extract a FunctionDecl from a function_declaration parse tree.

    Grammar (two forms):
        function [automatic] [function_range_or_type] FUNCTION_IDENTIFIER ;
            function_item_declaration+ function_statement endfunction
        function [automatic] [function_range_or_type] FUNCTION_IDENTIFIER
            ( function_port_list ) ; block_item_declaration* function_statement endfunction
    """
    return _extract_function_declaration_from_tree(tree, source_file, _function_task_callbacks())


def _extract_function_range_or_type(
    tree: Tree,
    source_file: str | None,
) -> tuple[Range | None, str | None]:
    """Extract return range and/or kind from function_range_or_type.

    function_range_or_type: KW_SIGNED? range?
        | KW_INTEGER | KW_REAL | KW_REALTIME | KW_TIME
    """
    return _extract_function_range_or_type_from_tree(tree, source_file, _function_task_callbacks())


def _extract_block_item_variables(tree: Tree, source_file: str | None) -> list[Variable]:
    """Extract variable declarations from a block_item_declaration-like node."""
    return _extract_block_item_variables_from_tree(tree, source_file, _function_task_callbacks())


def _extract_function_block_items(tree: Tree, source_file: str | None) -> tuple[list[Variable], list[BlockingAssign]]:
    """Extract function-local variables plus any initializer statements."""
    return _extract_function_block_items_from_tree(tree, source_file, _function_task_callbacks())


def _extract_function_body(tree: Tree, source_file: str | None, function_name: str) -> Statement | None:
    """Extract the body statement from function_statement.

    function_statement: statement
    """
    return _extract_function_body_from_tree(tree, source_file, function_name, _function_task_callbacks())


def _extract_tf_ports(tree: Tree, source_file: str | None) -> list[Port]:
    """Extract ports from tf_input/output/inout_declaration nodes.

    These appear in function_port_list, function_item_declaration,
    task_port_list, and task_item_declaration.
    """
    return _extract_tf_ports_from_tree(tree, source_file, _function_task_callbacks())


def _extract_task_port_type(tree: Tree) -> str | None:
    """Extract type from task_port_type: KW_INTEGER | KW_REAL | KW_REALTIME | KW_TIME."""
    return _extract_task_port_type_from_tree(tree)


def _extract_task_declaration(tree: Tree, source_file: str | None) -> TaskDecl | None:
    """Extract a TaskDecl from a task_declaration parse tree.

    Grammar (two forms):
        task [automatic] TASK_IDENTIFIER ; task_item_declaration* statement_or_null endtask
        task [automatic] TASK_IDENTIFIER ( [task_port_list] ) ;
            block_item_declaration* statement_or_null endtask
    """
    return _extract_task_declaration_from_tree(tree, source_file, _function_task_callbacks())


def _extract_genvar_declaration(tree: Tree, source_file: str | None) -> GenvarDecl:
    """Extract a GenvarDecl from a genvar_declaration parse tree.

    genvar_declaration: KW_GENVAR list_of_genvar_identifiers ";"
    """
    return _extract_genvar_declaration_from_tree(tree, source_file)


def _extract_loop_generate(tree: Tree, source_file: str | None) -> GenerateFor | None:
    """Extract a GenerateFor from a loop_generate_construct parse tree.

    loop_generate_construct:
        KW_FOR "(" genvar_initialization ";" genvar_expression ";" genvar_iteration ")"
            generate_block
    """
    return _extract_loop_generate_from_tree(tree, source_file, _generate_callbacks())


def _extract_genvar_init(
    tree: Tree,
    source_file: str | None,
) -> tuple[str, Expression | None, bool]:
    """Extract genvar name, initial value, and local flag from genvar_initialization.

    genvar_initialization: KW_GENVAR? GENVAR_IDENTIFIER "=" constant_expression
    """
    return _extract_genvar_init_from_tree(tree, source_file, _generate_callbacks())


def _build_genvar_expression(tree: Tree, source_file: str | None) -> Expression:
    """Build an Expression from a genvar_expression node.

    genvar_expression is structurally similar to constant_expression.
    We reuse the general expression builder since genvar_primary
    contains constant_primary and GENVAR_IDENTIFIER.
    """
    return _build_genvar_expression_from_tree(tree, source_file, _expression_callbacks())


def _build_genvar_expr_child(node: Tree | Token, source_file: str | None) -> Expression:
    """Build expression from a child of genvar_expression."""
    return _build_genvar_expr_child_from_tree(node, source_file, _expression_callbacks())


def _build_genvar_primary(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from genvar_primary: constant_primary | GENVAR_IDENTIFIER."""
    return _build_genvar_primary_from_tree(tree, source_file, _expression_callbacks())


def _extract_genvar_iteration(
    tree: Tree,
    source_file: str | None,
) -> tuple[str, Expression | None]:
    """Extract iteration operator and expression from genvar_iteration.

    genvar_iteration: GENVAR_IDENTIFIER "=" genvar_expression
        | GENVAR_IDENTIFIER OP_INC | GENVAR_IDENTIFIER OP_DEC
        | OP_INC GENVAR_IDENTIFIER | OP_DEC GENVAR_IDENTIFIER
        | GENVAR_IDENTIFIER OP_ADD_ASSIGN genvar_expression
        | GENVAR_IDENTIFIER OP_SUB_ASSIGN genvar_expression
        | GENVAR_IDENTIFIER OP_MUL_ASSIGN genvar_expression
        | GENVAR_IDENTIFIER OP_DIV_ASSIGN genvar_expression
        | GENVAR_IDENTIFIER OP_MOD_ASSIGN genvar_expression

    Returns (update_op, update_expr).
    update_op is one of: "=", "post++", "post--", "pre++", "pre--",
                         "+=", "-=", "*=", "/=", "%="
    update_expr is None for ++/-- forms, the RHS for assignment forms.
    """
    return _extract_genvar_iteration_from_tree(tree, source_file, _generate_callbacks())


def _extract_generate_block(tree: Tree, source_file: str | None) -> GenerateBlock:
    """Extract a GenerateBlock from generate_block or generate_block_or_null.

    generate_block:
        module_or_generate_item
        | KW_BEGIN ( ":" GENERATE_BLOCK_IDENTIFIER )? module_or_generate_item* KW_END
    generate_block_or_null:
        generate_block | ";"
    """
    return _extract_generate_block_from_tree(tree, source_file, _generate_callbacks())


def _extract_generate_block_items(tree: Tree, source_file: str | None) -> list:
    """Extract module items from inside a generate block.

    Generate blocks can contain the same items as module bodies:
    nets, variables, instances, assigns, always, initial, nested generate, etc.
    """
    return _extract_generate_block_items_from_tree(tree, source_file, _generate_callbacks())


def _extract_if_generate(tree: Tree, source_file: str | None) -> GenerateIf | None:
    """Extract a GenerateIf from an if_generate_construct parse tree.

    if_generate_construct:
        KW_IF "(" constant_expression ")" generate_block_or_null
        [ KW_ELSE generate_block_or_null ]
    """
    return _extract_if_generate_from_tree(tree, source_file, _generate_callbacks())


def _extract_case_generate(tree: Tree, source_file: str | None) -> GenerateCase | None:
    """Extract a GenerateCase from a case_generate_construct parse tree.

    case_generate_construct:
        case_qualifier? KW_CASE "(" constant_expression ")" case_generate_item+ KW_ENDCASE
    """
    return _extract_case_generate_from_tree(tree, source_file, _generate_callbacks())


def _extract_case_generate_item(tree: Tree, source_file: str | None) -> GenerateCaseItem | None:
    """Extract a GenerateCaseItem from a case_generate_item parse tree.

    case_generate_item:
        constant_expression ( "," constant_expression )* ":" generate_block_or_null
        | KW_DEFAULT ":"? generate_block_or_null
    """
    return _extract_case_generate_item_from_tree(tree, source_file, _generate_callbacks())


def _extract_sensitivity_from_timing_control(
    tree: Tree,
    source_file: str | None,
) -> list[SensitivityEdge]:
    """Extract sensitivity edges from a procedural_timing_control node.

    Looking for event_control → event_expression children.
    Empty event_control (no children) = @(*) = wildcard.
    """
    return _extract_sensitivity_from_timing_control_from_tree(tree, source_file, _statement_callbacks())


def _extract_event_control(tree: Tree, source_file: str | None) -> list[SensitivityEdge]:
    """Extract sensitivity edges from event_control.

    Shapes:
        @(*) → event_control with NO tree children (empty = wildcard)
        @(posedge clk) → event_control → event_expression → posedge + expression
        @(a or b) → event_control → event_expression → event_expression or event_expression
    """
    return _extract_event_control_from_tree(tree, source_file, _statement_callbacks())


def _collect_sensitivity_edges(
    tree: Tree,
    source_file: str | None,
    edges: list[SensitivityEdge],
) -> None:
    """Recursively collect SensitivityEdge from an event_expression tree.

    event_expression can be:
        - expression  (level-sensitive)
        - posedge expression
        - negedge expression
        - event_expression or/comma event_expression
    """
    _collect_sensitivity_edges_from_tree(tree, source_file, edges, _statement_callbacks())


def _classify_sensitivity(edges: list[SensitivityEdge]) -> SensitivityType:
    """Classify sensitivity type from edge list.

    - Empty list (from @(*)) → COMBINATIONAL
    - All posedge/negedge → SEQUENTIAL
    - All level → COMBINATIONAL
    - Mix → SEQUENTIAL (clock with async reset is still sequential)
    """
    return _classify_sensitivity_from_edges(edges)


# ---- Statement extraction ----


def _unwrap_statement(tree: Tree) -> Tree | None:
    """Unwrap a statement tree to find the inner semantic node.

    statement → one of 14 alternatives (blocking_assignment, seq_block, etc.)
    statement_or_null → statement | ;

    Statements may also contain attribute_instance children (e.g.
    ``(* parallel_case *)``).  These are skipped so we return the actual
    semantic node.
    """
    return _unwrap_statement_from_tree(tree)


def _extract_statement_from_tree(tree: Tree, source_file: str | None) -> Statement | None:  # noqa: PLR0911, PLR0912
    """Extract a Statement from a parse tree node.

    Handles statement, statement_or_null, and direct statement types.
    """
    return _extract_statement_from_tree_from_tree(tree, source_file, _statement_callbacks())


def _extract_blocking_assignment(tree: Tree, source_file: str | None) -> BlockingAssign:
    """Extract BlockingAssign from blocking_assignment tree.

    Tree shape: variable_lvalue + expression
    """
    return _extract_blocking_assignment_from_tree(tree, source_file, _statement_callbacks())


def _extract_nonblocking_assignment(tree: Tree, source_file: str | None) -> NonblockingAssign:
    """Extract NonblockingAssign from nonblocking_assignment tree.

    Tree shape: variable_lvalue + expression
    """
    return _extract_nonblocking_assignment_from_tree(tree, source_file, _statement_callbacks())


def _build_variable_lvalue(tree: Tree, source_file: str | None) -> Expression:
    """Build an Expression from a variable_lvalue subtree.

    Handles hierarchical identifiers, bit selects, range selects, and concatenation.
    Tree shape:
        variable_lvalue → hierarchical_variable_identifier [range_expression]
        variable_lvalue → { variable_lvalue , ... }
    """
    return _build_variable_lvalue_from_tree(tree, source_file, _lvalue_callbacks())


def _extract_seq_block(tree: Tree, source_file: str | None) -> SeqBlock:
    """Extract SeqBlock from seq_block tree.

    Tree shape: begin [: BLOCK_IDENTIFIER] block_item_declaration* statement* end
    """
    return _extract_seq_block_from_tree(tree, source_file, _statement_callbacks())


def _extract_par_block(tree: Tree, source_file: str | None) -> ParBlock:
    """Extract ParBlock from par_block tree.

    Tree shape: fork [: BLOCK_IDENTIFIER] block_item_declaration* statement* join
    """
    return _extract_par_block_from_tree(tree, source_file, _statement_callbacks())


def _extract_conditional_statement(tree: Tree, source_file: str | None) -> IfStatement:
    """Extract IfStatement from conditional_statement tree.

    Tree shape: Token("if") + Tree("expression") + Tree("statement_or_null")
                [+ Token("else") + Tree("statement_or_null")]
    """
    return _extract_conditional_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_if_else_if_statement(tree: Tree, source_file: str | None) -> IfStatement:
    """Extract IfStatement from if_else_if_statement (chained if-else-if).

    Builds nested IfStatement chain.
    """
    return _extract_if_else_if_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_case_statement(tree: Tree, source_file: str | None) -> CaseStatement:
    """Extract CaseStatement from case_statement tree.

    Tree shape: Token("case"/"casex"/"casez") + Tree("expression") + Tree("case_item")* + Token("endcase")
    Note: Earley may parse 'default' as identifier "default" instead of KW_DEFAULT.
    """
    return _extract_case_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_case_item(tree: Tree, source_file: str | None) -> CaseItem:
    """Extract CaseItem from case_item tree.

    Tree shape (normal): expression [, expression]* : statement_or_null
    Tree shape (default): KW_DEFAULT :? statement_or_null
    Note: Earley may parse 'default' as a regular expression/identifier.
    """
    return _extract_case_item_from_tree(tree, source_file, _statement_callbacks())


def _extract_loop_statement(tree: Tree, source_file: str | None) -> Statement:
    """Extract loop statement (for/while/forever/repeat) from loop_statement tree.

    Tree shapes:
        for: Token("for") + variable_assignment + expression + variable_assignment + statement
        while: Token("while") + expression + statement
        forever: Token("forever") + statement
        repeat: Token("repeat") + expression + statement
    """
    return _extract_loop_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_for_loop(tree: Tree, source_file: str | None, loc: SourceLocation) -> ForLoop:
    """Extract ForLoop from loop_statement with 'for' keyword.

    Tree: for + variable_assignment(init) + expression(cond) + variable_assignment(update) + statement(body)
    Also handles SV-style: for + for_variable_declaration(init) + expression(cond) + ...
    """
    return _extract_for_loop_from_tree(tree, source_file, loc, _statement_callbacks())


def _extract_variable_assignment(tree: Tree, source_file: str | None) -> BlockingAssign:
    """Extract a BlockingAssign from a variable_assignment tree.

    Grammar: variable_assignment: variable_lvalue "=" expression
             | variable_lvalue COMPOUND_ASSIGN expression
             | variable_lvalue "++"
             | variable_lvalue "--"
    """
    return _extract_variable_assignment_from_tree(tree, source_file, _statement_callbacks())


def _extract_for_variable_declaration(tree: Tree, source_file: str | None) -> BlockingAssign:
    """Extract a BlockingAssign from a for_variable_declaration tree.

    Grammar: for_variable_declaration: KW_INT KW_UNSIGNED? VARIABLE_IDENTIFIER "=" expression
             | KW_INTEGER VARIABLE_IDENTIFIER "=" expression
             | KW_LOGIC KW_SIGNED? range? VARIABLE_IDENTIFIER "=" expression
    Treats as a simple assignment (loop variable = init_value).
    """
    return _extract_for_variable_declaration_from_tree(tree, source_file, _statement_callbacks())


def _extract_while_loop(tree: Tree, source_file: str | None, loc: SourceLocation) -> WhileLoop:
    """Extract WhileLoop from loop_statement with 'while'."""
    return _extract_while_loop_from_tree(tree, source_file, loc, _statement_callbacks())


def _extract_forever_loop(tree: Tree, source_file: str | None, loc: SourceLocation) -> ForeverLoop:
    """Extract ForeverLoop from loop_statement with 'forever'."""
    return _extract_forever_loop_from_tree(tree, source_file, loc, _statement_callbacks())


def _extract_repeat_loop(tree: Tree, source_file: str | None, loc: SourceLocation) -> RepeatLoop:
    """Extract RepeatLoop from loop_statement with 'repeat'."""
    return _extract_repeat_loop_from_tree(tree, source_file, loc, _statement_callbacks())


def _extract_system_task_enable(tree: Tree, source_file: str | None) -> SystemTaskCall:
    """Extract SystemTaskCall from system_task_enable tree.

    Tree shape: SYSTEM_TASK_IDENTIFIER [expression*]
    """
    return _extract_system_task_enable_from_tree(tree, source_file, _statement_callbacks())


def _extract_task_enable(tree: Tree, source_file: str | None) -> TaskEnable:
    """Extract TaskEnable from task_enable tree.

    Tree shape: hierarchical_task_identifier [expression*]
    """
    return _extract_task_enable_from_tree(tree, source_file, _statement_callbacks())


def _extract_disable_statement(tree: Tree, source_file: str | None) -> DisableStatement:
    """Extract DisableStatement from disable_statement tree."""
    return _extract_disable_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_event_trigger(tree: Tree, source_file: str | None) -> EventTrigger:
    """Extract EventTrigger from event_trigger tree: -> event_name;"""
    return _extract_event_trigger_from_tree(tree, source_file, _statement_callbacks())


def _extract_wait_statement(tree: Tree, source_file: str | None) -> WaitStatement:
    """Extract WaitStatement from wait_statement tree.

    Tree shape: Token("wait") + expression + statement_or_null
    """
    return _extract_wait_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_procedural_timing_control_statement(
    tree: Tree,
    source_file: str | None,
) -> Statement:
    """Extract statement with timing control (delay or event).

    Tree shape: procedural_timing_control + statement_or_null
    procedural_timing_control → delay_control | event_control
    """
    return _extract_procedural_timing_control_statement_from_tree(tree, source_file, _statement_callbacks())


def _extract_delay_control_value(tree: Tree, source_file: str | None) -> Expression:
    """Extract the delay expression from a delay_control tree.

    Tree shape: delay_value → unsigned_number | ...
    Or: "(" mintypmax_expression ")"
    """
    return _extract_delay_control_value_from_tree(tree, source_file, _statement_callbacks())


def _build_delay_value(tree: Tree, source_file: str | None) -> Expression:
    """Build expression from a delay_value node."""
    return _build_delay_value_from_tree(tree, source_file, _statement_callbacks())


def _extract_procedural_continuous_assignment(tree: Tree, source_file: str | None) -> BlockingAssign:
    """Extract from procedural_continuous_assignments: assign/deassign/force/release.

    For now, model these as blocking assignments with the keyword as part of context.
    """
    return _extract_procedural_continuous_assignment_from_tree(tree, source_file, _statement_callbacks())
