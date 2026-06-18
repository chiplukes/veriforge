"""Hierarchy elaboration: flatten module instances into a single module.

Given a top-level module with submodule instances, this pass produces a
single flat module where:
  - All submodule signals are renamed with a hierarchical prefix
    (e.g., ``u1.clk``, ``u1.count``)
  - Port connections become continuous assigns bridging parent ↔ child signals
  - Submodule always/initial blocks and continuous assigns are deep-copied,
    with identifiers renamed to use the hierarchical prefix
  - Recursion handles arbitrary depth of hierarchy

The flat module can then be simulated by any engine (reference, VM, compiled)
without engine-level hierarchy awareness.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Iterable

from ..model.assignments import ContinuousAssign
from ..model.base import VerilogNode
from ..model.behavioral import AlwaysBlock, InitialBlock
from ..model.design import Design, Module
from ..model.functions import FunctionDecl
from ..model.expressions import (
    AssignmentPattern,
    BitSelect,
    BinaryOp,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    Replication,
    Range,
    RangeSelect,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ..model.generate import GenerateCase, GenerateFor, GenerateIf, GenvarDecl
from ..model.instances import Instance
from ..model.nets import Net, NetKind
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import BlockingAssign, ForLoop, IfStatement, ParBlock, SeqBlock
from ..model.sv_types import TypedefDecl
from ..model.variables import Variable, VariableKind

SYNTH_LOCAL_LOOP_PREFIX = "__vt_local_for_"
SYNTH_LOCAL_BLOCK_PREFIX = "__vt_local_blk_"


def check_signed_declarations(module: Module) -> None:
    """Log declared-signed nets, variables, ports, and parameters at debug level.

    Signedness propagation is implemented (item 10 step 2); this function now
    serves as a diagnostic aid rather than a warning.
    """
    import logging

    _log = logging.getLogger(__name__)
    signed_signals: list[str] = []

    for net in module.nets:
        if net.signed:
            signed_signals.append(f"net '{net.name}'")

    for var in module.variables:
        if var.signed:
            signed_signals.append(f"variable '{var.name}'")

    for port in module.ports:
        if port.signed:
            signed_signals.append(f"port '{port.name}'")

    for param in module.parameters:
        if param.signed:
            signed_signals.append(f"parameter '{param.name}'")

    if not signed_signals:
        return

    signal_list = ", ".join(signed_signals)
    _log.debug(
        "Module '%s' has declared-signed signals: %s.",
        module.name,
        signal_list,
    )


def is_synthesized_local_name(name: str) -> bool:
    """Return True for synthesized process-local loop or block variables."""
    return name.startswith((SYNTH_LOCAL_LOOP_PREFIX, SYNTH_LOCAL_BLOCK_PREFIX))


def materialize_process_locals(module: Module) -> Module:
    """Create unique module-level temporaries for declared process-local loop vars.

    SystemVerilog loop headers can declare a process-local iterator
    (for example ``for (int i = 0; ...)``). The simulator currently models
    runtime state as module signals, so these locals need unique synthesized
    names before elaboration to avoid collisions across instances/processes.
    """
    existing_names = _submodule_signal_names(module)
    counter = 0

    for idx, block in enumerate(module.always_blocks):
        counter = _materialize_stmt_process_locals(module, block.body, f"ab{idx}", existing_names, counter)

    for idx, block in enumerate(module.initial_blocks):
        counter = _materialize_stmt_process_locals(module, block.body, f"ib{idx}", existing_names, counter)

    return module


def _materialize_stmt_process_locals(
    module: Module,
    node: VerilogNode | None,
    scope_tag: str,
    existing_names: set[str],
    counter: int,
    scope_stack: tuple[dict[str, str], ...] = (),
) -> int:
    if node is None:
        return counter

    if isinstance(node, Identifier):
        if node.hierarchy is None:
            renamed = _lookup_local_name(scope_stack, node.name)
            if renamed is not None:
                node.name = renamed
        return counter

    if isinstance(node, ForLoop):
        child_scope = scope_stack
        if node.declares_var and isinstance(node.init.lhs, Identifier):
            original_name = node.init.lhs.name
            unique_name, counter = _allocate_local_loop_name(scope_tag, original_name, existing_names, counter)
            module.variables.append(Variable(unique_name, VariableKind.INT, signed=node.signed_var))
            existing_names.add(unique_name)
            child_scope = (*scope_stack, {original_name: unique_name})
            node.declares_var = False

        for child in (node.init, node.condition, node.update, node.body):
            counter = _materialize_stmt_process_locals(module, child, scope_tag, existing_names, counter, child_scope)
        return counter

    if isinstance(node, (SeqBlock, ParBlock)):
        block_scope: dict[str, str] = {}
        init_statements: list[BlockingAssign] = []

        for local_var in node.local_vars:
            unique_name, counter = _allocate_local_block_name(scope_tag, local_var.name, existing_names, counter)
            block_scope[local_var.name] = unique_name
            materialized_var = copy.deepcopy(local_var)
            materialized_var.name = unique_name
            materialized_var.initial_value = None
            module.variables.append(materialized_var)
            existing_names.add(unique_name)

            if local_var.initial_value is not None:
                init_stmt = BlockingAssign(
                    lhs=Identifier(unique_name, loc=local_var.loc),
                    rhs=copy.deepcopy(local_var.initial_value),
                    loc=local_var.loc,
                )
                counter = _materialize_stmt_process_locals(
                    module,
                    init_stmt.rhs,
                    scope_tag,
                    existing_names,
                    counter,
                    (*scope_stack, block_scope),
                )
                init_statements.append(init_stmt)

        node.local_vars = []
        if init_statements:
            node.statements = [*init_statements, *node.statements]

        child_scope = (*scope_stack, block_scope) if block_scope else scope_stack
        for child in node.statements:
            counter = _materialize_stmt_process_locals(module, child, scope_tag, existing_names, counter, child_scope)
        return counter

    if isinstance(node, VerilogNode):
        for child in node._child_nodes():
            counter = _materialize_stmt_process_locals(module, child, scope_tag, existing_names, counter, scope_stack)

    return counter


def _allocate_local_loop_name(
    scope_tag: str,
    original_name: str,
    existing_names: set[str],
    counter: int,
) -> tuple[str, int]:
    next_counter = counter
    while True:
        candidate = f"{SYNTH_LOCAL_LOOP_PREFIX}{scope_tag}_{next_counter}_{original_name}"
        next_counter += 1
        if candidate not in existing_names:
            return candidate, next_counter


def _allocate_local_block_name(
    scope_tag: str,
    original_name: str,
    existing_names: set[str],
    counter: int,
) -> tuple[str, int]:
    next_counter = counter
    while True:
        candidate = f"{SYNTH_LOCAL_BLOCK_PREFIX}{scope_tag}_{next_counter}_{original_name}"
        next_counter += 1
        if candidate not in existing_names:
            return candidate, next_counter


def _lookup_local_name(scope_stack: tuple[dict[str, str], ...], name: str) -> str | None:
    for scope in reversed(scope_stack):
        renamed = scope.get(name)
        if renamed is not None:
            return renamed
    return None


@dataclass(frozen=True)
class StructLayout:
    """Layout information for a packed struct type.

    Attributes:
        name: Typedef name (e.g. ``"bus_t"``).
        total_width: Total bit-width of the struct.
        fields: Mapping of field_name -> (bit_offset, bit_width).
    """

    name: str
    total_width: int
    fields: dict[str, tuple[int, int]] = field(default_factory=dict)


def match_assignment_pattern_layout(
    pattern: AssignmentPattern,
    struct_type_map: dict[str, StructLayout],
) -> StructLayout | None:
    """Find the packed struct layout that matches an assignment pattern's named fields."""
    if not pattern.named_pairs:
        return None
    field_names = {name for name, _ in pattern.named_pairs}
    exact_match = None
    superset_match = None
    for layout in struct_type_map.values():
        layout_fields = set(layout.fields)
        if layout_fields == field_names:
            exact_match = layout
            break
        if field_names <= layout_fields:
            if superset_match is None or len(layout_fields) < len(superset_match.fields):
                superset_match = layout
    return exact_match or superset_match


def resolve_struct_access(
    name: str,
    struct_signal_types: dict[str, StructLayout],
    signal_map: dict[str, object],
) -> tuple[str, int, int] | None:
    """Resolve nested struct access to a real base signal plus cumulative bit slice."""
    parts = name.split(".")
    for index in range(len(parts) - 1, 0, -1):
        base_name = ".".join(parts[:index])
        if base_name not in signal_map:
            continue
        current_name = base_name
        total_offset = 0
        field_width = 0
        for field_name in parts[index:]:
            layout = struct_signal_types.get(current_name)
            if layout is None:
                break
            field_info = layout.fields.get(field_name)
            if field_info is None:
                break
            offset, field_width = field_info
            total_offset += offset
            current_name = f"{current_name}.{field_name}"
        else:
            return base_name, total_offset, field_width
    return None


def _split_memory_element_name(name: str) -> tuple[str, str] | None:
    """Split ``mem[idx]`` into ``(mem, idx_text)``."""
    if not name.endswith("]"):
        return None
    bracket = name.rfind("[")
    if bracket < 0:
        return None
    index_text = name[bracket + 1 : -1]
    if not index_text:
        return None
    return name[:bracket], index_text.strip()


def normalize_struct_access_name(name: str) -> str:
    """Strip an unpacked-array index from only the storage segment of a dotted name."""
    parts = name.split(".")
    if not parts:
        return name
    bracket = parts[-1].find("[")
    if bracket >= 0:
        parts[-1] = parts[-1][:bracket]
    return ".".join(parts)


def resolve_struct_storage_access(
    name: str,
    struct_signal_types: dict[str, StructLayout],
    signal_map: dict[str, object],
    memory_names: set[str],
) -> tuple[str, int | str | None, int, int] | None:
    """Resolve nested struct access to a signal base or memory element plus slice."""
    parts = name.split(".")
    for index in range(len(parts) - 1, 0, -1):
        base_name = ".".join(parts[:index])
        storage_name: str | None = None
        storage_index: int | str | None = None
        if base_name in signal_map:
            storage_name = base_name
        else:
            mem_info = _split_memory_element_name(base_name)
            if mem_info is not None and mem_info[0] in memory_names:
                storage_name, storage_index_text = mem_info
                try:
                    storage_index = int(storage_index_text, 0)
                except ValueError:
                    storage_index = storage_index_text
        if storage_name is None:
            continue
        current_name = normalize_struct_access_name(base_name)
        total_offset = 0
        field_width = 0
        for field_name in parts[index:]:
            layout = struct_signal_types.get(current_name)
            if layout is None:
                break
            field_info = layout.fields.get(field_name)
            if field_info is None:
                break
            offset, field_width = field_info
            total_offset += offset
            current_name = f"{current_name}.{field_name}"
        else:
            return storage_name, storage_index, total_offset, field_width
    return None


