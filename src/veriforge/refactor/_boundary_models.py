"""Dataclass models and constants for hierarchy boundary moves."""

from __future__ import annotations

from dataclasses import dataclass, field

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import TextEditPlan

_DIRECTIONS = frozenset({"pull_up", "push_down", "collapse", "extract"})
_SELECTION_KINDS = frozenset({"instance", "subtree", "module", "file", "range", "signal"})
_MIN_INSTANCE_PATH_PARTS = 2

_DIRECTION_KIND_MATRIX: dict[str, frozenset[str]] = {
    "collapse": frozenset({"instance"}),
    "extract": frozenset({"range", "signal"}),
}

_SIGNAL_ONLY_KINDS = frozenset({"signal"})


@dataclass(frozen=True)
class BoundaryMoveSelection:
    """A hierarchy/file/module selection used as the source of a boundary move."""

    kind: str
    instance_path: str = ""
    module_name: str = ""
    file: str = ""
    start_line: int = 0
    end_line: int = 0
    signal: str = ""
    signal_module: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind}
        if self.instance_path:
            payload["instancePath"] = self.instance_path
        if self.module_name:
            payload["moduleName"] = self.module_name
        if self.file:
            payload["file"] = self.file
        if self.start_line:
            payload["startLine"] = self.start_line
        if self.end_line:
            payload["endLine"] = self.end_line
        if self.signal:
            payload["signal"] = self.signal
        if self.signal_module:
            payload["signalModule"] = self.signal_module
        return payload


@dataclass(frozen=True)
class BoundaryMoveRequest:
    """A normalized request to move logic across a hierarchy boundary."""

    direction: str
    selection: BoundaryMoveSelection
    target_parent_path: str = ""
    new_module_name: str = ""
    new_instance_name: str = ""
    extracted_module_name: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "direction": self.direction,
            "selection": self.selection.to_dict(),
        }
        if self.target_parent_path:
            payload["targetParentPath"] = self.target_parent_path
        if self.new_module_name:
            payload["newModuleName"] = self.new_module_name
        if self.new_instance_name:
            payload["newInstanceName"] = self.new_instance_name
        if self.extracted_module_name:
            payload["extractedModuleName"] = self.extracted_module_name
        return payload


@dataclass(frozen=True)
class BoundaryEndpoint:
    """A resolved hierarchy boundary endpoint."""

    module_name: str
    instance_path: str = ""
    instance_name: str = ""
    file: str = ""
    range: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"moduleName": self.module_name}
        if self.instance_path:
            payload["instancePath"] = self.instance_path
        if self.instance_name:
            payload["instanceName"] = self.instance_name
        if self.file:
            payload["file"] = self.file
        if self.range:
            payload["range"] = self.range
        return payload


_VALID_ENGINE_KINDS = frozenset({"boundary", "extract", "collapse"})


@dataclass(frozen=True)
class BoundaryMovePreview:
    """Preview contract for pull-up/push-down hierarchy boundary moves.

    This API intentionally does not produce edits yet. Later preview
    implementations can reuse the request, endpoint, and summary payload shape
    while replacing ``apply_ready=False`` with concrete guarded edits.

    ``engine_kind`` and ``engine_preview`` form a tagged union identifying
    which underlying engine produced this preview. ``engine_kind="boundary"``
    means the unified core's native pull-up/push-down engine (and
    ``engine_preview`` is implicitly ``self``). ``"extract"`` and ``"collapse"``
    indicate the preview was wrapped from ``ExtractPreview`` /
    ``CollapsePreview`` respectively, and ``engine_preview`` carries that
    underlying preview object. The underlying object is intentionally excluded
    from ``to_dict()`` — adapter helpers in the LSP layer are responsible for
    serializing engine-specific payloads.
    """

    request: BoundaryMoveRequest
    confidence: str
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    source: BoundaryEndpoint | None = None
    parent: BoundaryEndpoint | None = None
    target: BoundaryEndpoint | None = None
    before_hierarchy: dict[str, object] = field(default_factory=dict)
    after_hierarchy: dict[str, object] = field(default_factory=dict)
    moved_items: dict[str, object] = field(default_factory=dict)
    edits: tuple[TextEditPlan, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    engine_kind: str = "boundary"
    engine_preview: object | None = None

    def __post_init__(self) -> None:
        if self.engine_kind not in _VALID_ENGINE_KINDS:
            raise ValueError(
                f"Invalid engine_kind {self.engine_kind!r}; expected one of {sorted(_VALID_ENGINE_KINDS)}."
            )

    @property
    def ok(self) -> bool:
        return not any(diag.severity == "error" for diag in self.diagnostics)

    @property
    def apply_ready(self) -> bool:
        return bool(self.edits) and self.ok

    def as_extract_preview(self) -> object:
        """Return the wrapped ExtractPreview when engine_kind == 'extract'."""

        if self.engine_kind != "extract" or self.engine_preview is None:
            raise ValueError(f"BoundaryMovePreview.as_extract_preview called on engine_kind={self.engine_kind!r}.")
        return self.engine_preview

    def as_collapse_preview(self) -> object:
        """Return the wrapped CollapsePreview when engine_kind == 'collapse'."""

        if self.engine_kind != "collapse" or self.engine_preview is None:
            raise ValueError(f"BoundaryMovePreview.as_collapse_preview called on engine_kind={self.engine_kind!r}.")
        return self.engine_preview

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "operation": "moveHierarchyBoundary",
            "direction": self.request.direction,
            "selection": self.request.selection.to_dict(),
            "confidence": self.confidence,
            "ok": self.ok,
            "applyReady": self.apply_ready,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "source": self.source.to_dict() if self.source is not None else {},
            "parent": self.parent.to_dict() if self.parent is not None else {},
            "target": self.target.to_dict() if self.target is not None else {},
            "beforeHierarchy": dict(self.before_hierarchy),
            "afterHierarchy": dict(self.after_hierarchy),
            "movedItems": dict(self.moved_items),
            "edits": [edit.to_dict() for edit in self.edits],
            "metadata": dict(self.metadata),
            "engineKind": self.engine_kind,
        }
        if self.request.target_parent_path:
            payload["targetParentPath"] = self.request.target_parent_path
        return payload
