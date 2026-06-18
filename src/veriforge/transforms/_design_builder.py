"""Top-level design, module, interface, and package builders."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.behavioral import AlwaysBlock, InitialBlock
from ..model.design import Design, Module
from ..model.functions import FunctionDecl, TaskDecl
from ..model.generate import GenerateCase, GenerateFor, GenerateIf, GenvarDecl
from ..model.instances import Instance
from ..model.interface import Interface, Modport, ModportPort
from ..model.nets import Net
from ..model.package import ImportDecl, Package
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.specify import SpecifyBlock
from ..model.sv_types import TypedefDecl
from ..model.variables import Variable, VariableKind
from ._tree_utils import _loc_from_tree

BuildAlwaysFn = Callable[[Tree, str | None], AlwaysBlock | None]
BuildInitialFn = Callable[[Tree, str | None], InitialBlock | None]
ExtractAssignsFn = Callable[[Tree, str | None], list[ContinuousAssign]]
ExtractFunctionFn = Callable[[Tree, str | None], FunctionDecl | None]
ExtractGateFn = Callable[[Tree, str | None], list[Instance]]
ExtractGenerateCaseFn = Callable[[Tree, str | None], GenerateCase | None]
ExtractGenerateForFn = Callable[[Tree, str | None], GenerateFor | None]
ExtractGenerateIfFn = Callable[[Tree, str | None], GenerateIf | None]
ExtractImportFn = Callable[[Tree, str | None], list[ImportDecl]]
ExtractInstanceFn = Callable[[Tree, str | None], list[Instance]]
ExtractNetDeclFn = Callable[[Tree, str | None], tuple[list[Net], list[ContinuousAssign]]]
ExtractParametersFn = Callable[[Tree, str | None, bool], list[Parameter]]
ExtractPortsFn = Callable[[Tree, str | None], list[Port]]
ExtractRegDeclFn = Callable[[Tree, str | None, VariableKind], list[Variable]]
ExtractSvTypeDeclFn = Callable[[Tree, str | None], list[Variable]]
ExtractTaskFn = Callable[[Tree, str | None], TaskDecl | None]
ExtractTypedefFn = Callable[[Tree, str | None], TypedefDecl | None]
ExtractTypedVariableFn = Callable[[Tree, str | None, VariableKind], list[Variable]]

_ROOT_CONTAINERS = frozenset({"verilog", "source_text", "description"})
_VAR_KINDS: dict[str, VariableKind] = {
    "integer_declaration": VariableKind.INTEGER,
    "real_declaration": VariableKind.REAL,
    "realtime_declaration": VariableKind.REALTIME,
    "time_declaration": VariableKind.TIME,
    "event_declaration": VariableKind.EVENT,
}
_SV_REG_KINDS: dict[str, VariableKind] = {
    "byte_declaration": VariableKind.BYTE,
    "shortint_declaration": VariableKind.SHORTINT,
    "int_declaration": VariableKind.INT,
    "longint_declaration": VariableKind.LONGINT,
}
_GENERATE_BOUNDARIES = frozenset(
    {
        "loop_generate_construct",
        "if_generate_construct",
        "case_generate_construct",
        "generate_block",
        "specify_block",
    }
)
_MODULE_WRAPPER_NODES = frozenset(
    {
        "non_port_module_item",
        "module_item",
        "module_or_generate_item",
        "module_or_generate_item_declaration",
        "generate_region",
        "conditional_generate_construct",
    }
)
_MODULE_SKIP_NODES = frozenset({"attribute_instance", "port_declaration"})
_INTERFACE_WRAPPER_NODES = frozenset(
    {
        "interface_item",
        "module_or_generate_item",
        "module_or_generate_item_declaration",
    }
)
_INTERFACE_SKIP_NODES = frozenset({"attribute_instance"})
_UNMODELED_MODULE_ITEMS = frozenset({"dpi_import_export", "parameter_override", "specparam_declaration"})
_DIRECTION_TOKENS = {
    "KW_INPUT": PortDirection.INPUT,
    "KW_OUTPUT": PortDirection.OUTPUT,
    "KW_INOUT": PortDirection.INOUT,
}


@dataclass(frozen=True)
class _DesignBuilderCallbacks:
    extract_parameters: ExtractParametersFn
    extract_package_import_declaration: ExtractImportFn
    extract_ports_from_declarations: ExtractPortsFn
    extract_port_names: ExtractPortsFn
    extract_net_declaration: ExtractNetDeclFn
    extract_reg_declaration: ExtractRegDeclFn
    extract_typed_variable: ExtractTypedVariableFn
    extract_module_instantiation: ExtractInstanceFn
    extract_udp_as_instance: ExtractInstanceFn
    extract_gate_instantiation: ExtractGateFn
    extract_continuous_assign: ExtractAssignsFn
    extract_always_construct: BuildAlwaysFn
    extract_always_comb_construct: BuildAlwaysFn
    extract_always_ff_construct: BuildAlwaysFn
    extract_always_latch_construct: BuildAlwaysFn
    extract_initial_construct: BuildInitialFn
    extract_function_declaration: ExtractFunctionFn
    extract_task_declaration: ExtractTaskFn
    extract_genvar_declaration: Callable[[Tree, str | None], GenvarDecl]
    extract_loop_generate: ExtractGenerateForFn
    extract_if_generate: ExtractGenerateIfFn
    extract_case_generate: ExtractGenerateCaseFn
    extract_typedef_declaration: ExtractTypedefFn
    extract_import_declaration: ExtractImportFn
    extract_sv_type_declaration: ExtractSvTypeDeclFn


class _ModuleItems:
    """Accumulator for module body items during extraction."""

    __slots__ = (
        "always_blocks",
        "continuous_assigns",
        "functions",
        "generate_blocks",
        "imports",
        "initial_blocks",
        "instances",
        "nets",
        "parameters",
        "ports",
        "specify_blocks",
        "tasks",
        "typedefs",
        "variables",
    )

    def __init__(  # noqa: PLR0913
        self,
        parameters: list[Parameter],
        nets: list[Net],
        variables: list[Variable],
        ports: list[Port],
        instances: list[Instance],
        continuous_assigns: list[ContinuousAssign],
        always_blocks: list[AlwaysBlock],
        initial_blocks: list[InitialBlock],
        functions: list[FunctionDecl],
        tasks: list[TaskDecl],
        generate_blocks: list,
        specify_blocks: list[SpecifyBlock],
        typedefs: list[TypedefDecl] | None = None,
        imports: list[ImportDecl] | None = None,
    ):
        self.parameters = parameters
        self.nets = nets
        self.variables = variables
        self.ports = ports
        self.instances = instances
        self.continuous_assigns = continuous_assigns
        self.always_blocks = always_blocks
        self.initial_blocks = initial_blocks
        self.functions = functions
        self.tasks = tasks
        self.generate_blocks = generate_blocks
        self.specify_blocks = specify_blocks
        self.typedefs: list[TypedefDecl] = typedefs if typedefs is not None else []
        self.imports: list[ImportDecl] = imports if imports is not None else []


@dataclass
class _ModuleContext:
    name: str = ""
    parameters: list[Parameter] = None  # type: ignore[assignment]
    ports: list[Port] = None  # type: ignore[assignment]
    nets: list[Net] = None  # type: ignore[assignment]
    variables: list[Variable] = None  # type: ignore[assignment]
    instances: list[Instance] = None  # type: ignore[assignment]
    continuous_assigns: list[ContinuousAssign] = None  # type: ignore[assignment]
    always_blocks: list[AlwaysBlock] = None  # type: ignore[assignment]
    initial_blocks: list[InitialBlock] = None  # type: ignore[assignment]
    functions: list[FunctionDecl] = None  # type: ignore[assignment]
    tasks: list[TaskDecl] = None  # type: ignore[assignment]
    generate_blocks: list = None  # type: ignore[assignment]
    specify_blocks: list[SpecifyBlock] = None  # type: ignore[assignment]
    typedefs: list[TypedefDecl] = None  # type: ignore[assignment]
    imports: list[ImportDecl] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = []
        self.ports = []
        self.nets = []
        self.variables = []
        self.instances = []
        self.continuous_assigns = []
        self.always_blocks = []
        self.initial_blocks = []
        self.functions = []
        self.tasks = []
        self.generate_blocks = []
        self.specify_blocks = []
        self.typedefs = []
        self.imports = []


@dataclass
class _InterfaceContext:
    name: str = ""
    parameters: list[Parameter] = None  # type: ignore[assignment]
    nets: list[Net] = None  # type: ignore[assignment]
    variables: list[Variable] = None  # type: ignore[assignment]
    continuous_assigns: list[ContinuousAssign] = None  # type: ignore[assignment]
    modports: list[Modport] = None  # type: ignore[assignment]
    typedefs: list[TypedefDecl] = None  # type: ignore[assignment]
    imports: list[ImportDecl] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = []
        self.nets = []
        self.variables = []
        self.continuous_assigns = []
        self.modports = []
        self.typedefs = []
        self.imports = []


@dataclass
class _PackageContext:
    name: str = ""
    parameters: list[Parameter] = None  # type: ignore[assignment]
    typedefs: list[TypedefDecl] = None  # type: ignore[assignment]
    functions: list[FunctionDecl] = None  # type: ignore[assignment]
    tasks: list[TaskDecl] = None  # type: ignore[assignment]
    imports: list[ImportDecl] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = []
        self.typedefs = []
        self.functions = []
        self.tasks = []
        self.imports = []


def _build_design(
    tree: Tree,
    source_file: str | None,
    source_text: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> Design:
    """Build a Design from a top-level parse tree."""
    modules: list[Module] = []
    interfaces: list[Interface] = []
    packages: list[Package] = []

    for node in _iter_design_declarations(tree):
        if node.data == "module_declaration":
            modules.append(_build_module(node, source_file, source_text, callbacks))
        elif node.data == "interface_declaration":
            interfaces.append(_build_interface(node, source_file, callbacks))
        elif node.data == "package_declaration":
            packages.append(_build_package(node, source_file, callbacks))

    source_files = [source_file] if source_file else []
    return Design(
        modules=modules,
        source_files=source_files,
        interfaces=interfaces,
        packages=packages,
        loc=_loc_from_tree(tree, source_file),
    )


def _iter_design_declarations(tree: Tree) -> Iterable[Tree]:
    if tree.data in {"module_declaration", "interface_declaration", "package_declaration"}:
        yield tree
        return

    if tree.data in _ROOT_CONTAINERS:
        yield from _iter_matching_declarations(tree.iter_subtrees_topdown())
        return

    yield from _iter_matching_declarations(tree.iter_subtrees_topdown())


def _iter_matching_declarations(nodes: Iterable[Tree]) -> Iterable[Tree]:
    for node in nodes:
        if node.data in {"module_declaration", "interface_declaration", "package_declaration"}:
            yield node


def _build_module(
    tree: Tree,
    source_file: str | None,
    source_text: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> Module:
    """Build a Module from a module_declaration tree node."""
    ctx = _ModuleContext()

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER":
                ctx.name = str(child)
        elif isinstance(child, Tree):
            _update_module_context(ctx, child, source_file, source_text, callbacks)

    module = Module(
        name=ctx.name,
        parameters=ctx.parameters,
        ports=ctx.ports,
        nets=ctx.nets,
        variables=ctx.variables,
        instances=ctx.instances,
        continuous_assigns=ctx.continuous_assigns,
        loc=_loc_from_tree(tree, source_file),
    )
    module.always_blocks = ctx.always_blocks
    module.initial_blocks = ctx.initial_blocks
    module.functions = ctx.functions
    module.tasks = ctx.tasks
    module.generate_blocks = ctx.generate_blocks
    module.specify_blocks = ctx.specify_blocks
    module.typedefs = ctx.typedefs
    module.imports = ctx.imports
    _attach_module_children(module, ctx)
    return module


def _update_module_context(
    ctx: _ModuleContext,
    child: Tree,
    source_file: str | None,
    source_text: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> None:
    if child.data == "module_parameter_port_list":
        ctx.parameters.extend(callbacks.extract_parameters(child, source_file, False))
    elif child.data == "package_import_declaration":
        ctx.imports.extend(callbacks.extract_package_import_declaration(child, source_file))
    elif child.data == "list_of_port_declarations":
        ctx.ports.extend(callbacks.extract_ports_from_declarations(child, source_file))
    elif child.data == "list_of_ports":
        ctx.ports.extend(callbacks.extract_port_names(child, source_file))
    elif child.data in ("non_port_module_item", "module_item"):
        items = _ModuleItems(
            ctx.parameters,
            ctx.nets,
            ctx.variables,
            ctx.ports,
            ctx.instances,
            ctx.continuous_assigns,
            ctx.always_blocks,
            ctx.initial_blocks,
            ctx.functions,
            ctx.tasks,
            ctx.generate_blocks,
            ctx.specify_blocks,
            ctx.typedefs,
            ctx.imports,
        )
        _extract_module_items(child, source_file, items, source_text, callbacks)


def _attach_module_children(module: Module, ctx: _ModuleContext) -> None:
    for child in (
        *ctx.parameters,
        *ctx.ports,
        *ctx.nets,
        *ctx.variables,
        *ctx.instances,
        *ctx.continuous_assigns,
        *ctx.always_blocks,
        *ctx.initial_blocks,
        *ctx.functions,
        *ctx.tasks,
        *ctx.generate_blocks,
        *ctx.specify_blocks,
        *ctx.typedefs,
        *ctx.imports,
    ):
        child.parent = module


def _build_interface(
    tree: Tree,
    source_file: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> Interface:
    """Build an Interface from an interface_declaration tree node."""
    ctx = _InterfaceContext()

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER":
                ctx.name = str(child)
        elif isinstance(child, Tree):
            if child.data == "module_parameter_port_list":
                ctx.parameters.extend(callbacks.extract_parameters(child, source_file, False))
            elif child.data == "interface_item":
                _extract_interface_item(child, source_file, ctx, callbacks)

    iface = Interface(
        name=ctx.name,
        parameters=ctx.parameters,
        nets=ctx.nets,
        variables=ctx.variables,
        continuous_assigns=ctx.continuous_assigns,
        modports=ctx.modports,
        typedefs=ctx.typedefs,
        imports=ctx.imports,
        loc=_loc_from_tree(tree, source_file),
    )
    _attach_interface_children(iface, ctx)
    return iface


def _attach_interface_children(iface: Interface, ctx: _InterfaceContext) -> None:
    for child in (
        *ctx.parameters,
        *ctx.nets,
        *ctx.variables,
        *ctx.continuous_assigns,
        *ctx.modports,
        *ctx.typedefs,
        *ctx.imports,
    ):
        child.parent = iface


def _extract_interface_item(
    tree: Tree,
    source_file: str | None,
    ctx: _InterfaceContext,
    callbacks: _DesignBuilderCallbacks,
) -> None:
    """Extract declarations from an interface_item node."""
    for node in _iter_interface_constructs(tree):
        if node.data == "net_declaration":
            decl_nets, decl_assigns = callbacks.extract_net_declaration(node, source_file)
            ctx.nets.extend(decl_nets)
            ctx.continuous_assigns.extend(decl_assigns)
        elif node.data == "reg_declaration":
            ctx.variables.extend(callbacks.extract_reg_declaration(node, source_file, VariableKind.REG))
        elif node.data == "continuous_assign":
            ctx.continuous_assigns.extend(callbacks.extract_continuous_assign(node, source_file))
        elif node.data == "local_parameter_declaration":
            ctx.parameters.extend(callbacks.extract_parameters(node, source_file, True))
        elif node.data == "parameter_declaration":
            ctx.parameters.extend(callbacks.extract_parameters(node, source_file, False))
        elif node.data == "typedef_declaration":
            typedef = callbacks.extract_typedef_declaration(node, source_file)
            if typedef:
                ctx.typedefs.append(typedef)
        elif node.data == "modport_declaration":
            modport = _extract_modport_declaration(node, source_file)
            if modport:
                ctx.modports.append(modport)
        elif node.data == "import_declaration":
            ctx.imports.extend(callbacks.extract_import_declaration(node, source_file))
        else:
            loc = _loc_from_tree(node, source_file)
            raise ValueError(f"Unhandled interface item node '{node.data}' at {loc}")


def _iter_interface_constructs(root: Tree) -> Iterable[Tree]:
    for child in root.children:
        if isinstance(child, Tree):
            if child.data in _INTERFACE_SKIP_NODES:
                continue
            if child.data in _INTERFACE_WRAPPER_NODES:
                yield from _iter_interface_constructs(child)
            else:
                yield child


def _extract_modport_declaration(tree: Tree, source_file: str | None) -> Modport | None:
    """Extract a Modport from a modport_declaration tree node."""
    name = ""
    ports: list[ModportPort] = []

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER" and not name:
                name = str(child)
        elif isinstance(child, Tree) and child.data == "modport_port_declaration":
            direction, sig_name = _extract_modport_port(child)
            if sig_name:
                ports.append(
                    ModportPort(
                        name=sig_name,
                        direction=direction,
                        loc=_loc_from_tree(child, source_file),
                    )
                )

    if name:
        return Modport(name=name, ports=ports, loc=_loc_from_tree(tree, source_file))
    return None


def _extract_modport_port(tree: Tree) -> tuple[PortDirection, str]:
    """Extract direction and signal name from a modport_port_declaration node."""
    direction = PortDirection.INPUT
    sig_name = ""
    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER":
                sig_name = str(child)
            elif child.type in _DIRECTION_TOKENS:
                direction = _DIRECTION_TOKENS[child.type]
    return direction, sig_name


def _build_package(
    tree: Tree,
    source_file: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> Package:
    """Build a Package from a package_declaration tree node."""
    ctx = _PackageContext()

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER":
                ctx.name = str(child)
        elif isinstance(child, Tree) and child.data == "package_item":
            _extract_package_item(child, source_file, ctx, callbacks)

    pkg = Package(
        name=ctx.name,
        parameters=ctx.parameters,
        typedefs=ctx.typedefs,
        functions=ctx.functions,
        tasks=ctx.tasks,
        imports=ctx.imports,
        loc=_loc_from_tree(tree, source_file),
    )
    _attach_package_children(pkg, ctx)
    return pkg


def _attach_package_children(pkg: Package, ctx: _PackageContext) -> None:
    for child in (*ctx.parameters, *ctx.typedefs, *ctx.functions, *ctx.tasks, *ctx.imports):
        child.parent = pkg


def _extract_package_item(
    tree: Tree,
    source_file: str | None,
    ctx: _PackageContext,
    callbacks: _DesignBuilderCallbacks,
) -> None:
    """Extract declarations from a package_item node."""
    for child in tree.children:
        if not isinstance(child, Tree):
            continue
        if child.data == "parameter_declaration":
            ctx.parameters.extend(callbacks.extract_parameters(child, source_file, False))
        elif child.data == "local_parameter_declaration":
            ctx.parameters.extend(callbacks.extract_parameters(child, source_file, True))
        elif child.data == "typedef_declaration":
            typedef = callbacks.extract_typedef_declaration(child, source_file)
            if typedef:
                ctx.typedefs.append(typedef)
        elif child.data == "function_declaration":
            func = callbacks.extract_function_declaration(child, source_file)
            if func:
                ctx.functions.append(func)
        elif child.data == "task_declaration":
            task = callbacks.extract_task_declaration(child, source_file)
            if task:
                ctx.tasks.append(task)
        elif child.data == "import_declaration":
            ctx.imports.extend(callbacks.extract_import_declaration(child, source_file))
        else:
            loc = _loc_from_tree(child, source_file)
            raise ValueError(f"Unhandled package item node '{child.data}' at {loc}")


def _extract_module_items(  # noqa: PLR0912
    tree: Tree,
    source_file: str | None,
    items: _ModuleItems,
    source_text: str | None,
    callbacks: _DesignBuilderCallbacks,
) -> None:
    """Extract nets, variables, parameters, instances, and assigns from module item nodes."""
    for node in _iter_module_constructs(tree):
        if node.data == "net_declaration":
            decl_nets, decl_assigns = callbacks.extract_net_declaration(node, source_file)
            items.nets.extend(decl_nets)
            items.continuous_assigns.extend(decl_assigns)
        elif node.data == "reg_declaration":
            items.variables.extend(callbacks.extract_reg_declaration(node, source_file, VariableKind.REG))
        elif node.data == "logic_declaration":
            items.variables.extend(callbacks.extract_reg_declaration(node, source_file, VariableKind.LOGIC))
        elif node.data == "bit_declaration":
            items.variables.extend(callbacks.extract_reg_declaration(node, source_file, VariableKind.BIT))
        elif node.data in _VAR_KINDS:
            items.variables.extend(callbacks.extract_typed_variable(node, source_file, _VAR_KINDS[node.data]))
        elif node.data in _SV_REG_KINDS:
            items.variables.extend(callbacks.extract_reg_declaration(node, source_file, _SV_REG_KINDS[node.data]))
        elif node.data == "local_parameter_declaration":
            items.parameters.extend(callbacks.extract_parameters(node, source_file, True))
        elif node.data == "parameter_declaration":
            items.parameters.extend(callbacks.extract_parameters(node, source_file, False))
        elif node.data == "module_instantiation":
            items.instances.extend(callbacks.extract_module_instantiation(node, source_file))
        elif node.data == "udp_instantiation":
            items.instances.extend(callbacks.extract_udp_as_instance(node, source_file))
        elif node.data == "gate_instantiation":
            items.instances.extend(callbacks.extract_gate_instantiation(node, source_file))
        elif node.data == "continuous_assign":
            items.continuous_assigns.extend(callbacks.extract_continuous_assign(node, source_file))
        elif node.data in _UNMODELED_MODULE_ITEMS:
            continue
        else:
            _extract_non_declaration_module_item(node, source_file, source_text, items, callbacks)


def _extract_non_declaration_module_item(  # noqa: PLR0912
    node: Tree,
    source_file: str | None,
    source_text: str | None,
    items: _ModuleItems,
    callbacks: _DesignBuilderCallbacks,
) -> None:
    if node.data == "always_construct":
        _append_optional(items.always_blocks, callbacks.extract_always_construct(node, source_file))
    elif node.data == "always_comb_construct":
        _append_optional(items.always_blocks, callbacks.extract_always_comb_construct(node, source_file))
    elif node.data == "always_ff_construct":
        _append_optional(items.always_blocks, callbacks.extract_always_ff_construct(node, source_file))
    elif node.data == "always_latch_construct":
        _append_optional(items.always_blocks, callbacks.extract_always_latch_construct(node, source_file))
    elif node.data == "initial_construct":
        _append_optional(items.initial_blocks, callbacks.extract_initial_construct(node, source_file))
    elif node.data == "function_declaration":
        _append_optional(items.functions, callbacks.extract_function_declaration(node, source_file))
    elif node.data == "task_declaration":
        _append_optional(items.tasks, callbacks.extract_task_declaration(node, source_file))
    elif node.data == "genvar_declaration":
        items.generate_blocks.append(callbacks.extract_genvar_declaration(node, source_file))
    elif node.data == "loop_generate_construct":
        _append_optional(items.generate_blocks, callbacks.extract_loop_generate(node, source_file))
    elif node.data == "if_generate_construct":
        _append_optional(items.generate_blocks, callbacks.extract_if_generate(node, source_file))
    elif node.data == "case_generate_construct":
        _append_optional(items.generate_blocks, callbacks.extract_case_generate(node, source_file))
    elif node.data == "specify_block":
        items.specify_blocks.append(_extract_specify_block(node, source_file, source_text))
    elif node.data == "typedef_declaration":
        _append_optional(items.typedefs, callbacks.extract_typedef_declaration(node, source_file))
    elif node.data == "import_declaration":
        items.imports.extend(callbacks.extract_import_declaration(node, source_file))
    elif node.data == "sv_type_declaration":
        items.variables.extend(callbacks.extract_sv_type_declaration(node, source_file))
    else:
        loc = _loc_from_tree(node, source_file)
        raise ValueError(f"Unhandled module item node '{node.data}' at {loc}")


def _append_optional(target: list, item) -> None:
    if item:
        target.append(item)


def _extract_specify_block(node: Tree, source_file: str | None, source_text: str | None) -> SpecifyBlock:
    specify_src: str | None = None
    if source_text and hasattr(node.meta, "line") and hasattr(node.meta, "end_line"):
        src_lines = source_text.splitlines(keepends=True)
        start_line = node.meta.line - 1
        end_line = node.meta.end_line
        if 0 <= start_line < len(src_lines) and end_line <= len(src_lines):
            specify_src = "".join(src_lines[start_line:end_line]).strip()
    return SpecifyBlock(
        raw_tree=node,
        source_text=specify_src,
        loc=_loc_from_tree(node, source_file),
    )


def _iter_module_constructs(root: Tree) -> Iterable[Tree]:
    """Yield construct-level nodes, descending wrappers and stopping at generate/specify boundaries."""
    for child in root.children:
        if isinstance(child, Tree):
            if child.data in _MODULE_SKIP_NODES:
                continue
            if child.data in _GENERATE_BOUNDARIES:
                yield child
            elif child.data in _MODULE_WRAPPER_NODES:
                yield from _iter_module_constructs(child)
            else:
                yield child