def flatten_module(top: Module, design: Design | None = None) -> Module:  # cm:9f4b2d
    """Flatten a module hierarchy into a single module with no instances.

    Args:
        top: The top-level module (may contain instances).
        design: Optional design context for resolving module names.
            If *None*, instances must have ``resolved_module`` already set
            (e.g. via :func:`~veriforge.analysis.resolver.link_instances`).

    Returns:
        A new :class:`Module` with all hierarchy inlined.  If *top* has no
        instances the original module is returned unchanged.

    Raises:
        ValueError: If a referenced module cannot be resolved.
    """
    # Resolve SV package imports so that package-level parameters and
    # typedefs are available during generate elaboration.
    if design is not None:
        resolve_sv_imports(top, design)

    # Elaborate generate constructs first
    if top.generate_blocks:
        top = elaborate_generates(top)
        _resolve_typedef_widths(top)
        _resolve_parameterized_widths(top)

    has_interfaces = bool(getattr(top, "interface_instances", None))

    if not top.instances and not has_interfaces:
        return top

    module_map: dict[str, Module] = {}
    if design is not None:
        module_map = {m.name: m for m in design.modules}
    _collect_resolved(top, module_map)

    flat = Module(top.name, ports=list(top.ports))
    flat.nets = list(top.nets)
    flat.variables = list(top.variables)
    flat.parameters = list(top.parameters)
    flat.continuous_assigns = list(top.continuous_assigns)
    flat.always_blocks = list(top.always_blocks)
    flat.initial_blocks = list(top.initial_blocks)
    flat.functions = list(top.functions)
    flat.tasks = list(top.tasks)
    flat.typedefs = list(top.typedefs)
    flat.imports = list(top.imports)

    # Resolve package imports on the flat module so that _build_param_env
    # can see imported typedefs (needed for $bits(struct_type) in param
    # override expressions when inlining child instances).
    if design is not None:
        resolve_sv_imports(flat, design)

    hierarchy_map: dict[str, str] = {}
    if top.instances:
        _inline_instances(flat, top.instances, "", module_map, hierarchy_map, design)

    # Flatten interface instances — create prefixed signals and continuous assigns
    if has_interfaces:
        _flatten_interface_instances(flat, top.interface_instances)

    flat.hierarchy_map = hierarchy_map

    return flat


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_resolved(module: Module, module_map: dict[str, Module]) -> None:
    """Walk instances and collect resolved_module references into *module_map*."""
    for inst in module.instances:
        if inst.resolved_module is not None and inst.module_name not in module_map:
            module_map[inst.module_name] = inst.resolved_module
            _collect_resolved(inst.resolved_module, module_map)


def _inline_instances(
    flat: Module,
    instances: list[Instance],
    parent_prefix: str,
    module_map: dict[str, Module],
    hierarchy_map: dict[str, str],
    design: Design | None = None,
) -> None:
    """Recursively inline instances into *flat*."""
    for inst in instances:
        sub = inst.resolved_module or module_map.get(inst.module_name)
        if sub is None:
            raise ValueError(
                f"Cannot resolve module '{inst.module_name}' for instance "
                f"'{inst.instance_name}'. Call link_instances() first or "
                f"pass a Design to flatten_module()."
            )

        # Build hierarchical prefix (e.g. "u1" or "u1.u2")
        prefix = f"{parent_prefix}{inst.instance_name}" if parent_prefix else inst.instance_name

        # Record instance path → module name mapping
        hierarchy_map[prefix] = inst.module_name

        # Resolve SV package imports before elaborating generates
        if design is not None:
            resolve_sv_imports(sub, design)

        # Apply parameter overrides to submodule BEFORE recursive flatten/generates
        # so that downstream modules see the correct parameter values.
        if inst.parameter_bindings:
            sub = _pre_apply_param_overrides(flat, inst, sub)

        # Resolve typedef-based port/signal widths only after instance parameter
        # overrides have been applied; pre-resolving raw default-zero parameterized
        # modules can poison typedef-backed signal widths (e.g. [-1:0]).
        _resolve_typedef_widths(sub)

        # Resolve parameterized widths/dimensions to concrete Literal values
        # now that parameter defaults are set.  This must happen BEFORE
        # _create_prefixed_signals copies widths into the flat module.
        _resolve_parameterized_widths(sub)

        # Elaborate generates in submodule
        if sub.generate_blocks:
            sub = elaborate_generates(sub)
            _resolve_typedef_widths(sub)
            # Resolve widths on newly promoted variables/nets from generate blocks
            _resolve_parameterized_widths(sub)

        # Recursively flatten the submodule first if it has its own instances
        if sub.instances:
            sub = flatten_module(sub, design=design)
            # Re-populate module_map with the resolved references already collected
            _collect_resolved(sub, module_map)
            # Merge sub-hierarchy entries under the current prefix
            for sub_path, sub_mod in sub.hierarchy_map.items():
                hierarchy_map[f"{prefix}.{sub_path}"] = sub_mod

        # Gather the set of signal names declared in the submodule
        sub_signals = _submodule_signal_names(sub)

        # --- Create prefixed signals for the submodule ---
        _create_prefixed_signals(flat, sub, prefix)

        # --- Apply parameter overrides from instance bindings ---
        _apply_param_overrides(flat, inst, sub, prefix)

        # --- Wire port connections as continuous assigns ---
        _wire_port_connections(flat, inst, sub, prefix)

        # --- Inline submodule logic with renamed identifiers ---
        _inline_logic(flat, sub, prefix, sub_signals)

        # --- Merge typedefs (needed for enum constants during simulation) ---
        _merge_typedefs(flat, sub)

        # Recurse into sub's instances (already flattened above, so this is a no-op
        # for the normal path, but handles the case where _flatten_module returned
        # the same object because there were no instances)


def _try_resolve_range(r: Range, env: dict[str, int | str]) -> Range:
    """Try to resolve a Range to concrete Literal bounds.  Returns *r* unchanged on failure."""
    try:
        msb_val = _eval_const_expr(r.msb, env)
        lsb_val = _eval_const_expr(r.lsb, env)
        if isinstance(msb_val, int) and isinstance(lsb_val, int):
            return Range(Literal(msb_val), Literal(lsb_val))
    except (ValueError, TypeError, KeyError):
        pass
    return r


def _resolve_parameterized_widths(sub: Module) -> None:
    """Resolve symbolic width/dimension expressions in *sub* to concrete Literals.

    After ``_pre_apply_param_overrides`` updates parameter default values,
    this function evaluates all Range expressions on ports, nets, and variables
    so they become ``Range(Literal, Literal)``.  This ensures downstream code
    (signal registration, codegen, VCD) never needs to interpret symbolic
    width expressions.
    """
    env = _build_param_env(sub)
    if not env:
        return

    for port in sub.ports:
        if port.width is not None and not (isinstance(port.width.msb, Literal) and isinstance(port.width.lsb, Literal)):
            port.width = _try_resolve_range(port.width, env)
        if getattr(port, "dimensions", None):
            port.dimensions = [_try_resolve_range(d, env) for d in port.dimensions]

    for net in sub.nets:
        if net.width is not None and not (isinstance(net.width.msb, Literal) and isinstance(net.width.lsb, Literal)):
            net.width = _try_resolve_range(net.width, env)
        if net.dimensions:
            net.dimensions = [_try_resolve_range(d, env) for d in net.dimensions]

    for var in sub.variables:
        if var.width is not None and not (isinstance(var.width.msb, Literal) and isinstance(var.width.lsb, Literal)):
            var.width = _try_resolve_range(var.width, env)
        if var.dimensions:
            var.dimensions = [_try_resolve_range(d, env) for d in var.dimensions]

    for func in getattr(sub, "functions", []):
        if func.return_range is not None and not (
            isinstance(func.return_range.msb, Literal) and isinstance(func.return_range.lsb, Literal)
        ):
            func.return_range = _try_resolve_range(func.return_range, env)
        for port in func.ports:
            if port.width is not None and not (
                isinstance(port.width.msb, Literal) and isinstance(port.width.lsb, Literal)
            ):
                port.width = _try_resolve_range(port.width, env)
        for local_var in func.locals:
            if local_var.width is not None and not (
                isinstance(local_var.width.msb, Literal) and isinstance(local_var.width.lsb, Literal)
            ):
                local_var.width = _try_resolve_range(local_var.width, env)

    for task in getattr(sub, "tasks", []):
        for port in task.ports:
            if port.width is not None and not (
                isinstance(port.width.msb, Literal) and isinstance(port.width.lsb, Literal)
            ):
                port.width = _try_resolve_range(port.width, env)


def _submodule_signal_names(sub: Module) -> set[str]:
    """Return the set of all signal names declared in *sub*."""
    names: set[str] = set()
    for p in sub.ports:
        names.add(p.name)
    for n in sub.nets:
        names.add(n.name)
    for v in sub.variables:
        names.add(v.name)
    for param in sub.parameters:
        names.add(param.name)
    return names


def _create_prefixed_signals(flat: Module, sub: Module, prefix: str) -> None:
    """Add prefixed copies of the submodule's signals to *flat*."""
    seen: set[str] = set()

    for net in sub.nets:
        flat.nets.append(
            Net(
                f"{prefix}.{net.name}",
                net.kind,
                width=net.width,
                signed=net.signed,
                dimensions=net.dimensions,
                initial_value=net.initial_value,
            )
        )
        seen.add(net.name)

    for var in sub.variables:
        flat.variables.append(
            Variable(
                f"{prefix}.{var.name}",
                var.kind,
                width=var.width,
                signed=var.signed,
                dimensions=var.dimensions,
                initial_value=var.initial_value,
                type_name=var.type_name,
            )
        )
        seen.add(var.name)

    # Ports that don't have a matching net/var declaration need a signal too
    for port in sub.ports:
        if port.name not in seen:
            flat.nets.append(Net(f"{prefix}.{port.name}", NetKind.WIRE, width=port.width, dimensions=port.dimensions))


def _pre_apply_param_overrides(flat: Module, inst: Instance, sub: Module) -> Module:
    """Apply parameter overrides to *sub* before recursive flatten/generates.

    Returns a copy of *sub* with parameter default_values updated to reflect
    the overrides from *inst*.  This ensures that downstream recursive flattening
    and generate elaboration see the correct parameter values rather than the
    module-level defaults.
    """
    # Build override map from instance's parameter bindings
    override_map: dict[str, Expression] = {}
    for i, binding in enumerate(inst.parameter_bindings):
        if binding.name:
            override_map[binding.name] = binding.value
        elif i < len(sub.parameters):
            override_map[sub.parameters[i].name] = binding.value

    if not override_map:
        return sub

    # Resolve override expressions using the parent (flat) module's param env
    resolve_env = _build_param_env(flat)
    if flat.functions:
        resolve_env["__functions__"] = {f.name: f for f in flat.functions}

    sub = copy.deepcopy(sub)
    override_typedefs: set[str] = set()
    type_aliases: dict[str, str] = {}
    for param in sub.parameters:
        if param.name in override_map:
            value = override_map[param.name]
            if value is not None and not isinstance(value, Literal):
                try:
                    val = _eval_const_expr(value, resolve_env)
                    if isinstance(val, int):
                        literal_width = _resolved_param_literal_width(param, _build_param_env(sub))
                        value = Literal(int(val), width=literal_width)
                    elif isinstance(val, str):
                        value = StringLiteral(val)
                except (ValueError, TypeError):
                    # Could not evaluate as a constant: treat as a type/typedef alias
                    if isinstance(value, Identifier):
                        alias_name = _identifier_type_name(value)
                        override_typedefs.add(alias_name.rsplit("::", 1)[-1])
                        type_aliases[param.name] = alias_name
                    value = copy.deepcopy(value)
            param.default_value = value

    existing_typedefs = {td.name for td in sub.typedefs}
    parent_typedefs = {td.name: td for td in flat.typedefs}
    for type_name in override_typedefs:
        parent_td = parent_typedefs.get(type_name)
        if parent_td is not None and type_name not in existing_typedefs:
            sub.typedefs.append(copy.deepcopy(parent_td))
            existing_typedefs.add(type_name)

    if type_aliases:
        _apply_type_aliases(sub, type_aliases)

    return sub


