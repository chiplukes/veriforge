"""Dataclass models and internal structural classes for hierarchy extract."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.expressions import Expression
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port
from ..model.variables import Variable

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import TextEditPlan


@dataclass
class ExtractSelection:
    """A source range or signal-trace neighborhood used to select extractable model nodes."""

    file: str
    start_line: int
    end_line: int
    signal: str | None = None
    signal_module: str | None = None
    # Internal allowlist populated by trace-neighborhood resolution. When set, the
    # `_selected_*` helpers filter candidate nodes by Python object identity instead of
    # source range. Not serialized (intra-pipeline state).
    node_id_allowlist: frozenset[int] | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "file": self.file,
            "startLine": self.start_line,
            "endLine": self.end_line,
        }
        if self.signal is not None:
            payload["signal"] = self.signal
        if self.signal_module is not None:
            payload["signalModule"] = self.signal_module
        return payload


@dataclass(frozen=True)
class ExtractPreview:
    """Preview result for extracting selected logic into a child module."""

    module_name: str
    extracted_module_name: str
    instance_name: str
    selection: ExtractSelection
    confidence: str
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    edits: tuple[TextEditPlan, ...] = ()
    diff: str = ""
    boundary: dict[str, tuple[str, ...]] = field(default_factory=dict)
    generated_module: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(diag.severity == "error" for diag in self.diagnostics) and bool(self.edits)

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": "extractSubmodule",
            "moduleName": self.module_name,
            "extractedModuleName": self.extracted_module_name,
            "instanceName": self.instance_name,
            "selection": self.selection.to_dict(),
            "confidence": self.confidence,
            "ok": self.ok,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "boundary": {key: list(value) for key, value in self.boundary.items()},
            "edits": [edit.to_dict() for edit in self.edits],
            "diff": self.diff,
            "generatedModule": self.generated_module,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExtractApplyResult:
    """Result from applying an extract preview to source files."""

    applied: bool
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    written_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "writtenFiles": list(self.written_files),
        }


@dataclass(frozen=True)
class ExtractSelectionItem:
    """A complete semantic node covered by an extract selection."""

    kind: str
    name: str
    range: dict[str, object]
    supported: bool
    support: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "range": self.range,
            "supported": self.supported,
            "support": self.support,
        }


@dataclass(frozen=True)
class ExtractSelectionSuggestion:
    """A machine-readable hint for expanding a selection to a complete node range."""

    kind: str
    label: str
    start_line: int
    end_line: int
    range: dict[str, object]
    node_kind: str | None = None
    node_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "range": self.range,
            "nodeKind": self.node_kind,
            "nodeName": self.node_name,
        }


@dataclass(frozen=True)
class NormalizedExtractSelection:
    """A source selection normalized to complete semantic model nodes."""

    selection: ExtractSelection
    items: tuple[ExtractSelectionItem, ...] = ()
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    suggestions: tuple[ExtractSelectionSuggestion, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "selection": self.selection.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        }


@dataclass(frozen=True)
class _Boundary:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    internals: tuple[str, ...]
    complex_outputs: tuple[_ComplexOutput, ...] = ()
    forwarded_parameters: tuple[Parameter, ...] = ()
    copied_localparams: tuple[Parameter, ...] = ()
    blocked_constants: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _ComplexOutput:
    """A selected-instance output port whose connection expression is a slice/concat.

    The extracted child module exposes a synthetic output port with the same
    width as the inner module's port; the original parent expression
    (e.g., ``bus[15:8]``) is bound to that synthetic port at the new outer
    instantiation. This lets us extract instances whose outputs partially
    drive a parent net or whose outputs are wired to a concatenation.
    """

    synthetic_port_name: str
    parent_expression: Expression
    inner_port: Port
    selected_instance_index: int
    connection_index: int


@dataclass(frozen=True)
class _SelectedDeclarations:
    parameters: tuple[Parameter, ...] = ()
    nets: tuple[Net, ...] = ()
    variables: tuple[Variable, ...] = ()

    @property
    def parameter_names(self) -> set[str]:
        return {param.name for param in self.parameters}

    @property
    def moved_parameter_names(self) -> set[str]:
        return {param.name for param in self.parameters if param.is_local}

    @property
    def inherited_parameter_names(self) -> set[str]:
        return {param.name for param in self.parameters if not param.is_local}

    @property
    def signal_names(self) -> set[str]:
        return {decl.name for decl in (*self.nets, *self.variables)}  # type: ignore[attr-defined]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "parameters": [param.name for param in self.parameters],
            "nets": [net.name for net in self.nets],
            "variables": [variable.name for variable in self.variables],
        }


def _movable_selected_declarations(
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
) -> _SelectedDeclarations:
    """Return only declarations that should move into the extracted child."""

    movable_names = set(boundary.internals)
    return _SelectedDeclarations(
        parameters=selected_declarations.parameters,
        nets=tuple(net for net in selected_declarations.nets if net.name in movable_names),
        variables=tuple(variable for variable in selected_declarations.variables if variable.name in movable_names),
    )
