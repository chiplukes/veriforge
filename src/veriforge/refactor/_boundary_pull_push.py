"""Re-export facade for hierarchy boundary move models, engines, and utilities.

Consumers should import from this module for backwards compatibility. The
implementation is split across:
  _boundary_models.py     — dataclass models and constants
  _boundary_validation.py — request validation
  _boundary_selection.py  — selection resolution and source text utilities
  _pull_up_engine.py      — pull-up engine
  _push_down_engine.py    — push-down engine
"""

from __future__ import annotations

from .hierarchy_extract import ExtractSelection
from ._boundary_models import (
    BoundaryEndpoint,
    BoundaryMovePreview,
    BoundaryMoveRequest,
    BoundaryMoveSelection,
)
from ._boundary_validation import (
    _extract_request_diagnostics,
    _range_request_diagnostics,
    _request_diagnostics,
)
from ._boundary_selection import (
    _instance_endpoint,
    _instance_source_and_range,
    _loc_contains_selection,
    _loc_span,
    _loc_source_and_range,
    _module_endpoint,
    _node_source_and_range,
    _parent_path,
    _resolve_instance_path,
    _resolve_pull_up_range_selection,
    _resolve_selection,
    _resolve_target_parent,
    _same_path,
    _selection_overlaps_loc,
    _selection_within_loc,
    _source_line_range,
    _source_text_for_loc,
    _source_text_for_range_payload,
)
from ._pull_up_engine import (
    _build_child_module_for_pulled_up_procedural,
    _build_design_wide_pull_up_from_child_procedural,
    _build_pull_up_edit,
    _build_pull_up_edit_from_chain,
    _collect_all_module_instance_sites,
    _collect_module_instance_sites,
    _count_module_instance_sites,
    _preview_pull_up,
    _preview_pull_up_child_range,
    _preview_pull_up_range,
    _pull_up_preview_from_rewrite,
)
from ._push_down_engine import (
    _build_pass_through_instance,
    _build_push_down_edit,
    _is_valid_identifier,
    _module_body_is_empty,
    _preview_push_down,
    _unsupported_push_down_features,
)

__all__ = [
    "BoundaryEndpoint",
    "BoundaryMovePreview",
    "BoundaryMoveRequest",
    "BoundaryMoveSelection",
    "ExtractSelection",
    "_instance_source_and_range",
    "_node_source_and_range",
    "_preview_pull_up",
    "_preview_pull_up_range",
    "_preview_push_down",
    "_request_diagnostics",
    "_resolve_selection",
]