def _resolved_param_literal_width(param: Parameter, env: dict[str, int | str]) -> int | None:
    """Resolve the width to preserve when materializing a constant parameter value."""
    if param.width is not None:
        try:
            msb_val = _eval_const_expr(param.width.msb, env)
            lsb_val = _eval_const_expr(param.width.lsb, env)
        except (ValueError, TypeError):
            return None
        if isinstance(msb_val, int) and isinstance(lsb_val, int):
            return abs(msb_val - lsb_val) + 1
        return None
    if isinstance(param.default_value, Literal):
        return param.default_value.width
    return None


def parameter_signal_width(param: Parameter, env: dict[str, int | str], value: int | str | None = None) -> int:
    """Return the runtime signal width to use for a parameter value."""
    if isinstance(value, str):
        return max(len(value) * 8, 1)
    literal_width = _resolved_param_literal_width(param, env)
    if literal_width is not None:
        return literal_width
    if isinstance(value, int):
        return max(32, value.bit_length(), 1)
    return 32


def _resolve_type_alias(type_name: str, alias_map: dict[str, str]) -> str:
    """Resolve chained type-parameter aliases to a final type name."""
    seen: set[str] = set()
    current = type_name
    while current in alias_map and current not in seen:
        seen.add(current)
        current = alias_map[current]
    return current


def _identifier_type_name(expr: Identifier) -> str:
    """Convert an identifier used as a type reference into ``pkg::name`` form."""
    if expr.hierarchy:
        return "::".join([*expr.hierarchy, expr.name])
    return expr.name


def _apply_type_aliases(module: Module, alias_map: dict[str, str]) -> None:
    """Rewrite type-parameter aliases in ports, signals, typedef fields, and instance bindings."""

    def _rewrite_type_ref(type_name: str | None) -> str | None:
        if type_name is None:
            return None
        return _resolve_type_alias(type_name, alias_map)

    def _rewrite_instance(inst: Instance) -> None:
        for binding in inst.parameter_bindings:
            value = binding.value
            if isinstance(value, Identifier):
                resolved = _resolve_type_alias(_identifier_type_name(value), alias_map)
                if "::" in resolved:
                    *hierarchy, name = resolved.split("::")
                    value.hierarchy = hierarchy
                    value.name = name
                else:
                    value.hierarchy = None
                    value.name = resolved

    def _rewrite_generate_block(block: object) -> None:
        for item in getattr(block, "items", []):
            if isinstance(item, Net):
                if hasattr(item, "type_name"):
                    item.type_name = _rewrite_type_ref(getattr(item, "type_name", None))
            elif isinstance(item, Variable):
                if hasattr(item, "type_name"):
                    item.type_name = _rewrite_type_ref(getattr(item, "type_name", None))
            elif isinstance(item, Instance):
                _rewrite_instance(item)
            elif isinstance(item, GenerateFor):
                _rewrite_generate_block(item.body)
            elif isinstance(item, GenerateIf):
                if item.then_body is not None:
                    _rewrite_generate_block(item.then_body)
                if item.else_body is not None:
                    _rewrite_generate_block(item.else_body)
            elif isinstance(item, GenerateCase):
                for case_item in item.items:
                    if case_item.body is not None:
                        _rewrite_generate_block(case_item.body)

    for port in module.ports:
        port.data_type = _rewrite_type_ref(port.data_type)
    for net in module.nets:
        if hasattr(net, "type_name"):
            net.type_name = _rewrite_type_ref(getattr(net, "type_name", None))
    for var in module.variables:
        if hasattr(var, "type_name"):
            var.type_name = _rewrite_type_ref(getattr(var, "type_name", None))
    for td in getattr(module, "typedefs", []):
        struct_type = getattr(td, "struct_type", None)
        if struct_type is not None:
            for field in struct_type.fields:
                field.data_type = _rewrite_type_ref(getattr(field, "data_type", None))
        union_type = getattr(td, "union_type", None)
        if union_type is not None:
            for field in union_type.fields:
                field.data_type = _rewrite_type_ref(getattr(field, "data_type", None))
        td.type_ref = _rewrite_type_ref(getattr(td, "type_ref", None))
    for inst_item in module.instances:
        _rewrite_instance(inst_item)
    for gen in module.generate_blocks:
        if isinstance(gen, GenerateFor):
            _rewrite_generate_block(gen.body)
        elif isinstance(gen, GenerateIf):
            if gen.then_body is not None:
                _rewrite_generate_block(gen.then_body)
            if gen.else_body is not None:
                _rewrite_generate_block(gen.else_body)
        elif isinstance(gen, GenerateCase):
            for case_item in gen.items:
                if case_item.body is not None:
                    _rewrite_generate_block(case_item.body)


def _apply_param_overrides(flat: Module, inst: Instance, sub: Module, prefix: str) -> None:
    """Create prefixed parameters in *flat*, applying instance parameter overrides."""
    # Build override map from instance's parameter bindings
    override_map: dict[str, Expression] = {}
    for i, binding in enumerate(inst.parameter_bindings):
        if binding.name:
            override_map[binding.name] = binding.value
        elif i < len(sub.parameters):
            # Positional binding
            override_map[sub.parameters[i].name] = binding.value

    # Build a param env from the current flat module so we can resolve
    # override expressions that reference parent-scope parameters
    # (e.g. .NUM_REQS(NUM_REQS) where NUM_REQS is a localparam in the parent).
    resolve_env = _build_param_env(flat)
    if flat.functions:
        resolve_env["__functions__"] = {f.name: f for f in flat.functions}

    # Build a param env from the sub-module itself so that non-overridden
    # dependent parameters (e.g. l2w = $clog2(width)) are evaluated using
    # the sub-module's own parameter values (with overrides already applied
    # by _pre_apply_param_overrides) rather than the parent's env, which
    # may have identically-named parameters with different values.
    sub_env = _build_param_env(sub)

    for param in sub.parameters:
        value = override_map.get(param.name, param.default_value)
        # Try to resolve expression to a concrete Literal so that it
        # survives further hierarchical prefixing without losing context.
        if value is not None and not isinstance(value, Literal):
            # For overridden params, resolve against the parent (flat) env
            # since the override expression references parent-scope names.
            # For non-overridden params, prefer the sub-module's own env
            # since the expression references sub-module-scope names.
            env = resolve_env if param.name in override_map else sub_env
            try:
                val = _eval_const_expr(value, env)
                if isinstance(val, int):
                    literal_width = _resolved_param_literal_width(param, sub_env)
                    value = Literal(int(val), width=literal_width)
                elif isinstance(val, str):
                    value = StringLiteral(val)
            except (ValueError, TypeError):
                pass
        p = Parameter(
            f"{prefix}.{param.name}",
            default_value=copy.deepcopy(value) if value else None,
            is_local=param.is_local,
        )
        flat.parameters.append(p)


def _wire_port_connections(flat: Module, inst: Instance, sub: Module, prefix: str) -> None:
    """Create continuous assigns that wire parent signals to child signals."""
    for conn in inst.port_connections:
        port = _resolve_port_for_conn(conn, sub)
        if port is None or conn.expression is None:
            continue

        child_id = Identifier(f"{prefix}.{port.name}")
        # Deep-copy the parent expression so we don't share AST nodes
        parent_expr = copy.deepcopy(conn.expression)
        pairs = _expand_port_connection_pairs(child_id, parent_expr, getattr(port, "dimensions", []))

        if port.direction in (PortDirection.INPUT, PortDirection.INOUT):
            # Parent drives child: assign child = parent_expr
            for child_expr, source_expr in pairs:
                flat.continuous_assigns.append(ContinuousAssign(child_expr, source_expr))
        if port.direction in (PortDirection.OUTPUT, PortDirection.INOUT):
            # Child drives parent: assign parent_expr = child
            for child_expr, source_expr in pairs:
                flat.continuous_assigns.append(ContinuousAssign(copy.deepcopy(source_expr), copy.deepcopy(child_expr)))


def _expand_port_connection_pairs(
    child_expr: Expression,
    parent_expr: Expression,
    dimensions: list[Range],
) -> list[tuple[Expression, Expression]]:
    """Expand unpacked-array port connections into element-wise expression pairs."""
    if not dimensions:
        return [(child_expr, parent_expr)]

    first, *rest = dimensions
    indices = _dimension_indices(first)
    if indices is None:
        return [(child_expr, parent_expr)]

    pairs: list[tuple[Expression, Expression]] = []
    for index in indices:
        idx_expr = Literal(index)
        child_sel = BitSelect(target=copy.deepcopy(child_expr), index=idx_expr)
        parent_sel = BitSelect(target=copy.deepcopy(parent_expr), index=copy.deepcopy(idx_expr))
        pairs.extend(_expand_port_connection_pairs(child_sel, parent_sel, rest))
    return pairs


def _expand_unpacked_array_elements(expr: Expression, dimensions: list[Range]) -> list[Expression]:
    """Expand an unpacked-array expression into element selects."""
    if not dimensions:
        return [expr]

    first, *rest = dimensions
    indices = _dimension_indices(first)
    if indices is None:
        return [expr]

    elements: list[Expression] = []
    for index in indices:
        idx_expr = Literal(index)
        elem_expr = BitSelect(target=copy.deepcopy(expr), index=idx_expr)
        elements.extend(_expand_unpacked_array_elements(elem_expr, rest))
    return elements


def _expand_unpacked_array_rhs(
    rhs: Expression,
    lhs_parts: list[Expression],
    signal_dimensions: dict[str, list[Range]],
) -> list[Expression]:
    """Expand an unpacked-array RHS into per-element expressions when possible."""
    if isinstance(rhs, Identifier) and rhs.hierarchy is None and signal_dimensions.get(rhs.name):
        rhs_parts = _expand_unpacked_array_elements(copy.deepcopy(rhs), signal_dimensions[rhs.name])
        if len(rhs_parts) == len(lhs_parts):
            return rhs_parts

    if isinstance(rhs, AssignmentPattern):
        if rhs.positional and len(rhs.positional) == len(lhs_parts):
            return [copy.deepcopy(part) for part in rhs.positional]
        if rhs.default_value is not None and not rhs.named_pairs and not rhs.positional:
            return [copy.deepcopy(rhs.default_value) for _ in lhs_parts]

    return [copy.deepcopy(rhs) for _ in lhs_parts]


