"""Refactoring and hierarchy analysis helpers."""

from .diagnostics import RefactorDiagnostic
from .hierarchy_boundary import (
    BoundaryEndpoint,
    BoundaryMoveApplyResult,
    BoundaryMovePreview,
    BoundaryMoveRequest,
    BoundaryMoveSelection,
    apply_hierarchy_boundary_move_preview,
    preview_hierarchy_boundary_move,
    preview_hierarchy_push_down,
    preview_hierarchy_pull_up,
)
from .hierarchy_graph import (
    HIERARCHY_WRAPPER_CLASSES,
    HierarchyGraph,
    HierarchyNode,
    WrapperInfo,
    build_hierarchy_graph,
    classify_wrapper_module,
)
from .hierarchy_collapse import (
    CollapseApplyResult,
    CollapsePreview,
    TextEditPlan,
    apply_collapse_preview,
    preview_collapse_hierarchy,
)
from .hierarchy_extract import (
    ExtractApplyResult,
    ExtractPreview,
    ExtractSelection,
    ExtractSelectionItem,
    ExtractSelectionSuggestion,
    NormalizedExtractSelection,
    apply_extract_preview,
    normalize_extract_selection,
    resolve_extract_selection,
    preview_extract_submodule,
)
from .visualization import hierarchy_graph_to_dot, hierarchy_graph_to_mermaid, hierarchy_graph_to_text

__all__ = [
    "HIERARCHY_WRAPPER_CLASSES",
    "BoundaryEndpoint",
    "BoundaryMoveApplyResult",
    "BoundaryMovePreview",
    "BoundaryMoveRequest",
    "BoundaryMoveSelection",
    "CollapseApplyResult",
    "CollapsePreview",
    "ExtractApplyResult",
    "ExtractPreview",
    "ExtractSelection",
    "ExtractSelectionItem",
    "ExtractSelectionSuggestion",
    "HierarchyGraph",
    "HierarchyNode",
    "NormalizedExtractSelection",
    "RefactorDiagnostic",
    "TextEditPlan",
    "WrapperInfo",
    "apply_collapse_preview",
    "apply_extract_preview",
    "apply_hierarchy_boundary_move_preview",
    "build_hierarchy_graph",
    "classify_wrapper_module",
    "hierarchy_graph_to_dot",
    "hierarchy_graph_to_mermaid",
    "hierarchy_graph_to_text",
    "normalize_extract_selection",
    "preview_collapse_hierarchy",
    "preview_extract_submodule",
    "preview_hierarchy_boundary_move",
    "preview_hierarchy_pull_up",
    "preview_hierarchy_push_down",
    "resolve_extract_selection",
]
