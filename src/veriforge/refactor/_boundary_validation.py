"""Request validation for hierarchy boundary moves."""

from __future__ import annotations

from .diagnostics import RefactorDiagnostic
from ._boundary_models import (
    BoundaryMoveRequest,
    _DIRECTION_KIND_MATRIX,
    _DIRECTIONS,
    _SELECTION_KINDS,
    _SIGNAL_ONLY_KINDS,
)


def _extract_request_diagnostics(request: BoundaryMoveRequest) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    if not request.extracted_module_name:
        diagnostics.append(
            RefactorDiagnostic(
                "extracted-module-name-required",
                "Extract boundary moves require an extracted module name.",
                severity="error",
            )
        )
    if request.selection.kind == "range":
        if not request.selection.file:
            diagnostics.append(
                RefactorDiagnostic(
                    "extract-range-file-required",
                    "Extract range selections require a source file.",
                    severity="error",
                )
            )
        if request.selection.start_line <= 0 or request.selection.end_line <= 0:
            diagnostics.append(
                RefactorDiagnostic(
                    "extract-range-lines-required",
                    "Extract range selections require positive start and end lines.",
                    severity="error",
                )
            )
        elif request.selection.end_line < request.selection.start_line:
            diagnostics.append(
                RefactorDiagnostic(
                    "extract-range-lines-invalid",
                    "Extract range end line must be >= start line.",
                    severity="error",
                )
            )
    elif request.selection.kind == "signal":
        if not request.selection.signal:
            diagnostics.append(
                RefactorDiagnostic(
                    "extract-signal-name-required",
                    "Extract signal selections require a signal name.",
                    severity="error",
                )
            )
        if not request.selection.signal_module:
            diagnostics.append(
                RefactorDiagnostic(
                    "extract-signal-module-required",
                    "Extract signal selections require a signal module name.",
                    severity="error",
                )
            )
    return diagnostics


def _range_request_diagnostics(request: BoundaryMoveRequest) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    if not request.selection.file:
        diagnostics.append(
            RefactorDiagnostic(
                "range-file-required",
                "Range boundary selections require a source file.",
                severity="error",
            )
        )
    if request.selection.start_line <= 0 or request.selection.end_line <= 0:
        diagnostics.append(
            RefactorDiagnostic(
                "range-lines-required",
                "Range boundary selections require positive start and end lines.",
                severity="error",
            )
        )
    elif request.selection.end_line < request.selection.start_line:
        diagnostics.append(
            RefactorDiagnostic(
                "range-lines-invalid",
                "Range boundary selection end line must be >= start line.",
                severity="error",
            )
        )
    return diagnostics


def _request_diagnostics(request: BoundaryMoveRequest) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    if request.direction not in _DIRECTIONS:
        diagnostics.append(
            RefactorDiagnostic(
                "unsupported-boundary-move-direction",
                f"Unsupported hierarchy boundary move direction: {request.direction}.",
                severity="error",
            )
        )
    if request.selection.kind not in _SELECTION_KINDS:
        diagnostics.append(
            RefactorDiagnostic(
                "unsupported-boundary-selection-kind",
                f"Unsupported hierarchy boundary selection kind: {request.selection.kind}.",
                severity="error",
            )
        )
    if (
        request.direction in _DIRECTION_KIND_MATRIX
        and request.selection.kind in _SELECTION_KINDS
        and request.selection.kind not in _DIRECTION_KIND_MATRIX[request.direction]
    ):
        diagnostics.append(
            RefactorDiagnostic(
                "direction-kind-mismatch",
                (f"Selection kind '{request.selection.kind}' is not valid for direction '{request.direction}'."),
                severity="error",
            )
        )
    elif request.selection.kind == "range" and request.direction not in {"extract", "pull_up"}:
        diagnostics.append(
            RefactorDiagnostic(
                "direction-kind-mismatch",
                (f"Selection kind '{request.selection.kind}' is only valid for direction 'extract' or 'pull_up'."),
                severity="error",
            )
        )
    elif (
        request.selection.kind in _SIGNAL_ONLY_KINDS
        and request.direction in _DIRECTIONS
        and request.direction != "extract"
    ):
        diagnostics.append(
            RefactorDiagnostic(
                "direction-kind-mismatch",
                (f"Selection kind '{request.selection.kind}' is only valid for direction 'extract'."),
                severity="error",
            )
        )
    if request.direction == "push_down" and not request.new_module_name:
        diagnostics.append(
            RefactorDiagnostic(
                "new-module-name-required",
                "Push-down boundary moves require a new child module name.",
                severity="error",
            )
        )
    if request.direction == "extract":
        diagnostics.extend(_extract_request_diagnostics(request))
    elif request.selection.kind == "range":
        diagnostics.extend(_range_request_diagnostics(request))
    return diagnostics
