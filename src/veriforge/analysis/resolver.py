"""Name resolution, cross-linking, and driver/load analysis.

This module implements the core Layer 3 analysis passes that populate
cross-references on the semantic model objects in-place.

Analysis passes (run in order by analyze_design):
    1. link_instances — resolve Instance.resolved_module
    2. resolve_names — build symbol tables, resolve Identifier.resolved
    3. resolve_port_connections — resolve PortConnection.resolved_port
    4. analyze_connectivity — populate Net.drivers/loads, Variable.drivers/loads
"""

from __future__ import annotations

from ..model.assignments import ContinuousAssign  # noqa: F401 — used by tests via isinstance
from ..model.base import VerilogNode
from ..model.behavioral import AlwaysBlock, InitialBlock
from ..model.design import Design, Module
from ..model.expressions import (
    BitSelect,
    Concatenation,
    Expression,
    Identifier,
    RangeSelect,
)
from ..model.instances import Instance
from ..model.nets import Net
from ..model.ports import PortDirection
from ..model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    EventControl,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from ..model.variables import Variable


# ---------------------------------------------------------------------------
# Driver / Load types
# ---------------------------------------------------------------------------


class Driver:
    """Something that drives a net or variable.

    Attributes:
        source: The VerilogNode responsible for driving the signal.
            Typically a ContinuousAssign, AlwaysBlock, InitialBlock, or Instance.
    """

    __slots__ = ("source",)

    def __init__(self, source: VerilogNode):
        self.source = source

    def __repr__(self) -> str:
        return f"Driver({type(self.source).__name__})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Driver):
            return NotImplemented
        return self.source is other.source

    def __hash__(self) -> int:
        return id(self.source)


class Load:
    """Something that reads a net or variable.

    Attributes:
        consumer: The VerilogNode that reads the signal.
            Typically a ContinuousAssign, AlwaysBlock, InitialBlock, or Instance.
    """

    __slots__ = ("consumer",)

    def __init__(self, consumer: VerilogNode):
        self.consumer = consumer

    def __repr__(self) -> str:
        return f"Load({type(self.consumer).__name__})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Load):
            return NotImplemented
        return self.consumer is other.consumer

    def __hash__(self) -> int:
        return id(self.consumer)


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


def analyze_design(design: Design) -> None:
    """Run all analysis passes on a design, mutating model objects in-place.

    Pass order:
        1. link_instances — resolve Instance.resolved_module
        2. resolve_names — resolve Identifier.resolved
        3. resolve_port_connections — resolve PortConnection.resolved_port
        4. analyze_connectivity — populate drivers/loads on Net and Variable
    """
    link_instances(design)
    resolve_names(design)
    resolve_port_connections(design)
    analyze_connectivity(design)


# ---------------------------------------------------------------------------
# Pass 1: Instance linking
# ---------------------------------------------------------------------------


def link_instances(design: Design) -> None:
    """Link Instance.resolved_module to the actual Module object.

    Builds a module lookup table from the Design, then sets
    ``inst.resolved_module`` for every Instance in every Module.
    """
    module_map: dict[str, Module] = {m.name: m for m in design.modules}

    for module in design.modules:
        for inst in module.instances:
            target = module_map.get(inst.module_name)
            if target is not None:
                inst.resolved_module = target


# ---------------------------------------------------------------------------
# Pass 2: Name resolution
# ---------------------------------------------------------------------------


def resolve_names(design: Design) -> None:
    """Resolve Identifier.resolved for every identifier in the design.

    For each module, builds a symbol table mapping names to their
    declarations (Port, Net, Variable, Parameter), then walks every
    Identifier in the module tree and sets ``identifier.resolved``.
    """
    for module in design.modules:
        symbols = _build_symbol_table(module)
        _resolve_identifiers_in_module(module, symbols)


def _build_symbol_table(module: Module) -> dict[str, VerilogNode]:
    """Build a name → declaration mapping for a module.

    Priority order (later entries overwrite earlier):
        1. Ports (lowest — may be overwritten by explicit net/var)
        2. Parameters / localparams
        3. Nets (wire, tri, etc.)
        4. Variables (reg, integer, etc.) — highest priority
    """
    table: dict[str, VerilogNode] = {}

    for port in module.ports:
        table[port.name] = port

    for param in module.parameters:
        table[param.name] = param

    for net in module.nets:
        table[net.name] = net

    for var in module.variables:
        table[var.name] = var

    return table


def _resolve_identifiers_in_module(
    module: Module,
    symbols: dict[str, VerilogNode],
) -> None:
    """Walk all nodes in a module and resolve Identifier references."""
    for node in module.walk():
        if isinstance(node, Identifier):
            target = symbols.get(node.name)
            if target is not None:
                node.resolved = target