def _expand_unpacked_array_stmt(stmt, signal_dimensions: dict[str, list[Range]]):
    """Lower whole unpacked-array procedural assignments into per-element assignments."""
    from veriforge.model.statements import (  # noqa: PLC0415
        BlockingAssign,
        CaseStatement,
        DelayControl,
        EventControl,
        ForLoop,
        ForeverLoop,
        IfStatement,
        NonblockingAssign,
        RepeatLoop,
        SeqBlock,
        WhileLoop,
    )

    if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
        lhs = stmt.lhs
        if isinstance(lhs, Identifier) and lhs.hierarchy is None:
            lhs_dims = signal_dimensions.get(lhs.name)
            if lhs_dims:
                lhs_parts = _expand_unpacked_array_elements(copy.deepcopy(lhs), lhs_dims)
                rhs_parts = _expand_unpacked_array_rhs(stmt.rhs, lhs_parts, signal_dimensions)
                assign_type = type(stmt)
                parts = [
                    assign_type(lhs_part, rhs_part) for lhs_part, rhs_part in zip(lhs_parts, rhs_parts, strict=False)
                ]
                return SeqBlock(parts) if len(parts) > 1 else parts[0]
        return stmt

    if isinstance(stmt, SeqBlock):
        stmt.statements = [_expand_unpacked_array_stmt(s, signal_dimensions) for s in stmt.statements]
        return stmt
    if isinstance(stmt, IfStatement):
        stmt.then_body = _expand_unpacked_array_stmt(stmt.then_body, signal_dimensions)
        stmt.else_body = _expand_unpacked_array_stmt(stmt.else_body, signal_dimensions)
        return stmt
    if isinstance(stmt, CaseStatement):
        for item in stmt.items:
            item.body = _expand_unpacked_array_stmt(item.body, signal_dimensions)
        return stmt
    if isinstance(stmt, ForLoop):
        stmt.init = _expand_unpacked_array_stmt(stmt.init, signal_dimensions)
        stmt.update = _expand_unpacked_array_stmt(stmt.update, signal_dimensions)
        stmt.body = _expand_unpacked_array_stmt(stmt.body, signal_dimensions)
        return stmt
    if isinstance(stmt, WhileLoop):
        stmt.body = _expand_unpacked_array_stmt(stmt.body, signal_dimensions)
        return stmt
    if isinstance(stmt, (ForeverLoop, RepeatLoop, DelayControl, EventControl)):
        if hasattr(stmt, "body") and stmt.body is not None:
            stmt.body = _expand_unpacked_array_stmt(stmt.body, signal_dimensions)
        return stmt
    return stmt


def _dimension_indices(dim: Range) -> list[int] | None:
    """Return the concrete indices covered by a resolved unpacked-array dimension."""
    try:
        msb = _eval_const_expr(dim.msb, {})
        lsb = _eval_const_expr(dim.lsb, {})
    except (ValueError, TypeError, KeyError):
        return None

    if not isinstance(msb, int) or not isinstance(lsb, int):
        return None

    step = -1 if msb > lsb else 1
    return list(range(msb, lsb + step, step))


def _resolve_port_for_conn(conn, sub: Module) -> Port | None:
    """Resolve the port object for a port connection."""
    if conn.resolved_port is not None:
        return conn.resolved_port
    if conn.is_named and conn.port_name:
        return sub.get_port(conn.port_name)
    # Positional: match by index (need to find the index)
    # The caller iterates inst.port_connections, but we don't have the index here.
    # For positional connections, resolved_port should already be set by
    # resolve_port_connections(). If not, we can try matching by position.
    return None


def _inline_logic(flat: Module, sub: Module, prefix: str, sub_signals: set[str]) -> None:
    """Deep-copy and rename submodule logic into *flat*."""
    signal_dimensions: dict[str, list[Range]] = {}
    for port in sub.ports:
        signal_dimensions[port.name] = list(getattr(port, "dimensions", []) or [])
    for net in sub.nets:
        signal_dimensions[net.name] = list(getattr(net, "dimensions", []) or [])
    for var in sub.variables:
        signal_dimensions[var.name] = list(getattr(var, "dimensions", []) or [])

    for ca in sub.continuous_assigns:
        lhs = ca.lhs
        rhs = ca.rhs
        if isinstance(lhs, Identifier) and lhs.hierarchy is None and signal_dimensions.get(lhs.name):
            lhs_parts = _expand_unpacked_array_elements(copy.deepcopy(lhs), signal_dimensions[lhs.name])
            rhs_parts = _expand_unpacked_array_rhs(copy.deepcopy(rhs), lhs_parts, signal_dimensions)
            for lhs_expr, rhs_expr in zip(lhs_parts, rhs_parts, strict=False):
                ca_copy = ContinuousAssign(lhs_expr, rhs_expr)
                _prefix_identifiers(ca_copy, prefix, sub_signals)
                flat.continuous_assigns.append(ca_copy)
            continue
        ca_copy = copy.deepcopy(ca)
        _prefix_identifiers(ca_copy, prefix, sub_signals)
        flat.continuous_assigns.append(ca_copy)

    for ab in sub.always_blocks:
        ab_copy = copy.deepcopy(ab)
        ab_copy.body = _expand_unpacked_array_stmt(ab_copy.body, signal_dimensions)
        _prefix_identifiers(ab_copy, prefix, sub_signals)
        flat.always_blocks.append(ab_copy)

    for ib in sub.initial_blocks:
        ib_copy = copy.deepcopy(ib)
        ib_copy.body = _expand_unpacked_array_stmt(ib_copy.body, signal_dimensions)
        _prefix_identifiers(ib_copy, prefix, sub_signals)
        flat.initial_blocks.append(ib_copy)

    for func in sub.functions:
        func_copy = copy.deepcopy(func)
        # Function-local names that must NOT be prefix-rewritten: the
        # function's own name (used as the return-value identifier in IEEE
        # 1364), its argument ports, and any locally declared variables.
        local_names: set[str] = {func_copy.name}
        for p in func_copy.ports:
            local_names.add(p.name)
        for lv in func_copy.locals:
            local_names.add(lv.name)
        # Names visible inside the function body that come from the
        # enclosing module (parameters, nets, vars, ports) — these must be
        # rewritten so they continue to resolve after flattening.
        outer_names = sub_signals - local_names
        if func_copy.body is not None and outer_names:
            _prefix_identifiers(func_copy.body, prefix, outer_names)
        flat.functions.append(func_copy)

    for task in sub.tasks:
        task_copy = copy.deepcopy(task)
        local_names = {task_copy.name}
        for p in task_copy.ports:
            local_names.add(p.name)
        outer_names = sub_signals - local_names
        if task_copy.body is not None and outer_names:
            _prefix_identifiers(task_copy.body, prefix, outer_names)
        flat.tasks.append(task_copy)


def _merge_typedefs(flat: Module, sub: Module) -> None:
    """Copy unique typedefs from *sub* into *flat* (no duplicates by name)."""
    sub_tds = getattr(sub, "typedefs", None)
    if not sub_tds:
        return
    existing = {td.name for td in getattr(flat, "typedefs", [])}
    if not hasattr(flat, "typedefs"):
        flat.typedefs = []
    for td in sub_tds:
        if td.name not in existing:
            flat.typedefs.append(td)
            existing.add(td.name)


def _prefix_identifiers(root: Expression, prefix: str, signal_names: set[str]) -> None:
    """Walk *root* and rename all :class:`Identifier` nodes whose name is in *signal_names*.

    Also handles hierarchical identifiers (struct field access like ``a.b``)
    where ``a`` is in *signal_names* — these are flattened to dotted names
    (e.g. ``Identifier(name='b', hierarchy=['a'])`` → ``Identifier(name='prefix.a.b')``).

    Additionally handles already-flattened dotted names (e.g. ``inst.sig.field``)
    where ``inst.sig`` is in *signal_names* — the full name is prefixed.
    """
    for node in root.walk():
        if isinstance(node, Identifier):
            if node.name in signal_names:
                node.name = f"{prefix}.{node.name}"
            elif node.hierarchy:
                # Check if the first hierarchy part is a signal (struct field access)
                first_part = node.hierarchy[0]
                base_part = first_part.split("[", 1)[0] if "[" in first_part else first_part
                if first_part in signal_names or base_part in signal_names:
                    parts = [prefix, *node.hierarchy, node.name]
                    node.name = ".".join(parts)
                    node.hierarchy = None
            elif "." in node.name:
                # Already-flattened dotted name (e.g. "inst.struct_var.field")
                # Check if any prefix of the dotted name is a known signal
                parts = node.name.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    candidate = ".".join(parts[:i])
                    if candidate in signal_names or normalize_struct_access_name(candidate) in signal_names:
                        node.name = f"{prefix}.{node.name}"
                        break


def _rename_identifiers(root: Expression, rename_map: dict[str, str]) -> None:
    """Walk *root* and rename Identifier nodes whose name is a key in *rename_map*.

    Each key maps to the fully-scoped replacement name.
    Used to propagate ancestor generate scope renames into nested blocks.
    """
    for node in root.walk():
        if isinstance(node, Identifier):
            if node.name in rename_map:
                node.name = rename_map[node.name]
            elif node.hierarchy:
                first_part = node.hierarchy[0]
                if first_part in rename_map:
                    new_base = rename_map[first_part]
                    parts = [new_base, *list(node.hierarchy[1:]), node.name]
                    node.name = ".".join(parts)
                    node.hierarchy = None
                    continue
                base_part = first_part.split("[", 1)[0] if "[" in first_part else first_part
                if base_part in rename_map:
                    suffix = first_part[len(base_part) :]
                    new_base = f"{rename_map[base_part]}{suffix}"
                    parts = [new_base, *list(node.hierarchy[1:]), node.name]
                    node.name = ".".join(parts)
                    node.hierarchy = None


# ── Interface flattening ─────────────────────────────────────────────


def _flatten_interface_instances(flat: Module, intf_instances: list[tuple[str, object]]) -> None:
    """Create prefixed signals for interface instances and wire continuous assigns."""
    from ..model.interface import Interface as InterfaceModel  # noqa: PLC0415

    for inst_name, intf in intf_instances:
        if not isinstance(intf, InterfaceModel):
            continue
        for net in intf.nets:
            flat.nets.append(Net(f"{inst_name}.{net.name}", net.kind, width=net.width, signed=net.signed))
        for var in intf.variables:
            flat.variables.append(Variable(f"{inst_name}.{var.name}", var.kind, width=var.width, signed=var.signed))
        # Copy continuous assigns with prefixed identifiers
        intf_signals = _submodule_signal_names_from_intf(intf)
        for ca in intf.continuous_assigns:
            ca_copy = copy.deepcopy(ca)
            _prefix_identifiers(ca_copy, inst_name, intf_signals)
            flat.continuous_assigns.append(ca_copy)
        # Copy typedefs from interface
        for td in intf.typedefs:
            if not hasattr(flat, "typedefs"):
                flat.typedefs = []
            flat.typedefs.append(td)


def _submodule_signal_names_from_intf(intf: object) -> set[str]:
    """Return signal names from an interface."""
    names: set[str] = set()
    for n in intf.nets:
        names.add(n.name)
    for v in intf.variables:
        names.add(v.name)
    return names


# ── Generate elaboration ─────────────────────────────────────────────


