"""Hierarchy graph and conservative wrapper classification."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.request import pathname2url

from ..model.assignments import ContinuousAssign
from ..model.base import SourceLocation
from ..model.design import Design, Module
from ..model.instances import Instance
from .diagnostics import RefactorDiagnostic
from ._refactor_utils import _UnionFind, _loc_range, _simple_identifier_name

HIERARCHY_WRAPPER_CLASSES = frozenset(
    {
        "pure_pass_through",
        "structural_wrapper",
        "behavioral_wrapper",
        "unknown_or_unsupported",
        "not_wrapper",
    }
)


@dataclass(frozen=True)
class WrapperInfo:
    """Wrapper classification and editor-facing actions for a module instance."""

    wrapper_class: str
    confidence: str
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    refactor_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "wrapperClass": self.wrapper_class,
            "confidence": self.confidence,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "refactorActions": list(self.refactor_actions),
        }


@dataclass
class HierarchyNode:
    """A resolved module or instance node in the hierarchy tree."""

    name: str
    module_name: str
    instance_path: str
    kind: str
    file: str = ""
    range: dict[str, object] = field(default_factory=dict)
    instance_file: str = ""
    instance_range: dict[str, object] = field(default_factory=dict)
    wrapper_info: WrapperInfo | None = None
    children: list[HierarchyNode] = field(default_factory=list)
    has_more_children: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object]
        if self.kind == "module":
            payload = {
                "name": self.name,
                "moduleName": self.module_name,
                "instancePath": self.instance_path,
                "file": self.file,
                "range": self.range,
                "children": [child.to_dict() for child in self.children],
                "hasMoreChildren": self.has_more_children,
            }
        else:
            payload = {
                "instanceName": self.name,
                "moduleName": self.module_name,
                "instancePath": self.instance_path,
                "file": self.file,
                "range": self.range,
                "instanceFile": self.instance_file,
                "instanceRange": self.instance_range,
                "children": [child.to_dict() for child in self.children],
                "hasMoreChildren": self.has_more_children,
            }
            if self.wrapper_info is not None:
                payload.update(self.wrapper_info.to_dict())
        return payload


@dataclass
class HierarchyGraph:
    """Hierarchy graph rooted at one or more modules."""

    roots: list[HierarchyNode]

    def wrapper_nodes(self) -> list[HierarchyNode]:
        return [
            node
            for node in self.iter_nodes()
            if node.wrapper_info is not None and node.wrapper_info.wrapper_class != "not_wrapper"
        ]

    def iter_nodes(self) -> list[HierarchyNode]:
        nodes: list[HierarchyNode] = []
        pending = list(reversed(self.roots))
        while pending:
            node = pending.pop()
            nodes.append(node)
            pending.extend(reversed(node.children))
        return nodes

    def to_dict(self) -> dict[str, object]:
        wrappers = [_wrapper_summary(node) for node in self.wrapper_nodes()]
        return {
            "roots": [root.to_dict() for root in self.roots],
            "wrappers": wrappers,
            "stats": {
                "roots": len(self.roots),
                "nodes": len(self.iter_nodes()),
                "wrappers": len(wrappers),
            },
        }


def build_hierarchy_graph(design: Design, *, top: str | None = None, max_depth: int | None = 8) -> HierarchyGraph:
    """Build a resolved hierarchy tree with wrapper metadata."""

    roots = _select_roots(design, top)
    return HierarchyGraph(
        roots=[_module_node(module, instance_path=module.name, stack=(), max_depth=max_depth) for module in roots]
    )


def classify_wrapper_module(module: Module | None) -> WrapperInfo:  # noqa: PLR0911
    """Classify a module as a wrapper candidate or explain why it is not safe."""

    if module is None:
        return WrapperInfo(
            wrapper_class="unknown_or_unsupported",
            confidence="blocked",
            diagnostics=(
                RefactorDiagnostic("unresolved-module", "Instance target could not be resolved.", severity="error"),
            ),
        )

    unsupported = _unsupported_diagnostics(module)
    if unsupported:
        return WrapperInfo(
            wrapper_class="unknown_or_unsupported",
            confidence="blocked",
            diagnostics=tuple(unsupported),
        )

    if _has_behavior(module):
        return WrapperInfo(
            wrapper_class="behavioral_wrapper",
            confidence="unsafe",
            diagnostics=(
                RefactorDiagnostic(
                    "behavioral-wrapper",
                    "Module contains behavioral code and is not safe to collapse in the initial implementation.",
                    severity="warning",
                ),
            ),
            refactor_actions=("visualize",),
        )

    if not module.instances:
        return WrapperInfo(wrapper_class="not_wrapper", confidence="none")

    unresolved_children = [inst.module_name for inst in module.instances if inst.resolved_module is None]
    if unresolved_children:
        names = ", ".join(sorted(set(unresolved_children)))
        return WrapperInfo(
            wrapper_class="unknown_or_unsupported",
            confidence="blocked",
            diagnostics=(
                RefactorDiagnostic(
                    "unresolved-child",
                    f"Wrapper contains unresolved child instance module(s): {names}.",
                    severity="error",
                ),
            ),
        )

    if len(module.instances) == 1 and _is_pure_pass_through(module):
        return WrapperInfo(
            wrapper_class="pure_pass_through",
            confidence="safe",
            refactor_actions=("previewCollapse", "visualize"),
        )

    return WrapperInfo(
        wrapper_class="structural_wrapper",
        confidence="preview",
        diagnostics=(
            RefactorDiagnostic(
                "structural-wrapper",
                "Module is structural but not a single pure pass-through wrapper.",
                severity="info",
            ),
        ),
        refactor_actions=("visualize",),
    )


def _select_roots(design: Design, top: str | None) -> list[Module]:
    if top is not None:
        module = design.get_module(top)
        if module is None:
            msg = f"Top module not found in design: {top}"
            raise ValueError(msg)
        return [module]

    roots = design.get_top_modules()
    return roots if roots else list(design.modules)


def _module_node(
    module: Module,
    *,
    instance_path: str,
    stack: tuple[str, ...],
    max_depth: int | None,
) -> HierarchyNode:
    at_depth_limit = max_depth is not None and max_depth <= 0
    children = (
        []
        if at_depth_limit
        else [
            _instance_node(
                inst,
                parent_path=instance_path,
                stack=(*stack, module.name),
                max_depth=None if max_depth is None else max_depth - 1,
            )
            for inst in module.instances
            if inst.module_name not in stack
        ]
    )
    return HierarchyNode(
        name=module.name,
        module_name=module.name,
        instance_path=instance_path,
        kind="module",
        file=_loc_file_uri(module.loc),
        range=_loc_range(module.loc),
        children=children,
        has_more_children=at_depth_limit and bool(module.instances),
    )


def _instance_node(
    inst: Instance,
    *,
    parent_path: str,
    stack: tuple[str, ...],
    max_depth: int | None,
) -> HierarchyNode:
    instance_path = f"{parent_path}/{inst.instance_name}"
    resolved = inst.resolved_module
    at_depth_limit = max_depth is not None and max_depth <= 0
    cycle = resolved is not None and resolved.name in stack
    children = (
        []
        if at_depth_limit or cycle or resolved is None
        else [
            _instance_node(
                child,
                parent_path=instance_path,
                stack=(*stack, resolved.name),
                max_depth=None if max_depth is None else max_depth - 1,
            )
            for child in resolved.instances
        ]
    )

    wrapper_info = classify_wrapper_module(resolved)
    if cycle:
        wrapper_info = WrapperInfo(
            wrapper_class="unknown_or_unsupported",
            confidence="blocked",
            diagnostics=(
                RefactorDiagnostic(
                    "recursive-hierarchy",
                    f"Recursive hierarchy encountered at module {resolved.name}.",  # type: ignore[union-attr]
                    severity="error",
                ),
            ),
        )

    return HierarchyNode(
        name=inst.instance_name,
        module_name=inst.module_name,
        instance_path=instance_path,
        kind="instance",
        file=_loc_file_uri(resolved.loc if resolved is not None else None),
        range=_loc_range(resolved.loc if resolved is not None else None),
        instance_file=_loc_file_uri(inst.loc),
        instance_range=_loc_range(inst.loc),
        wrapper_info=wrapper_info,
        children=children,
        has_more_children=at_depth_limit and bool(getattr(resolved, "instances", [])),
    )


def _wrapper_summary(node: HierarchyNode) -> dict[str, object]:
    payload = node.to_dict()
    payload.pop("children", None)
    payload.pop("hasMoreChildren", None)
    return payload


def _unsupported_diagnostics(module: Module) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    if module.generate_blocks:
        diagnostics.append(
            RefactorDiagnostic(
                "generate-blocks",
                "Generate blocks are not supported by initial wrapper classification.",
                severity="warning",
            )
        )
    if module.specify_blocks:
        diagnostics.append(
            RefactorDiagnostic(
                "specify-blocks",
                "Specify blocks are not supported by hierarchy collapse.",
                severity="warning",
            )
        )
    if module.interface_instances:
        diagnostics.append(
            RefactorDiagnostic(
                "interface-instances",
                "Interface instances are not supported by initial wrapper classification.",
                severity="warning",
            )
        )
    return diagnostics


def _has_behavior(module: Module) -> bool:
    return bool(module.always_blocks or module.initial_blocks or module.functions or module.tasks)


def _is_pure_pass_through(module: Module) -> bool:  # noqa: PLR0911
    child = module.instances[0]
    aliases = _alias_groups(module.continuous_assigns)
    if aliases is None:
        return False

    wrapper_ports = {port.name for port in module.ports}
    if not wrapper_ports:
        return False

    for assign in module.continuous_assigns:
        lhs = _simple_identifier_name(assign.lhs)
        rhs = _simple_identifier_name(assign.rhs)
        if lhs is None or rhs is None:
            return False
        if not aliases.group_contains_any(lhs, wrapper_ports):
            return False

    if not child.port_connections:
        return False

    for conn in child.port_connections:
        signal = _simple_identifier_name(conn.expression)
        if signal is None or not aliases.group_contains_any(signal, wrapper_ports):
            return False

    return True


def _alias_groups(assigns: list[ContinuousAssign]) -> _UnionFind | None:
    aliases = _UnionFind()
    for assign in assigns:
        lhs = _simple_identifier_name(assign.lhs)
        rhs = _simple_identifier_name(assign.rhs)
        if lhs is None or rhs is None:
            return None
        aliases.union(lhs, rhs)
    return aliases


def _loc_file_uri(loc: SourceLocation | None) -> str:
    if loc is None or not loc.file:
        return ""
    return "file:///" + pathname2url(os.path.abspath(loc.file)).lstrip("/")