# ---------------------------------------------------------------------------
# Pass 3: Port connection resolution
# ---------------------------------------------------------------------------


def resolve_port_connections(design: Design) -> None:
    """Resolve PortConnection.resolved_port for every instance.

    Requires link_instances to have been run first so that
    ``inst.resolved_module`` is populated.
    """
    for module in design.modules:
        for inst in module.instances:
            _resolve_inst_port_connections(inst)


def _resolve_inst_port_connections(inst: Instance) -> None:
    """Resolve port connections for a single instance."""
    target_module = inst.resolved_module
    if target_module is None:
        return

    for i, conn in enumerate(inst.port_connections):
        if conn.is_named and conn.port_name:
            # Named: .port_name(expr) → look up by name
            port = target_module.get_port(conn.port_name)
            if port is not None:
                conn.resolved_port = port
        elif not conn.is_named:
            # Positional: match by index
            if i < len(target_module.ports):
                conn.resolved_port = target_module.ports[i]


# ---------------------------------------------------------------------------
# Pass 4: Driver / load connectivity analysis
# ---------------------------------------------------------------------------


def analyze_connectivity(design: Design) -> None:
    """Populate Net.drivers/loads and Variable.drivers/loads.

    Requires resolve_names and resolve_port_connections to have run first.

    Driver sources:
        - Continuous assign LHS
        - Blocking / nonblocking assign LHS in always/initial blocks
        - Instance output port connections

    Load sources:
        - Continuous assign RHS
        - Expression reads in always/initial block statements
        - Sensitivity list signals
        - Instance input port connections
    """
    for module in design.modules:
        symbols = _build_symbol_table(module)
        _clear_connectivity(module)
        _analyze_continuous_assigns(module, symbols)
        _analyze_behavioral_blocks(module, symbols)
        _analyze_instance_connections(module, symbols)


def _clear_connectivity(module: Module) -> None:
    """Clear existing driver/load lists (idempotent re-analysis)."""
    for net in module.nets:
        net.drivers.clear()
        net.loads.clear()
    for var in module.variables:
        var.drivers.clear()
        var.loads.clear()
    for port in module.ports:
        port.drivers.clear()
        port.loads.clear()


def _signal_for_name(
    name: str,
    symbols: dict[str, VerilogNode],
) -> VerilogNode | None:
    """Look up a signal by name.

    Nets and Variables win when an explicit declaration exists for the name.
    Ports are included as a fallback for combined output-reg/output-wire
    declarations where no separate net or variable is created by the parser.
    """
    from ..model.ports import Port

    target = symbols.get(name)
    if isinstance(target, (Net, Variable, Port)):
        return target
    return None


# ---- Continuous assigns ----


def _analyze_continuous_assigns(
    module: Module,
    symbols: dict[str, VerilogNode],
) -> None:
    """Analyze drivers and loads from continuous assignments."""
    for ca in module.continuous_assigns:
        _add_drivers_from_expression(ca.lhs, ca, symbols)
        _add_loads_from_expression(ca.rhs, ca, symbols)


# ---- Behavioral blocks ----


def _analyze_behavioral_blocks(
    module: Module,
    symbols: dict[str, VerilogNode],
) -> None:
    """Analyze drivers and loads from always/initial blocks."""
    for block in module.always_blocks:
        # Sensitivity list signals are loads
        for edge in block.sensitivity_list:
            _add_loads_from_expression(edge.signal, block, symbols)
        # Walk the body for assignment drivers and expression loads
        _analyze_statement(block.body, block, symbols)

    for init_block in module.initial_blocks:
        _analyze_statement(init_block.body, init_block, symbols)