def elaborate_generates(module: Module, param_values: dict[str, int] | None = None) -> Module:
    """Evaluate generate constructs and promote items into the module.

    Returns a **new** :class:`Module` with generate blocks resolved into
    concrete nets, variables, assigns, always/initial blocks, and instances.
    The original *module* is not mutated.
    """
    if not module.generate_blocks:
        return module

    result = Module(module.name, ports=list(module.ports))
    result.nets = list(module.nets)
    result.variables = list(module.variables)
    result.parameters = list(module.parameters)
    result.continuous_assigns = list(module.continuous_assigns)
    result.always_blocks = list(module.always_blocks)
    result.initial_blocks = list(module.initial_blocks)
    result.instances = list(module.instances)
    result.functions = list(module.functions)
    result.tasks = list(module.tasks)
    result.typedefs = list(module.typedefs)
    result.imports = list(module.imports)

    env = _build_param_env(result, param_values)
    unnamed_ct = [0]
    for gen in module.generate_blocks:
        _elaborate_one(result, gen, env, unnamed_ct)
    # generate_blocks intentionally left empty on result
    return result


# ── Constant expression evaluation ──────────────────────────────────

_UNARY_OPS: dict[str, object] = {
    "~": lambda v: ~v & 0xFFFFFFFF,
    "!": lambda v: int(not v),
    "-": lambda v: -v,
    "+": lambda v: v,
    # Verilog reduction operators (unary |, &, ^, etc.)
    "|": lambda v: int(v != 0),
    "&": lambda v: int(v == ((1 << v.bit_length()) - 1)) if v >= 0 else 1,
    "^": lambda v: bin(v & 0xFFFFFFFF).count("1") % 2,
    "~|": lambda v: int(v == 0),
    "~&": lambda v: int(v != ((1 << v.bit_length()) - 1)) if v >= 0 else 0,
    "~^": lambda v: int(bin(v & 0xFFFFFFFF).count("1") % 2 == 0),
    "^~": lambda v: int(bin(v & 0xFFFFFFFF).count("1") % 2 == 0),
}

_BINARY_OPS: dict[str, object] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b if b else 0,
    "%": lambda a, b: a % b if b else 0,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b),
    ">=": lambda a, b: int(a >= b),
    "&&": lambda a, b: int(bool(a) and bool(b)),
    "||": lambda a, b: int(bool(a) or bool(b)),
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "~^": lambda a, b: ~(a ^ b) & 0xFFFFFFFF,
    "^~": lambda a, b: ~(a ^ b) & 0xFFFFFFFF,
    "**": lambda a, b: a**b,
}


def _eval_const_expr(expr: Expression, env: dict[str, int | str]) -> int | str:
    """Evaluate a constant expression given genvar/parameter values in *env*."""
    if isinstance(expr, StringLiteral):
        return expr.value
    if isinstance(expr, Literal):
        if isinstance(expr.value, str):
            try:
                return int(expr.value)
            except (ValueError, TypeError):
                return expr.value
        return int(expr.value)
    if isinstance(expr, Identifier):
        if expr.name in env:
            return env[expr.name]
        raise ValueError(f"Unknown identifier '{expr.name}' in constant expression")
    if isinstance(expr, UnaryOp):
        val = _eval_const_expr(expr.operand, env)
        fn = _UNARY_OPS.get(expr.op)
        if fn is None:
            raise ValueError(f"Unsupported unary operator '{expr.op}' in constant expression")
        return fn(val)
    if isinstance(expr, BinaryOp):
        left = _eval_const_expr(expr.left, env)
        right = _eval_const_expr(expr.right, env)
        fn = _BINARY_OPS.get(expr.op)
        if fn is None:
            raise ValueError(f"Unsupported binary operator '{expr.op}' in constant expression")
        return fn(left, right)
    if isinstance(expr, TernaryOp):
        cond = _eval_const_expr(expr.condition, env)
        return _eval_const_expr(expr.true_expr if cond else expr.false_expr, env)
    if isinstance(expr, Concatenation):
        # Single-element concatenation {X} is just X.
        # Multi-element: evaluate each part and combine.
        if len(expr.parts) == 1:
            return _eval_const_expr(expr.parts[0], env)
        result = 0
        for part in expr.parts:
            part_val = _eval_const_expr(part, env)
            if not isinstance(part_val, int):
                raise ValueError("Cannot evaluate non-integer Concatenation part")
            part_width = getattr(part, "inferred_width", None)
            if part_width is None and isinstance(part, Literal):
                part_width = part.width
            if part_width is None and isinstance(part, FunctionCall):
                part_width = _user_func_return_width(part.name, env)
            if part_width is None:
                raise ValueError("Cannot evaluate multi-element Concatenation without width info")
            result = (result << part_width) | (part_val & ((1 << part_width) - 1))
        return result
    if isinstance(expr, Replication):
        count = _eval_const_expr(expr.count, env)
        value = _eval_const_expr(expr.value, env)
        if not isinstance(count, int) or not isinstance(value, int):
            raise ValueError("Cannot evaluate non-integer Replication")
        value_width = getattr(expr.value, "inferred_width", None)
        if value_width is None and isinstance(expr.value, Literal):
            value_width = expr.value.width
        if value_width is None and isinstance(expr.value, Concatenation):
            part_widths: list[int] = []
            for part in expr.value.parts:
                part_width = getattr(part, "inferred_width", None)
                if part_width is None and isinstance(part, Literal):
                    part_width = part.width
                if part_width is None:
                    raise ValueError("Cannot evaluate Replication without value width info")
                part_widths.append(part_width)
            value_width = sum(part_widths)
        if value_width is None:
            raise ValueError("Cannot evaluate Replication without value width info")
        mask = (1 << value_width) - 1
        result = 0
        for _ in range(count):
            result = (result << value_width) | (value & mask)
        return result
    if isinstance(expr, RangeSelect):
        base = _eval_const_expr(expr.target, env)
        if isinstance(base, int):
            msb = _eval_const_expr(expr.msb, env)
            lsb = _eval_const_expr(expr.lsb, env)
            if isinstance(msb, int) and isinstance(lsb, int):
                width = msb - lsb + 1
                return (base >> lsb) & ((1 << width) - 1)
        raise ValueError("Cannot evaluate RangeSelect as constant expression")
    if isinstance(expr, PartSelect):
        target_val = _eval_const_expr(expr.target, env)
        if isinstance(target_val, int):
            base_val = _eval_const_expr(expr.base, env)
            width_val = _eval_const_expr(expr.width, env)
            if isinstance(base_val, int) and isinstance(width_val, int):
                if expr.direction == "+:":
                    return (target_val >> base_val) & ((1 << width_val) - 1)
                # "-:" direction: bits[base : base - width + 1]
                lsb_val = base_val - width_val + 1
                return (target_val >> lsb_val) & ((1 << width_val) - 1)
        raise ValueError("Cannot evaluate PartSelect as constant expression")
    if isinstance(expr, FunctionCall) and (expr.is_system or expr.name.startswith("$")):
        if expr.name == "$clog2" and len(expr.arguments) == 1:
            val = _eval_const_expr(expr.arguments[0], env)
            if isinstance(val, int) and val > 0:
                return (val - 1).bit_length()
            return 0
        if expr.name == "$bits" and len(expr.arguments) == 1:
            arg = expr.arguments[0]
            if isinstance(arg, Identifier):
                bits_key = f"$bits:{arg.name}"
                if bits_key in env:
                    return env[bits_key]
            # Fall back: try evaluating the argument and returning 32 (integer)
            raise ValueError(f"Cannot resolve $bits({arg}) — unknown type")
        raise ValueError(f"Unsupported system function '{expr.name}' in constant expression")
    if isinstance(expr, FunctionCall):
        user_funcs = env.get("__functions__")
        if isinstance(user_funcs, dict) and expr.name in user_funcs:
            func = user_funcs[expr.name]
            arg_vals = [_eval_const_expr(a, env) for a in expr.arguments]
            # Merge outer env so module-level parameters are accessible inside the function body.
            # Function arguments and the return variable take precedence over the outer scope.
            local_env: dict[str, int | str] = {**env, "__functions__": user_funcs}
            local_env[func.name] = 0  # initialise return variable
            for port, val in zip(func.ports, arg_vals):
                local_env[port.name] = val
            if func.body is not None:
                return _eval_user_function_body(func.name, func.body, local_env)
        raise ValueError(f"Cannot evaluate user function '{expr.name}' as constant expression")
    raise ValueError(f"Cannot evaluate {type(expr).__name__} as constant expression")


def _user_func_return_width(func_name: str, env: dict[str, int | str]) -> int | None:
    """Return the return width of a user-defined function looked up via env['__functions__']."""
    user_funcs = env.get("__functions__")
    if not isinstance(user_funcs, dict):
        return None
    func = user_funcs.get(func_name)
    if func is None or func.return_range is None:
        return None
    try:
        msb = _eval_const_expr(func.return_range.msb, env)
        lsb = _eval_const_expr(func.return_range.lsb, env)
        if isinstance(msb, int) and isinstance(lsb, int):
            return abs(msb - lsb) + 1
    except (ValueError, TypeError):
        pass
    return None


def _exec_const_stmt(stmt: object, env: dict[str, int | str], func_name: str) -> None:
    """Execute a statement in a constant expression context (function body evaluation).

    Supports: ``SeqBlock``, ``BlockingAssign`` (simple or part-select LHS),
    ``IfStatement``, and ``ForLoop``.  Updates *env* in-place.
    """
    if isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            _exec_const_stmt(s, env, func_name)
        return

    if isinstance(stmt, BlockingAssign):
        rhs_val = _eval_const_expr(stmt.rhs, env)
        if isinstance(stmt.lhs, Identifier):
            env[stmt.lhs.name] = rhs_val
        elif isinstance(stmt.lhs, PartSelect) and isinstance(stmt.lhs.target, Identifier):
            # Handle: target[base +: width] = rhs  (part-select write)
            target_name = stmt.lhs.target.name
            base_val = _eval_const_expr(stmt.lhs.base, env)
            width_val = _eval_const_expr(stmt.lhs.width, env)
            current = env.get(target_name, 0)
            if isinstance(current, int) and isinstance(base_val, int) and isinstance(width_val, int):
                mask = (1 << width_val) - 1
                env[target_name] = (current & ~(mask << base_val)) | ((rhs_val & mask) << base_val)
        return

    if isinstance(stmt, IfStatement):
        cond_val = _eval_const_expr(stmt.condition, env)
        if cond_val:
            if stmt.then_body is not None:
                _exec_const_stmt(stmt.then_body, env, func_name)
        else:
            if stmt.else_body is not None:
                _exec_const_stmt(stmt.else_body, env, func_name)
        return

    if isinstance(stmt, ForLoop):
        _exec_const_stmt(stmt.init, env, func_name)
        for _ in range(100_000):  # safety limit against infinite loops
            if not _eval_const_expr(stmt.condition, env):
                break
            if stmt.body is not None:
                _exec_const_stmt(stmt.body, env, func_name)
            _exec_const_stmt(stmt.update, env, func_name)
        return

    raise ValueError(f"Cannot evaluate {type(stmt).__name__} in constant function body '{func_name}'")


