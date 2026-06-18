"""Hierarchy graph visualization serializers."""

from __future__ import annotations

import re

from .hierarchy_graph import HierarchyGraph, HierarchyNode


def hierarchy_graph_to_text(graph: HierarchyGraph) -> str:
    """Serialize a hierarchy graph as an indented text tree."""

    lines: list[str] = []
    for root in graph.roots:
        _append_text_node(lines, root, indent=0)
    return "\n".join(lines)


def hierarchy_graph_to_dot(graph: HierarchyGraph) -> str:
    """Serialize a hierarchy graph as Graphviz DOT."""

    lines = [
        "digraph verilog_hierarchy {",
        "  rankdir=LR;",
        '  node [shape=box, fontname="Consolas"];',
    ]
    for node in graph.iter_nodes():
        attrs = {
            "label": _node_label(node),
            "style": "rounded",
        }
        color = _classification_color(node)
        if color:
            attrs["color"] = color
            attrs["penwidth"] = "2"
        lines.append(f"  {_dot_quote(node.instance_path)} [{_dot_attrs(attrs)}];")
        for child in node.children:
            lines.append(f"  {_dot_quote(node.instance_path)} -> {_dot_quote(child.instance_path)};")
    lines.append("}")
    return "\n".join(lines)


def hierarchy_graph_to_mermaid(graph: HierarchyGraph) -> str:
    """Serialize a hierarchy graph as Mermaid flowchart syntax."""

    nodes = graph.iter_nodes()
    node_ids = {node.instance_path: f"n{idx}" for idx, node in enumerate(nodes)}
    lines = ["flowchart TD"]
    for node in nodes:
        node_id = node_ids[node.instance_path]
        lines.append(f'  {node_id}["{_mermaid_escape(_node_label(node))}"]')
        css_class = _classification_class(node)
        if css_class:
            lines.append(f"  class {node_id} {css_class}")
        for child in node.children:
            lines.append(f"  {node_id} --> {node_ids[child.instance_path]}")
    lines.extend(
        [
            "  classDef pure_pass_through stroke:#2f855a,stroke-width:2px",
            "  classDef structural_wrapper stroke:#b7791f,stroke-width:2px",
            "  classDef behavioral_wrapper stroke:#c53030,stroke-width:2px",
            "  classDef unknown_or_unsupported stroke:#718096,stroke-width:2px,stroke-dasharray: 5 5",
        ]
    )
    return "\n".join(lines)


def _append_text_node(lines: list[str], node: HierarchyNode, *, indent: int) -> None:
    label = node.name if node.kind == "module" else f"{node.name} [{node.module_name}]"
    wrapper_class = _wrapper_class(node)
    if wrapper_class:
        label = f"{label} ({wrapper_class})"
    lines.append(f"{'  ' * indent}{label}")
    for child in node.children:
        _append_text_node(lines, child, indent=indent + 1)


def _node_label(node: HierarchyNode) -> str:
    if node.kind == "module":
        return node.module_name
    wrapper_class = _wrapper_class(node)
    if wrapper_class:
        return f"{node.name}\\n{node.module_name}\\n{wrapper_class}"
    return f"{node.name}\\n{node.module_name}"


def _wrapper_class(node: HierarchyNode) -> str:
    if node.wrapper_info is None or node.wrapper_info.wrapper_class == "not_wrapper":
        return ""
    return node.wrapper_info.wrapper_class


def _classification_color(node: HierarchyNode) -> str:
    return {
        "pure_pass_through": "forestgreen",
        "structural_wrapper": "darkorange",
        "behavioral_wrapper": "firebrick",
        "unknown_or_unsupported": "gray45",
    }.get(_wrapper_class(node), "")


def _classification_class(node: HierarchyNode) -> str:
    wrapper_class = _wrapper_class(node)
    if not wrapper_class:
        return ""
    return re.sub(r"[^a-zA-Z0-9_]", "_", wrapper_class)


def _dot_attrs(attrs: dict[str, str]) -> str:
    return ", ".join(f"{name}={_dot_quote(value)}" for name, value in attrs.items())


def _dot_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', r"\"").replace("\n", r"\n") + '"'


def _mermaid_escape(value: str) -> str:
    return value.replace('"', "#quot;").replace("\\n", "<br/>").replace("\n", "<br/>")