def _analyze_statement(  # noqa: PLR0912
    stmt: Statement | None,
    block: AlwaysBlock | InitialBlock,
    symbols: dict[str, VerilogNode],
) -> None:
    """Recursively analyze a statement for drivers and loads.

    Assignment LHS → driver, all other expressions → load.
    The block (AlwaysBlock/InitialBlock) is recorded as the source/consumer.
    """
    if stmt is None:
        return

    if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
        _add_drivers_from_expression(stmt.lhs, block, symbols)
        _add_loads_from_expression(stmt.rhs, block, symbols)

    elif isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            _analyze_statement(s, block, symbols)

    elif isinstance(stmt, ParBlock):
        for s in stmt.statements:
            _analyze_statement(s, block, symbols)

    elif isinstance(stmt, IfStatement):
        _add_loads_from_expression(stmt.condition, block, symbols)
        _analyze_statement(stmt.then_body, block, symbols)
        _analyze_statement(stmt.else_body, block, symbols)

    elif isinstance(stmt, CaseStatement):
        _add_loads_from_expression(stmt.expression, block, symbols)
        for item in stmt.items:
            _analyze_case_item(item, block, symbols)

    elif isinstance(stmt, ForLoop):
        # init and update are BlockingAssign-like
        _add_drivers_from_expression(stmt.init.lhs, block, symbols)
        _add_loads_from_expression(stmt.init.rhs, block, symbols)
        _add_loads_from_expression(stmt.condition, block, symbols)
        _add_drivers_from_expression(stmt.update.lhs, block, symbols)
        _add_loads_from_expression(stmt.update.rhs, block, symbols)
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, WhileLoop):
        _add_loads_from_expression(stmt.condition, block, symbols)
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, ForeverLoop):
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, RepeatLoop):
        _add_loads_from_expression(stmt.count, block, symbols)
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, SystemTaskCall):
        for arg in stmt.arguments:
            _add_loads_from_expression(arg, block, symbols)

    elif isinstance(stmt, TaskEnable):
        for arg in stmt.arguments:
            _add_loads_from_expression(arg, block, symbols)

    elif isinstance(stmt, WaitStatement):
        _add_loads_from_expression(stmt.condition, block, symbols)
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, DelayControl):
        _add_loads_from_expression(stmt.delay, block, symbols)
        _analyze_statement(stmt.body, block, symbols)

    elif isinstance(stmt, EventControl):
        for edge in stmt.events:
            _add_loads_from_expression(edge.signal, block, symbols)
        _analyze_statement(stmt.body, block, symbols)


def _analyze_case_item(
    item: CaseItem,
    block: AlwaysBlock | InitialBlock,
    symbols: dict[str, VerilogNode],
) -> None:
    """Analyze a single case item for loads and drivers."""
    if item.values:
        for val in item.values:
            _add_loads_from_expression(val, block, symbols)
    _analyze_statement(item.body, block, symbols)


# ---- Instance connections ----


def _analyze_instance_connections(
    module: Module,
    symbols: dict[str, VerilogNode],
) -> None:
    """Analyze drivers and loads from instance port connections.

    Output ports drive the connected signal (driver).
    Input ports read the connected signal (load).
    Inout ports are both driver and load.
    """
    for inst in module.instances:
        for conn in inst.port_connections:
            if conn.expression is None:
                continue

            if conn.resolved_port is not None:
                direction = conn.resolved_port.direction
                if direction == PortDirection.OUTPUT:
                    _add_drivers_from_expression(conn.expression, inst, symbols)
                elif direction == PortDirection.INPUT:
                    _add_loads_from_expression(conn.expression, inst, symbols)
                elif direction == PortDirection.INOUT:
                    _add_drivers_from_expression(conn.expression, inst, symbols)
                    _add_loads_from_expression(conn.expression, inst, symbols)
            else:
                # Unresolved port — treat expression as both driver and load
                # (conservative: we don't know direction)
                _add_loads_from_expression(conn.expression, inst, symbols)


# ---- Expression walking helpers ----


def _add_drivers_from_expression(
    expr: Expression,
    source: VerilogNode,
    symbols: dict[str, VerilogNode],
) -> None:
    """Add driver entries for all signal identifiers reachable from an lvalue expression.

    For lvalues, the top-level identifiers are driven. We extract the
    base signal name from Identifier, BitSelect, RangeSelect, and Concatenation.
    """
    if isinstance(expr, Identifier):
        sig = _signal_for_name(expr.name, symbols)
        if sig is not None:
            driver = Driver(source=source)
            if not any(d.source is source for d in sig.drivers):  # type: ignore[attr-defined]
                sig.drivers.append(driver)  # type: ignore[attr-defined]

    elif isinstance(expr, (BitSelect, RangeSelect)):
        # The target is what's being driven
        _add_drivers_from_expression(expr.target, source, symbols)

    elif isinstance(expr, Concatenation):
        for part in expr.parts:
            _add_drivers_from_expression(part, source, symbols)


def _add_loads_from_expression(
    expr: Expression,
    consumer: VerilogNode,
    symbols: dict[str, VerilogNode],
) -> None:
    """Add load entries for all signal identifiers read by an expression.

    Walks the full expression tree and records a load for every Identifier
    that resolves to a Net or Variable.
    """
    for node in expr.walk():
        if isinstance(node, Identifier):
            sig = _signal_for_name(node.name, symbols)
            if sig is not None:
                load = Load(consumer=consumer)
                if not any(existing.consumer is consumer for existing in sig.loads):  # type: ignore[attr-defined]
                    sig.loads.append(load)  # type: ignore[attr-defined]