def _eval_user_function_body(
    func_name: str,
    stmt: object,
    local_env: dict[str, int | str],
) -> int | str:
    """Evaluate a user-defined function body at elaboration time.

    Supports sequential blocks with local variable assignments, ``for`` loops,
    ``if`` conditionals, and part-select LHS writes to the function return variable.
    The caller must have pre-initialised ``local_env[func_name] = 0``.
    """
    _exec_const_stmt(stmt, local_env, func_name)
    result = local_env.get(func_name)
    if result is None:
        raise ValueError(f"Cannot evaluate function '{func_name}': no return assignment found in body")
    return result


# ── Generate helpers ─────────────────────────────────────────────────


def _collect_enum_values(module: Module) -> dict[str, int]:
    """Collect enum member name → integer value from typedefs (no dependency on _build_param_env)."""
    result: dict[str, int] = {}
    typedefs = getattr(module, "typedefs", None)
    if not typedefs:
        return result
    for td in typedefs:
        enum = getattr(td, "enum_type", None)
        if enum is None:
            continue
        next_val = 0
        for member in enum.members:
            if member.value is not None:
                try:
                    next_val = _eval_const_expr(member.value, result)
                except (ValueError, TypeError):
                    pass
            result[member.name] = next_val
            next_val += 1
    return result


def _seed_bits_entries(env: dict[str, int | str], module: Module) -> None:
    """Add ``$bits:typename`` entries to *env* for typedef width lookups.

    Enables ``$bits(typename)`` in constant expressions during elaboration.
    Uses two passes: first aliases/enums (no dependencies), then structs
    (may reference alias/enum types in their fields).
    """
    typedefs = getattr(module, "typedefs", None)
    if not typedefs:
        return

    struct_tds: list = []

    # Pass 1: aliases and enums (no cross-typedef dependencies)
    for td in typedefs:
        w = getattr(td, "width", None)
        if w is not None:
            try:
                msb = _eval_const_expr(w.msb, env)
                lsb = _eval_const_expr(w.lsb, env)
                env[f"$bits:{td.name}"] = int(msb) - int(lsb) + 1
            except (ValueError, TypeError):
                pass
            continue
        et = getattr(td, "enum_type", None)
        if et is not None and getattr(et, "width", None) is not None:
            try:
                msb = _eval_const_expr(et.width.msb, env)
                lsb = _eval_const_expr(et.width.lsb, env)
                env[f"$bits:{td.name}"] = int(msb) - int(lsb) + 1
            except (ValueError, TypeError):
                pass
            continue
        st = getattr(td, "struct_type", None)
        if st is not None and getattr(st, "packed", False):
            struct_tds.append(td)

    # Pass 2: packed structs (fields may reference alias/enum types resolved above)
    for td in struct_tds:
        st = td.struct_type
        total = 0
        ok = True
        for fld in st.fields:
            if fld.width is not None:
                try:
                    msb = _eval_const_expr(fld.width.msb, env)
                    lsb = _eval_const_expr(fld.width.lsb, env)
                    total += int(msb) - int(lsb) + 1
                except (ValueError, TypeError):
                    ok = False
                    break
            else:
                dt = getattr(fld, "data_type", None)
                bits_key = f"$bits:{dt}"
                if dt and bits_key in env:
                    total += env[bits_key]
                else:
                    total += 1
        if ok and total > 0:
            env[f"$bits:{td.name}"] = total


def _build_type_width_map(module: Module, env: dict[str, int | str] | None = None) -> dict[str, int]:
    """Build a map of typedef name -> bit width for resolving struct field types."""
    type_widths: dict[str, int] = {}
    typedefs = getattr(module, "typedefs", None)
    if not typedefs:
        return type_widths
    if env is None:
        env = _build_param_env(module)

    # Pass 1: collect alias and enum widths independent of declaration order.
    for td in typedefs:
        w = getattr(td, "width", None)
        if w is not None:
            try:
                msb = _eval_const_expr(w.msb, env)
                lsb = _eval_const_expr(w.lsb, env)
                type_widths[td.name] = int(msb) - int(lsb) + 1
            except (TypeError, ValueError):
                pass
            continue
        if td.enum_type is not None:
            w = td.enum_type.width
            if w is not None and isinstance(w.msb, Literal) and isinstance(w.lsb, Literal):
                try:
                    type_widths[td.name] = int(w.msb.value) - int(w.lsb.value) + 1
                except (TypeError, ValueError):
                    type_widths[td.name] = 32
            else:
                try:
                    msb = _eval_const_expr(w.msb, env)
                    lsb = _eval_const_expr(w.lsb, env)
                    type_widths[td.name] = int(msb) - int(lsb) + 1
                except (TypeError, ValueError, AttributeError):
                    type_widths[td.name] = 32

    # Pass 2: resolve packed struct widths using the alias/enum widths collected above.
    for td in typedefs:
        if td.struct_type is not None:
            type_widths[td.name] = _struct_total_width(td.struct_type, type_widths)
    return type_widths


def _struct_total_width(st, type_widths: dict[str, int]) -> int:
    """Compute total width of a packed struct using resolved field type widths."""
    total = 0
    for f in st.fields:
        total += _resolved_field_width(f, type_widths)
    return total


def _resolved_field_width(field, type_widths: dict[str, int]) -> int:
    """Compute the width of a struct field, resolving typedef'd types."""
    if field.width is not None:
        try:
            if isinstance(field.width.msb, Literal) and isinstance(field.width.lsb, Literal):
                return int(field.width.msb.value) - int(field.width.lsb.value) + 1
        except (TypeError, ValueError):
            pass
    data_type = getattr(field, "data_type", None)
    if data_type in type_widths:
        return type_widths[data_type]
    if isinstance(data_type, str) and "::" in data_type:
        bare = data_type.rsplit("::", 1)[-1]
        if bare in type_widths:
            return type_widths[bare]
    return 1


def _eval_assignment_pattern(
    pattern: AssignmentPattern,
    env: dict[str, int | str],
    module: Module,
) -> int:
    """Evaluate a struct assignment pattern to an integer value.

    Matches the pattern's named fields against struct typedefs to find
    the struct type, then packs field values at their bit positions.
    """
    # Default-only pattern ('{default: '0}) → all zeros or all ones
    if not pattern.named_pairs and pattern.default_value is not None and not pattern.positional:
        dv = _eval_const_expr(pattern.default_value, env)
        if isinstance(dv, int) and dv == 0:
            return 0
        if isinstance(dv, int) and dv == -1:
            return (1 << 32) - 1  # all ones for 32-bit
        return int(dv) if isinstance(dv, int) else 0

    # Named pattern: find matching struct typedef
    if not pattern.named_pairs:
        return 0

    field_names = {name for name, _ in pattern.named_pairs}
    type_widths = _build_type_width_map(module, env)
    # Supplement type_widths with $bits: entries from env
    for key, val in env.items():
        if isinstance(key, str) and key.startswith("$bits:") and isinstance(val, int):
            tname = key[6:]
            if tname not in type_widths:
                type_widths[tname] = val

    typedefs = getattr(module, "typedefs", None) or []
    matching_struct = None
    best_extra = float("inf")
    for td in typedefs:
        st = getattr(td, "struct_type", None)
        if st is None or not st.packed:
            continue
        struct_fields = {f.name for f in st.fields}
        if field_names <= struct_fields:
            extra = len(struct_fields) - len(field_names)
            if extra < best_extra:
                matching_struct = st
                best_extra = extra
                if extra == 0:
                    break  # exact match

    if matching_struct is None:
        raise ValueError(f"Cannot find matching struct type for fields: {field_names}")

    # Compute layout with resolved field widths (MSB-first packing)
    layout: dict[str, tuple[int, int]] = {}
    offset = 0
    for struct_field in reversed(matching_struct.fields):
        w = _resolved_field_width(struct_field, type_widths)
        layout[struct_field.name] = (offset, w)
        offset += w

    # Evaluate default value
    default_int = 0
    if pattern.default_value is not None:
        try:
            dv = _eval_const_expr(pattern.default_value, env)
            if isinstance(dv, int):
                default_int = dv
        except (ValueError, TypeError):
            pass

    # Start with default for all fields
    result = 0
    if default_int == -1:
        # All ones
        result = (1 << offset) - 1
    elif default_int != 0:
        # Apply default to each field
        for _, (foff, fwid) in layout.items():
            mask = (1 << fwid) - 1
            result |= (default_int & mask) << foff

    # Apply named fields
    for name, expr in pattern.named_pairs:
        if name not in layout:
            continue
        foff, fwid = layout[name]
        try:
            val = _eval_const_expr(expr, env)
            if isinstance(val, int):
                mask = (1 << fwid) - 1
                # Clear the field bits first, then set
                result &= ~(mask << foff)
                result |= (val & mask) << foff
        except (ValueError, TypeError):
            pass

    return result


def _build_param_env(module: Module, overrides: dict[str, int] | None = None) -> dict[str, int | str]:
    """Build an environment mapping parameter names to integer (or string) values."""
    env: dict[str, int | str] = {}

    # Seed with enum member values from typedefs (needed for enum-typed parameters)
    enum_env = _collect_enum_values(module)
    env.update(enum_env)

    # Seed with $bits:typename entries for typedef width lookups
    _seed_bits_entries(env, module)

    # Inject the module's own functions so parameter expressions (e.g. derived
    # parameters computed via a helper function) can be evaluated.
    module_funcs = getattr(module, "functions", None)
    if module_funcs:
        env["__functions__"] = {f.name: f for f in module_funcs}

    # Iterate to a fixed point: parameter expressions may forward-reference
    # later-declared params (e.g. M_ID_WIDTH = S_ID_WIDTH + $clog2(S_COUNT)
    # where S_COUNT is declared after M_ID_WIDTH).  Keep retrying any params
    # that failed to evaluate until no more progress is made.
    remaining = [p for p in module.parameters if p.default_value is not None]
    while remaining:
        progress = False
        still_pending: list = []
        for p in remaining:
            # For hierarchically-prefixed params (e.g. "uut.BITS"), the default
            # expression still references unprefixed identifiers ("ENABLE_WIDE").
            # Build a local env that includes unprefixed aliases for the same prefix.
            prefix = ""
            dot = p.name.rfind(".")
            if dot >= 0:
                prefix = p.name[: dot + 1]  # e.g. "uut."
            if prefix:
                local_env = dict(env)
                for k, v in env.items():
                    if k.startswith(prefix):
                        local_env[k[len(prefix) :]] = v
            else:
                local_env = env
            try:
                env[p.name] = _eval_const_expr(p.default_value, local_env)
                progress = True
            except (ValueError, TypeError):
                # Try evaluating as a struct assignment pattern
                if isinstance(p.default_value, AssignmentPattern):
                    try:
                        env[p.name] = _eval_assignment_pattern(p.default_value, local_env, module)
                        progress = True
                        continue
                    except (ValueError, TypeError):
                        pass
                still_pending.append(p)
        if not progress:
            break
        remaining = still_pending
    if overrides:
        env.update(overrides)

    # Re-seed $bits entries now that parameters are resolved – typedefs whose
    # widths reference parameters (e.g. typedef logic [Param-1:0] ...) can now
    # be evaluated.  Typedef width expressions use unprefixed identifiers, so
    # create a supplementary env with unprefixed aliases for all dotted params.
    seed_env = dict(env)
    for k, v in env.items():
        dot = k.rfind(".")
        if dot >= 0:
            short = k[dot + 1 :]
            if short not in seed_env:
                seed_env[short] = v
    _seed_bits_entries(seed_env, module)
    # Copy only the new $bits: entries back into env
    for k, v in seed_env.items():
        if k.startswith("$bits:") and k not in env:
            env[k] = v

    return env


