"""Hierarchy boundary move public API and routing layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model.design import Design
from .diagnostics import RefactorDiagnostic
from .hierarchy_collapse import (
    CollapsePreview,
    apply_collapse_preview,
    preview_collapse_hierarchy,
)
from ._refactor_utils import (
    _apply_text_edits,
    _group_edits_by_file,
)
from .hierarchy_extract import (
    ExtractPreview,
    apply_extract_preview,
    preview_extract_submodule,
)
from ._boundary_pull_push import (
    BoundaryEndpoint,
    BoundaryMovePreview,
    BoundaryMoveRequest,
    BoundaryMoveSelection,
    ExtractSelection,
    _instance_source_and_range,
    _node_source_and_range,
    _preview_pull_up,
    _preview_pull_up_range,
    _preview_push_down,
    _request_diagnostics,
    _resolve_selection,
)


def preview_hierarchy_boundary_move(  # noqa: PLR0911
    design: Design, request: BoundaryMoveRequest
) -> BoundaryMovePreview:
    """Validate and normalize a hierarchy boundary move request without editing source."""

    diagnostics = _request_diagnostics(request)
    if diagnostics:
        return BoundaryMovePreview(request=request, confidence="blocked", diagnostics=tuple(diagnostics))

    if request.direction == "extract":
        return _preview_extract_via_unified(design, request)

    if request.direction == "collapse":
        return _preview_collapse_via_unified(design, request)

    if request.direction == "pull_up" and request.selection.kind == "range":
        return _preview_pull_up_range(design, request)

    resolved = _resolve_selection(design, request.selection)
    if isinstance(resolved, RefactorDiagnostic):
        return BoundaryMovePreview(request=request, confidence="blocked", diagnostics=(resolved,))

    source, parent, selected_module = resolved
    if request.direction == "pull_up":
        return _preview_pull_up(design, request, source, parent, selected_module)
    return _preview_push_down(design, request, source, parent, selected_module)


def _blocked_unified(request: BoundaryMoveRequest, code: str, message: str) -> BoundaryMovePreview:
    return BoundaryMovePreview(
        request=request,
        confidence="blocked",
        diagnostics=(RefactorDiagnostic(code, message, severity="error"),),
    )


def _preview_extract_via_unified(design: Design, request: BoundaryMoveRequest) -> BoundaryMovePreview:
    """Route an extract-direction unified request through the extract engine."""

    sel = request.selection
    if sel.kind == "range":
        module_name = sel.module_name
        if not module_name:
            return _blocked_unified(
                request,
                "extract-module-name-required",
                "Range extract selections require selection.module_name to identify the parent module.",
            )
        extract_selection = ExtractSelection(
            file=sel.file,
            start_line=sel.start_line,
            end_line=sel.end_line,
        )
    else:  # signal — validation already enforced kind in {range, signal}
        module_name = sel.module_name or sel.signal_module
        extract_selection = ExtractSelection(
            file=sel.file,
            start_line=sel.start_line,
            end_line=sel.end_line,
            signal=sel.signal,
            signal_module=sel.signal_module,
        )

    extract_preview = preview_extract_submodule(
        design,
        module_name=module_name,
        selection=extract_selection,
        extracted_module_name=request.extracted_module_name,
        instance_name=request.new_instance_name or None,
    )
    return _wrap_extract_preview(request, extract_preview)


def _wrap_extract_preview(request: BoundaryMoveRequest, extract_preview: object) -> BoundaryMovePreview:
    """Wrap an ExtractPreview into the unified BoundaryMovePreview shape."""

    metadata = dict(getattr(extract_preview, "metadata", {}) or {})
    metadata.setdefault("origin", "extract")
    metadata["extractedModuleName"] = extract_preview.extracted_module_name
    metadata["instanceName"] = extract_preview.instance_name
    metadata["moduleName"] = extract_preview.module_name

    extract_sel = extract_preview.selection
    source = BoundaryEndpoint(
        module_name=extract_preview.module_name,
        file=extract_sel.file,
        range={"startLine": extract_sel.start_line, "endLine": extract_sel.end_line},
    )
    parent = BoundaryEndpoint(module_name=extract_preview.module_name)
    target = BoundaryEndpoint(
        module_name=extract_preview.extracted_module_name,
        instance_name=extract_preview.instance_name,
    )

    return BoundaryMovePreview(
        request=request,
        confidence=extract_preview.confidence,
        diagnostics=tuple(extract_preview.diagnostics),
        source=source,
        parent=parent,
        target=target,
        edits=tuple(extract_preview.edits),
        metadata=metadata,
        engine_kind="extract",
        engine_preview=extract_preview,
    )


def _preview_collapse_via_unified(design: Design, request: BoundaryMoveRequest) -> BoundaryMovePreview:
    """Route a collapse-direction unified request through the collapse engine."""

    instance_path = request.selection.instance_path
    if not instance_path:
        return _blocked_unified(
            request,
            "collapse-instance-path-required",
            "Collapse boundary moves require selection.instance_path.",
        )
    collapse_preview = preview_collapse_hierarchy(design, instance_path)
    return _wrap_collapse_preview(request, collapse_preview)


def _wrap_collapse_preview(request: BoundaryMoveRequest, collapse_preview: CollapsePreview) -> BoundaryMovePreview:
    """Wrap a CollapsePreview into the unified BoundaryMovePreview shape."""

    metadata = dict(collapse_preview.metadata or {})
    metadata.setdefault("origin", "collapse")
    metadata["instancePath"] = collapse_preview.instance_path

    parts = collapse_preview.instance_path.split("/") if collapse_preview.instance_path else []
    instance_name = parts[-1] if parts else ""
    parent_module = ""
    # Best-effort: parent module name isn't on CollapsePreview; leave empty unless
    # the rename map exposes it. The LSP adapter layer (Phase 9) re-derives
    # whatever extra fields the legacy payload requires.
    source = BoundaryEndpoint(
        module_name=parent_module,
        instance_path=collapse_preview.instance_path,
        instance_name=instance_name,
    )

    return BoundaryMovePreview(
        request=request,
        confidence=collapse_preview.confidence,
        diagnostics=tuple(collapse_preview.diagnostics),
        source=source,
        edits=tuple(collapse_preview.edits),
        metadata=metadata,
        engine_kind="collapse",
        engine_preview=collapse_preview,
    )


def preview_hierarchy_pull_up(design: Design, selection: BoundaryMoveSelection) -> BoundaryMovePreview:
    """Preview pulling a selected hierarchy node up into its parent scope."""

    return preview_hierarchy_boundary_move(design, BoundaryMoveRequest(direction="pull_up", selection=selection))


def preview_hierarchy_push_down(
    design: Design,
    selection: BoundaryMoveSelection,
    *,
    new_module_name: str,
    new_instance_name: str = "",
    target_parent_path: str = "",
) -> BoundaryMovePreview:
    """Preview pushing a selected hierarchy/module/file scope down into a new child."""

    return preview_hierarchy_boundary_move(
        design,
        BoundaryMoveRequest(
            direction="push_down",
            selection=selection,
            target_parent_path=target_parent_path,
            new_module_name=new_module_name,
            new_instance_name=new_instance_name,
        ),
    )


@dataclass(frozen=True)
class BoundaryMoveApplyResult:
    """Result from applying a hierarchy boundary move preview to source files."""

    applied: bool
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    written_files: tuple[str, ...] = ()
    created_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "writtenFiles": list(self.written_files),
            "createdFiles": list(self.created_files),
        }


def apply_hierarchy_boundary_move_preview(preview: BoundaryMovePreview) -> BoundaryMoveApplyResult:
    """Apply a hierarchy boundary move preview's edits, dispatching by engine_kind."""

    if preview.engine_kind == "extract":
        return _apply_extract_via_unified(preview)
    if preview.engine_kind == "collapse":
        return _apply_collapse_via_unified(preview)
    return _apply_boundary_native(preview)