def _build_enum_env(module: Module) -> dict[str, tuple[int, int]]:
    """Build an environment mapping enum member names to (value, width) pairs.

    Scans ``module.typedefs`` for ``EnumType`` declarations and returns a dict
    mapping each member name to its constant value and width.  Members without
    explicit values are auto-numbered sequentially from the last assigned value.
    """
    result: dict[str, tuple[int, int]] = {}
    typedefs = getattr(module, "typedefs", None)
    if not typedefs:
        return result

    param_env = _build_param_env(module)

    for td in typedefs:
        enum = getattr(td, "enum_type", None)
        if enum is None:
            continue
        # Determine the width of the enum type
        width = 32  # default
        if enum.width is not None:
            if isinstance(enum.width.msb, Literal) and isinstance(enum.width.lsb, Literal):
                try:
                    width = int(enum.width.msb.value) - int(enum.width.lsb.value) + 1
                except (TypeError, ValueError):
                    pass

        # Assign values to members
        next_val = 0
        for member in enum.members:
            if member.value is not None:
                try:
                    next_val = _eval_const_expr(member.value, param_env)
                except (ValueError, TypeError):
                    pass  # keep next_val
            if member.name not in result:
                result[member.name] = (next_val, width)
            next_val += 1
    return result


def _build_struct_env(module: Module) -> tuple[dict[str, StructLayout], dict[str, StructLayout]]:
    """Build struct type registry from module typedefs.

    Returns:
        (type_map, signal_map) where:
        - type_map maps typedef name -> StructLayout
        - signal_map maps variable name -> StructLayout (for struct-typed variables)
    """
    type_map: dict[str, StructLayout] = {}
    signal_map: dict[str, StructLayout] = {}
    struct_defs: dict[str, object] = {}

    typedefs = getattr(module, "typedefs", None)
    type_widths = _build_type_width_map(module)
    if typedefs:
        for td in typedefs:
            st = getattr(td, "struct_type", None)
            if st is None or not st.packed:
                continue
            struct_defs[td.name] = st
            layout_fields: dict[str, tuple[int, int]] = {}
            offset = 0
            for field in reversed(st.fields):
                width = _resolved_field_width(field, type_widths)
                layout_fields[field.name] = (offset, width)
                offset += width
            layout = StructLayout(
                name=td.name,
                total_width=offset,
                fields=layout_fields,
            )
            type_map[td.name] = layout

    unpacked_array_names: set[str] = set()
    for net in module.nets:
        if getattr(net, "dimensions", None):
            unpacked_array_names.add(net.name)
    for var in module.variables:
        if getattr(var, "dimensions", None):
            unpacked_array_names.add(var.name)
    for port in module.ports:
        if getattr(port, "dimensions", None):
            unpacked_array_names.add(port.name)

    def _struct_assign_base(expr: Expression) -> str | None:
        if isinstance(expr, Identifier) and expr.hierarchy is None:
            return expr.name
        if isinstance(expr, BitSelect) and isinstance(expr.target, Identifier) and expr.target.hierarchy is None:
            base_name = expr.target.name
            if base_name in unpacked_array_names:
                return base_name
        return None

    # Scan variables and ports for struct-typed ones
    if type_map:
        for var in module.variables:
            tn = getattr(var, "type_name", None)
            if tn:
                bare = tn.rsplit("::", 1)[-1] if "::" in tn else tn
                if bare in type_map:
                    signal_map[var.name] = type_map[bare]
        for port in module.ports:
            dt = getattr(port, "data_type", None)
            if dt:
                bare = dt.rsplit("::", 1)[-1] if "::" in dt else dt
                if bare in type_map:
                    signal_map[port.name] = type_map[bare]

        changed = True
        while changed:
            changed = False
            for assign in getattr(module, "continuous_assigns", []):
                if not isinstance(assign, ContinuousAssign):
                    continue
                lhs_name = _struct_assign_base(assign.lhs)
                rhs_name = _struct_assign_base(assign.rhs)
                if lhs_name is None or rhs_name is None:
                    continue
                lhs_layout = signal_map.get(lhs_name)
                rhs_layout = signal_map.get(rhs_name)
                if lhs_layout is not None and rhs_layout is None:
                    signal_map[rhs_name] = lhs_layout
                    changed = True
                elif rhs_layout is not None and lhs_layout is None:
                    signal_map[lhs_name] = rhs_layout
                    changed = True

        nested_added = True
        while nested_added:
            nested_added = False
            for signal_name, layout in list(signal_map.items()):
                struct_type = struct_defs.get(layout.name)
                if struct_type is None:
                    continue
                for field in struct_type.fields:
                    data_type = getattr(field, "data_type", None)
                    bare = (
                        data_type.rsplit("::", 1)[-1] if isinstance(data_type, str) and "::" in data_type else data_type
                    )
                    nested_layout = type_map.get(bare)
                    if nested_layout is None:
                        continue
                    nested_name = f"{signal_name}.{field.name}"
                    if nested_name not in signal_map:
                        signal_map[nested_name] = nested_layout
                        nested_added = True

    return type_map, signal_map


def resolve_sv_imports(module: Module, design: Design | None) -> None:  # noqa: PLR0912
    """Copy package symbols into module scope based on import declarations.

    Scans ``module.imports`` and for each referenced package found in *design*,
    copies its parameters and typedefs into the module so that elaboration
    and simulation can resolve them.  Duplicate names are skipped.
    """
    imports = getattr(module, "imports", None) or []
    if design is None:
        return
    packages = getattr(design, "packages", None)
    if not packages:
        return
    pkg_map = {p.name: p for p in packages}
    existing_params = {p.name for p in module.parameters}
    existing_typedefs = {td.name for td in getattr(module, "typedefs", [])}

    for imp in imports:
        pkg = pkg_map.get(imp.package_name)
        if pkg is None:
            continue
        if imp.is_wildcard:
            for p in pkg.parameters:
                if p.name not in existing_params:
                    module.parameters.append(p)
                    existing_params.add(p.name)
            for td in pkg.typedefs:
                if td.name not in existing_typedefs:
                    module.typedefs.append(td)
                    existing_typedefs.add(td.name)
        else:
            for p in pkg.parameters:
                if p.name == imp.item_name and p.name not in existing_params:
                    module.parameters.append(p)
                    existing_params.add(p.name)
            for td in pkg.typedefs:
                if td.name == imp.item_name and td.name not in existing_typedefs:
                    module.typedefs.append(td)
                    existing_typedefs.add(td.name)

    def _iter_qualified_type_refs() -> Iterable[tuple[str, str]]:
        def _yield_ref(type_name: str | None) -> Iterable[tuple[str, str]]:
            if not isinstance(type_name, str) or "::" not in type_name:
                return
            pkg_name, item_name = type_name.rsplit("::", 1)
            if pkg_name and item_name:
                yield pkg_name, item_name

        for port in module.ports:
            yield from _yield_ref(getattr(port, "data_type", None))
        for net in module.nets:
            yield from _yield_ref(getattr(net, "type_name", None))
            yield from _yield_ref(getattr(net, "data_type", None))
        for var in module.variables:
            yield from _yield_ref(getattr(var, "type_name", None))
            yield from _yield_ref(getattr(var, "data_type", None))
        for td in getattr(module, "typedefs", []) or []:
            st = getattr(td, "struct_type", None)
            if st is not None:
                for field in getattr(st, "fields", []) or []:
                    yield from _yield_ref(getattr(field, "data_type", None))
            et = getattr(td, "enum_type", None)
            if et is not None:
                yield from _yield_ref(getattr(et, "base_type", None))

    for pkg_name, item_name in _iter_qualified_type_refs():
        pkg = pkg_map.get(pkg_name)
        if pkg is None:
            continue
        for p in pkg.parameters:
            if p.name == item_name and p.name not in existing_params:
                module.parameters.append(p)
                existing_params.add(p.name)
        for td in pkg.typedefs:
            if td.name == item_name and td.name not in existing_typedefs:
                module.typedefs.append(td)
                existing_typedefs.add(td.name)


def _resolve_typedef_widths(module: Module) -> None:
    """Resolve widths for ports/nets/variables whose type is a typedef with a known Range.

    After ``resolve_sv_imports`` has copied package typedefs into the module,
    this function evaluates typedef Range expressions (e.g. ``[IbexMuBiWidth-1:0]``)
    using the module's parameter environment and sets ``width`` on any
    port/net/variable that references the typedef but has no explicit width.

    Handles simple type alias typedefs (``typedef logic [3:0] foo_t;``),
    enum typedefs (``typedef enum logic [3:0] { ... } bar_t;``), and
    packed struct typedefs (``typedef struct packed { ... } baz_t;``).
    """
    typedefs = getattr(module, "typedefs", None)
    if not typedefs:
        return

    # Build typedef map: name -> Range (resolved or raw)
    td_width_map: dict[str, Range] = {}
    struct_typedefs: list = []
    for td in typedefs:
        # Simple type alias: typedef logic [N:M] name_t;
        w = getattr(td, "width", None)
        if w is not None:
            td_width_map[td.name] = w
            continue
        # Enum typedef: typedef enum logic [N:M] { ... } name_t;
        et = getattr(td, "enum_type", None)
        if et is not None and getattr(et, "width", None) is not None:
            td_width_map[td.name] = et.width
            continue
        # Packed struct typedef: typedef struct packed { ... } name_t;
        st = getattr(td, "struct_type", None)
        if st is not None and getattr(st, "packed", False):
            struct_typedefs.append(td)

    # Build parameter environment for evaluating Range expressions
    env = _build_param_env(module)

    # Resolve alias/enum type widths to concrete Ranges first (needed for struct fields)
    resolved_widths: dict[str, int] = {}
    for td_name, raw_range in td_width_map.items():
        try:
            msb_val = _eval_const_expr(raw_range.msb, env)
            lsb_val = _eval_const_expr(raw_range.lsb, env)
            resolved_widths[td_name] = int(msb_val) - int(lsb_val) + 1
        except (ValueError, TypeError):
            pass

    # Now resolve struct typedefs — struct fields may reference other typedefs
    for td in struct_typedefs:
        st = td.struct_type
        total = 0
        ok = True
        for fld in st.fields:
            if fld.width is not None:
                try:
                    msb = _eval_const_expr(fld.width.msb, env)
                    lsb = _eval_const_expr(fld.width.lsb, env)
                    total += int(msb) - int(lsb) + 1
                    continue
                except (ValueError, TypeError):
                    pass
            # Field has no explicit width — check if its type is a known typedef
            dt = getattr(fld, "data_type", None)
            fw = 0
            if dt and dt in resolved_widths:
                fw = resolved_widths[dt]
            elif dt and f"$bits:{dt}" in env:
                fw = env[f"$bits:{dt}"]
            elif dt in ("logic", "bit", "reg"):
                fw = 1
            else:
                fw = 1
            # Set the resolved width on the field so _field_width/compute_layout work
            if fw > 1 and fld.width is None:
                fld.width = Range(Literal(fw - 1), Literal(0))
            total += fw
        if ok and total > 0:
            td_width_map[td.name] = Range(Literal(total - 1), Literal(0))
            resolved_widths[td.name] = total

    for td_name, raw_range in td_width_map.items():
        try:
            msb_val = _eval_const_expr(raw_range.msb, env)
            lsb_val = _eval_const_expr(raw_range.lsb, env)
            resolved_range = Range(Literal(int(msb_val)), Literal(int(lsb_val)))
        except (ValueError, TypeError):
            continue

        # Resolve port widths (handle scoped names like "ibex_pkg::pc_sel_e")
        for port in module.ports:
            if port.data_type is not None:
                dt = port.data_type
                bare = dt.rsplit("::", 1)[-1] if "::" in dt else dt
                if bare == td_name:
                    port.width = resolved_range

        # Resolve function/task port widths
        for func in getattr(module, "functions", []):
            for port in func.ports:
                if port.data_type is None:
                    continue
                dt = port.data_type
                bare = dt.rsplit("::", 1)[-1] if "::" in dt else dt
                if bare == td_name:
                    port.width = resolved_range
        for task in getattr(module, "tasks", []):
            for port in task.ports:
                if port.data_type is None:
                    continue
                dt = port.data_type
                bare = dt.rsplit("::", 1)[-1] if "::" in dt else dt
                if bare == td_name:
                    port.width = resolved_range

        # Resolve net widths
        for net in module.nets:
            tn = getattr(net, "type_name", None)
            if tn is not None:
                bare = tn.rsplit("::", 1)[-1] if "::" in tn else tn
                if bare == td_name:
                    net.width = resolved_range

        # Resolve variable widths
        for var in module.variables:
            tn = getattr(var, "type_name", None)
            if tn is not None:
                bare = tn.rsplit("::", 1)[-1] if "::" in tn else tn
                if bare == td_name:
                    var.width = resolved_range


def _next_genblk(unnamed_ct: list[int]) -> str:
    """Return the next auto-generated block name (genblk1, genblk2, ...)."""
    unnamed_ct[0] += 1
    return f"genblk{unnamed_ct[0]}"


def _elaborate_one(
    module: Module,
    gen: GenerateFor | GenerateIf | GenerateCase | GenvarDecl,
    env: dict[str, int],
    unnamed_ct: list[int],
    parent_scope: str = "",
    ancestor_locals: dict[str, str] | None = None,
) -> None:
    """Dispatch elaboration for a single generate construct."""
    if isinstance(gen, GenvarDecl):
        return  # declarations only — no items to produce
    if isinstance(gen, GenerateFor):
        _elaborate_generate_for(module, gen, env, unnamed_ct, parent_scope, ancestor_locals)
    elif isinstance(gen, GenerateIf):
        _elaborate_generate_if(module, gen, env, unnamed_ct, parent_scope, ancestor_locals)
    elif isinstance(gen, GenerateCase):
        _elaborate_generate_case(module, gen, env, unnamed_ct, parent_scope, ancestor_locals)


def _elaborate_generate_for(
    module: Module,
    gen: GenerateFor,
    env: dict[str, int],
    unnamed_ct: list[int],
    parent_scope: str = "",
    ancestor_locals: dict[str, str] | None = None,
) -> None:
    """Unroll a generate-for loop and promote each iteration's items."""
    block_name = gen.body.name if gen.body.name else _next_genblk(unnamed_ct)
    env[gen.genvar] = _eval_const_expr(gen.init_value, env)

    for _ in range(65536):  # safety limit
        if not _eval_const_expr(gen.condition, env):
            break
        current_val = env[gen.genvar]
        scope_part = f"{block_name}[{current_val}]"
        scope = f"{parent_scope}.{scope_part}" if parent_scope else scope_part
        _promote_block_items(
            module,
            gen.body,
            scope,
            env,
            unnamed_ct,
            genvar_name=gen.genvar,
            genvar_val=current_val,
            ancestor_locals=ancestor_locals,
        )
        env[gen.genvar] = _compute_genvar_update(gen, env)

    env.pop(gen.genvar, None)


def _elaborate_generate_if(
    module: Module,
    gen: GenerateIf,
    env: dict[str, int],
    unnamed_ct: list[int],
    parent_scope: str = "",
    ancestor_locals: dict[str, str] | None = None,
) -> None:
    """Select and promote the matching branch of a generate-if."""
    cond_val = _eval_const_expr(gen.condition, env)
    body = gen.then_body if cond_val else gen.else_body
    if body:
        scope_part = body.name or ""
        if scope_part:
            scope = f"{parent_scope}.{scope_part}" if parent_scope else scope_part
        else:
            scope = parent_scope
        _promote_block_items(module, body, scope, env, unnamed_ct, ancestor_locals=ancestor_locals)


def _elaborate_generate_case(
    module: Module,
    gen: GenerateCase,
    env: dict[str, int],
    unnamed_ct: list[int],
    parent_scope: str = "",
    ancestor_locals: dict[str, str] | None = None,
) -> None:
    """Select and promote the matching case item."""
    expr_val = _eval_const_expr(gen.expression, env)
    default_item = None
    for item in gen.items:
        if item.is_default:
            default_item = item
            continue
        for val_expr in item.values:
            if _eval_const_expr(val_expr, env) == expr_val:
                if item.body:
                    scope_part = item.body.name or ""
                    if scope_part:
                        scope = f"{parent_scope}.{scope_part}" if parent_scope else scope_part
                    else:
                        scope = parent_scope
                    _promote_block_items(module, item.body, scope, env, unnamed_ct, ancestor_locals=ancestor_locals)
                return
    if default_item and default_item.body:
        scope_part = default_item.body.name or ""
        if scope_part:
            scope = f"{parent_scope}.{scope_part}" if parent_scope else scope_part
        else:
            scope = parent_scope
        _promote_block_items(module, default_item.body, scope, env, unnamed_ct, ancestor_locals=ancestor_locals)


def _compute_genvar_update(gen: GenerateFor, env: dict[str, int]) -> int:  # noqa: PLR0911
    """Compute the next genvar value after one iteration."""
    current = env[gen.genvar]
    if gen.update_op in ("post++", "pre++"):
        return current + 1
    if gen.update_op in ("post--", "pre--"):
        return current - 1
    if gen.update is None:
        raise ValueError(f"GenerateFor update requires expression for op '{gen.update_op}'")
    update_val = _eval_const_expr(gen.update, env)
    if gen.update_op == "=":
        return update_val
    if gen.update_op == "+=":
        return current + update_val
    if gen.update_op == "-=":
        return current - update_val
    if gen.update_op == "*=":
        return current * update_val
    if gen.update_op == "/=":
        return current // update_val if update_val else 0
    if gen.update_op == "%=":
        return current % update_val if update_val else 0
    raise ValueError(f"Unknown genvar update op: {gen.update_op!r}")


def _promote_block_items(  # noqa: PLR0912, PLR0913
    module: Module,
    block: object,
    scope: str,
    env: dict[str, int],
    unnamed_ct: list[int],
    *,
    genvar_name: str | None = None,
    genvar_val: int | None = None,
    ancestor_locals: dict[str, str] | None = None,
) -> None:
    """Deep-copy items from a GenerateBlock and add them to *module*."""
    items = copy.deepcopy(block.items)

    # Substitute genvar references with literal values
    if genvar_name is not None:
        for item in items:
            _substitute_genvar(item, genvar_name, genvar_val)

    # Collect local signal names declared in the block (need scope prefix)
    local_signals: set[str] = set()
    if scope:
        for item in items:
            if isinstance(item, (Net, Variable)):
                local_signals.add(item.name)

    for item in items:
        if isinstance(item, Net):
            if scope and item.name in local_signals:
                item.name = f"{scope}.{item.name}"
            module.nets.append(item)
        elif isinstance(item, Variable):
            if scope and item.name in local_signals:
                item.name = f"{scope}.{item.name}"
            module.variables.append(item)
        elif isinstance(item, Parameter):
            if item.default_value is not None:
                try:
                    env[item.name] = _eval_const_expr(item.default_value, env)
                except (ValueError, TypeError):
                    pass
            module.parameters.append(item)
        elif isinstance(item, TypedefDecl):
            if not any(td.name == item.name for td in getattr(module, "typedefs", [])):
                module.typedefs.append(item)
        elif isinstance(item, ContinuousAssign):
            if ancestor_locals:
                _rename_identifiers(item, ancestor_locals)
            if scope and local_signals:
                _prefix_identifiers(item, scope, local_signals)
            module.continuous_assigns.append(item)
        elif isinstance(item, AlwaysBlock):
            if ancestor_locals:
                _rename_identifiers(item, ancestor_locals)
            if scope and local_signals:
                _prefix_identifiers(item, scope, local_signals)
            module.always_blocks.append(item)
        elif isinstance(item, InitialBlock):
            if ancestor_locals:
                _rename_identifiers(item, ancestor_locals)
            if scope and local_signals:
                _prefix_identifiers(item, scope, local_signals)
            module.initial_blocks.append(item)
        elif isinstance(item, Instance):
            if ancestor_locals:
                _rename_identifiers(item, ancestor_locals)
            if scope and local_signals:
                _prefix_identifiers(item, scope, local_signals)
            if scope:
                item.instance_name = f"{scope}.{item.instance_name}"
            module.instances.append(item)
        elif isinstance(item, (GenerateFor, GenerateIf, GenerateCase, GenvarDecl)):
            # Build accumulated ancestor locals for nested generates
            nested_ancestors = dict(ancestor_locals) if ancestor_locals else {}
            if scope:
                for sig in local_signals:
                    nested_ancestors[sig] = f"{scope}.{sig}"
            _elaborate_one(
                module,
                item,
                env,
                unnamed_ct,
                parent_scope=scope,
                ancestor_locals=nested_ancestors if nested_ancestors else None,
            )


def _substitute_genvar(root: VerilogNode, genvar_name: str, value: int) -> None:
    """Replace ``Identifier(genvar_name)`` with ``Literal(value)`` throughout the subtree."""
    index_pattern = re.compile(rf"\[\s*{re.escape(genvar_name)}\s*\]")

    def _replace_embedded_index(text: str) -> str:
        return index_pattern.sub(f"[{value}]", text)

    for slot in getattr(root, "__slots__", ()):
        attr = getattr(root, slot, None)
        if attr is None:
            continue
        if isinstance(attr, Identifier) and attr.name == genvar_name:
            setattr(root, slot, Literal(value, width=32))
        elif isinstance(attr, Identifier):
            attr.name = _replace_embedded_index(attr.name)
            if attr.hierarchy:
                attr.hierarchy = [_replace_embedded_index(part) for part in attr.hierarchy]
        elif isinstance(attr, VerilogNode):
            _substitute_genvar(attr, genvar_name, value)
        elif isinstance(attr, list):
            for i, item in enumerate(attr):
                if isinstance(item, Identifier) and item.name == genvar_name:
                    attr[i] = Literal(value, width=32)
                elif isinstance(item, Identifier):
                    item.name = _replace_embedded_index(item.name)
                    if item.hierarchy:
                        item.hierarchy = [_replace_embedded_index(part) for part in item.hierarchy]
                elif isinstance(item, VerilogNode):
                    _substitute_genvar(item, genvar_name, value)