def _apply_boundary_native(preview: BoundaryMovePreview) -> BoundaryMoveApplyResult:
    if not preview.apply_ready:
        return BoundaryMoveApplyResult(
            applied=False,
            diagnostics=(
                RefactorDiagnostic(
                    "preview-not-applicable",
                    "Hierarchy boundary preview is not apply-ready and cannot be written.",
                    severity="error",
                ),
                *preview.diagnostics,
            ),
        )
    written: list[str] = []
    for file_path, file_edits in _group_edits_by_file(list(preview.edits)).items():
        diagnostic = _apply_text_edits(file_path, file_edits)
        if diagnostic is not None:
            return BoundaryMoveApplyResult(applied=False, diagnostics=(diagnostic,), written_files=tuple(written))
        written.append(file_path)
    return BoundaryMoveApplyResult(applied=True, written_files=tuple(written))


def _apply_extract_via_unified(preview: BoundaryMovePreview) -> BoundaryMoveApplyResult:
    extract_preview = preview.engine_preview
    if not isinstance(extract_preview, ExtractPreview):
        return BoundaryMoveApplyResult(
            applied=False,
            diagnostics=(
                RefactorDiagnostic(
                    "engine-preview-missing",
                    "Extract-engine boundary preview is missing its underlying ExtractPreview.",
                    severity="error",
                ),
            ),
        )
    pre_existing = {edit.file for edit in extract_preview.edits if edit.file and Path(edit.file).exists()}
    all_files = [edit.file for edit in extract_preview.edits if edit.file]
    result = apply_extract_preview(extract_preview)
    if not result.applied:
        return BoundaryMoveApplyResult(
            applied=False,
            diagnostics=result.diagnostics,
            written_files=tuple(result.written_files),
        )
    created = tuple(dict.fromkeys(f for f in all_files if f not in pre_existing))
    return BoundaryMoveApplyResult(
        applied=True,
        diagnostics=result.diagnostics,
        written_files=tuple(result.written_files),
        created_files=created,
    )


def _apply_collapse_via_unified(preview: BoundaryMovePreview) -> BoundaryMoveApplyResult:
    collapse_preview = preview.engine_preview
    if not isinstance(collapse_preview, CollapsePreview):
        return BoundaryMoveApplyResult(
            applied=False,
            diagnostics=(
                RefactorDiagnostic(
                    "engine-preview-missing",
                    "Collapse-engine boundary preview is missing its underlying CollapsePreview.",
                    severity="error",
                ),
            ),
        )
    result = apply_collapse_preview(collapse_preview)
    return BoundaryMoveApplyResult(
        applied=result.applied,
        diagnostics=result.diagnostics,
        written_files=tuple(result.written_files),
    )
