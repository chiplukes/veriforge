"""Wide-signal emitter helpers and recursive scratch emitter for CythonCodegen (mixin).

Contains all _emit_wide_* line-builder helpers, the class-level primitive
dispatch tables (_WIDE_BINARY_PRIMS etc.), _literal_wide_words,
_emit_wide_expr_to_scratch, _rhs_needs_wide_eval, and _emit_wide_lhs_write_new.
CythonCodegen inherits from _WideEmitterMixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    TernaryOp,
    UnaryOp,
)
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _cy_u64_hex,
    _const_int,
    _NATURAL_WIDTH_OPS,
    _REDUCTION_OPS,
)
from veriforge.sim.compiled._expr_emitter import _is_fixed_self_determined
from veriforge.sim.value import Value


class _WideEmitterMixin:
    """Mixin providing wide-value emitter helpers for CythonCodegen."""

    __slots__ = ()

    def _emit_wide_mem_copy_lines(
        self,
        mid: int,
        idx_expr: str,
        rhs_mid: int,
        rhs_idx_expr: str,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
        track_change: bool,
    ) -> list[str]:
        pad = "    " * indent
        words = self._mem_words(mid)
        if is_nba:
            lines: list[str] = []
            for word_index in range(words):
                lhs_word = f"((({idx_expr}) * {words}) + {word_index})"
                rhs_word = f"((({rhs_idx_expr}) * {words}) + {word_index})"
                lines.extend(
                    [
                        f"{pad}c.nba_mem_mid[c.nba_mem_count] = {mid}",
                        f"{pad}c.nba_mem_addr[c.nba_mem_count] = {lhs_word}",
                        f"{pad}c.nba_mem_val[c.nba_mem_count] = <long long>c.wide_mem_{rhs_mid}_val[{rhs_word}]",
                        f"{pad}c.nba_mem_mask[c.nba_mem_count] = <long long>c.wide_mem_{rhs_mid}_mask[{rhs_word}]",
                        f"{pad}c.nba_mem_count += 1",
                    ]
                )
            lines.append(f"{pad}c.nba_pending = 1")
            return lines

        if track_change:
            lines = [
                f"{pad}cdef int idx = ({idx_expr})",
                f"{pad}cdef int rhs_idx = ({rhs_idx_expr})",
                f"{pad}cdef int changed = 0",
            ]
            for word_index in range(words):
                lhs_word = f"idx * {words} + {word_index}"
                rhs_word = f"rhs_idx * {words} + {word_index}"
                lines.extend(
                    [
                        f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] != c.wide_mem_{rhs_mid}_val[{rhs_word}] or c.wide_mem_{mid}_mask[{lhs_word}] != c.wide_mem_{rhs_mid}_mask[{rhs_word}]:",
                        f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = c.wide_mem_{rhs_mid}_val[{rhs_word}]",
                        f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = c.wide_mem_{rhs_mid}_mask[{rhs_word}]",
                        f"{pad}    changed = 1",
                    ]
                )
            lines.extend(
                [
                    f"{pad}if changed:",
                    f"{pad}    c.val[{marker_sid}] ^= 1",
                    f"{pad}    c.dirty[{marker_sid}] = 1",
                ]
            )
            return lines

        lines = [f"{pad}_mchg = 0"]
        for word_index in range(words):
            lhs_word = f"((({idx_expr}) * {words}) + {word_index})"
            rhs_word = f"((({rhs_idx_expr}) * {words}) + {word_index})"
            lines.extend(
                [
                    f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] != c.wide_mem_{rhs_mid}_val[{rhs_word}]"
                    f" or c.wide_mem_{mid}_mask[{lhs_word}] != c.wide_mem_{rhs_mid}_mask[{rhs_word}]:",
                    f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = c.wide_mem_{rhs_mid}_val[{rhs_word}]",
                    f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = c.wide_mem_{rhs_mid}_mask[{rhs_word}]",
                    f"{pad}    _mchg = 1",
                ]
            )
        lines.extend(
            [
                f"{pad}if _mchg:",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_wide_mem_flat_concat_lines(
        self,
        mid: int,
        idx_expr: str,
        flat_parts: list[tuple[str, int, str, str]],
        elem_w: int,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
        track_change: bool,
    ) -> list[str]:
        pad = "    " * indent
        words = self._mem_words(mid)
        if is_nba:
            lines: list[str] = []
            for word_index in range(words):
                base = word_index * _WORD_BITS
                word_v, word_m = self._masked_flat_concat_word_exprs(flat_parts, base, elem_w)
                lines.extend(
                    [
                        f"{pad}c.nba_mem_mid[c.nba_mem_count] = {mid}",
                        f"{pad}c.nba_mem_addr[c.nba_mem_count] = (({idx_expr}) * {words}) + {word_index}",
                        f"{pad}c.nba_mem_val[c.nba_mem_count] = <long long>({word_v})",
                        f"{pad}c.nba_mem_mask[c.nba_mem_count] = <long long>({word_m})",
                        f"{pad}c.nba_mem_count += 1",
                    ]
                )
            lines.append(f"{pad}c.nba_pending = 1")
            return lines

        if track_change:
            lines = [
                f"{pad}cdef int idx = ({idx_expr})",
                f"{pad}cdef int changed = 0",
            ]
            for word_index in range(words):
                base = word_index * _WORD_BITS
                word_v, word_m = self._masked_flat_concat_word_exprs(flat_parts, base, elem_w)
                lhs_word = f"idx * {words} + {word_index}"
                lines.extend(
                    [
                        f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] != {word_v} or c.wide_mem_{mid}_mask[{lhs_word}] != {word_m}:",
                        f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = {word_v}",
                        f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = {word_m}",
                        f"{pad}    changed = 1",
                    ]
                )
            lines.extend(
                [
                    f"{pad}if changed:",
                    f"{pad}    c.val[{marker_sid}] ^= 1",
                    f"{pad}    c.dirty[{marker_sid}] = 1",
                ]
            )
            return lines

        lines = [f"{pad}_mchg = 0"]
        for word_index in range(words):
            base = word_index * _WORD_BITS
            word_v, word_m = self._masked_flat_concat_word_exprs(flat_parts, base, elem_w)
            lhs_word = f"((({idx_expr}) * {words}) + {word_index})"
            lines.extend(
                [
                    f"{pad}_mwvu = {word_v}",
                    f"{pad}_mwmu = {word_m}",
                    f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] != _mwvu or c.wide_mem_{mid}_mask[{lhs_word}] != _mwmu:",
                    f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = _mwvu",
                    f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = _mwmu",
                    f"{pad}    _mchg = 1",
                ]
            )
        lines.extend(
            [
                f"{pad}if _mchg:",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_wide_mem_zero_lines(
        self, mid: int, idx_expr: str, *, marker_sid: int, indent: int, is_nba: bool, track_change: bool
    ) -> list[str]:
        pad = "    " * indent
        words = self._mem_words(mid)
        if is_nba:
            lines: list[str] = []
            for word_index in range(words):
                lines.extend(
                    [
                        f"{pad}c.nba_mem_mid[c.nba_mem_count] = {mid}",
                        f"{pad}c.nba_mem_addr[c.nba_mem_count] = (({idx_expr}) * {words}) + {word_index}",
                        f"{pad}c.nba_mem_val[c.nba_mem_count] = 0",
                        f"{pad}c.nba_mem_mask[c.nba_mem_count] = 0",
                        f"{pad}c.nba_mem_count += 1",
                    ]
                )
            lines.append(f"{pad}c.nba_pending = 1")
            return lines

        if track_change:
            lines = [
                f"{pad}cdef int idx = ({idx_expr})",
                f"{pad}cdef int changed = 0",
            ]
            for word_index in range(words):
                lhs_word = f"idx * {words} + {word_index}"
                lines.extend(
                    [
                        f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] or c.wide_mem_{mid}_mask[{lhs_word}]:",
                        f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = 0",
                        f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = 0",
                        f"{pad}    changed = 1",
                    ]
                )
            lines.extend(
                [
                    f"{pad}if changed:",
                    f"{pad}    c.val[{marker_sid}] ^= 1",
                    f"{pad}    c.dirty[{marker_sid}] = 1",
                ]
            )
            return lines

        lines = [f"{pad}_mchg = 0"]
        for word_index in range(words):
            lhs_word = f"((({idx_expr}) * {words}) + {word_index})"
            lines.extend(
                [
                    f"{pad}if c.wide_mem_{mid}_val[{lhs_word}] or c.wide_mem_{mid}_mask[{lhs_word}]:",
                    f"{pad}    c.wide_mem_{mid}_val[{lhs_word}] = 0",
                    f"{pad}    c.wide_mem_{mid}_mask[{lhs_word}] = 0",
                    f"{pad}    _mchg = 1",
                ]
            )
        lines.extend(
            [
                f"{pad}if _mchg:",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_scalar_mem_write_lines(
        self,
        mid: int,
        idx_expr: str,
        rhs_val_expr: str,
        rhs_mask_expr: str,
        elem_w: int,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
        track_change: bool,
    ) -> list[str]:
        pad = "    " * indent
        if is_nba:
            return [
                f"{pad}c.nba_mem_mid[c.nba_mem_count] = {mid}",
                f"{pad}c.nba_mem_addr[c.nba_mem_count] = ({idx_expr})",
                f"{pad}c.nba_mem_val[c.nba_mem_count] = ({rhs_val_expr}) & wmask({elem_w})",
                f"{pad}c.nba_mem_mask[c.nba_mem_count] = ({rhs_mask_expr}) & wmask({elem_w})",
                f"{pad}c.nba_mem_count += 1",
                f"{pad}c.nba_pending = 1",
            ]

        if track_change:
            return [
                f"{pad}cdef long long idx = ({idx_expr})",
                f"{pad}cdef long long rval = ({rhs_val_expr}) & wmask({elem_w})",
                f"{pad}cdef long long rmask = ({rhs_mask_expr}) & wmask({elem_w})",
                f"{pad}rval = rval & ~rmask",
                f"{pad}if c.mem_{mid}_val[idx] != rval or c.mem_{mid}_mask[idx] != rmask:",
                f"{pad}    c.mem_{mid}_val[idx] = rval",
                f"{pad}    c.mem_{mid}_mask[idx] = rmask",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]

        return [
            f"{pad}_mwi = ({idx_expr})",
            f"{pad}_mwv = (({rhs_val_expr}) & ~({rhs_mask_expr})) & wmask({elem_w})",
            f"{pad}_mwm = ({rhs_mask_expr}) & wmask({elem_w})",
            f"{pad}if c.mem_{mid}_val[_mwi] != _mwv or c.mem_{mid}_mask[_mwi] != _mwm:",
            f"{pad}    c.mem_{mid}_val[_mwi] = _mwv",
            f"{pad}    c.mem_{mid}_mask[_mwi] = _mwm",
            f"{pad}    c.val[{marker_sid}] ^= 1",
            f"{pad}    c.dirty[{marker_sid}] = 1",
        ]

    def _emit_mem_bit_write_lines(
        self,
        mid: int,
        idx_expr: str,
        bit_expr: str,
        rhs_val_expr: str,
        rhs_mask_expr: str,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
        track_change: bool,
    ) -> list[str]:
        pad = "    " * indent
        elem_w, _depth = self._mem_info[mid]
        if elem_w > _WORD_BITS:
            words = self._mem_words(mid)
            lines = [
                f"{pad}cdef long long idx = ({idx_expr})",
                f"{pad}cdef long long bit = ({bit_expr})",
                f"{pad}cdef long long word = bit >> 6",
                f"{pad}cdef long long word_lsb = bit & 0x3f",
                f"{pad}cdef long long word_addr = (idx * {words}) + word",
                f"{pad}cdef unsigned long long bit_mask = (<unsigned long long>1) << word_lsb",
                f"{pad}cdef unsigned long long rval = (<unsigned long long>({rhs_val_expr})) & 1",
                f"{pad}cdef unsigned long long rmask = (<unsigned long long>({rhs_mask_expr})) & 1",
            ]
            if is_nba:
                lines.extend(
                    [
                        f"{pad}c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                        f"{pad}c.nba_mem_range_addr[c.nba_mem_range_count] = word_addr",
                        f"{pad}c.nba_mem_range_msb[c.nba_mem_range_count] = word_lsb",
                        f"{pad}c.nba_mem_range_lsb[c.nba_mem_range_count] = word_lsb",
                        f"{pad}c.nba_mem_range_val[c.nba_mem_range_count] = <long long>rval",
                        f"{pad}c.nba_mem_range_mask[c.nba_mem_range_count] = <long long>rmask",
                        f"{pad}c.nba_mem_range_count += 1",
                        f"{pad}c.nba_pending = 1",
                    ]
                )
                return lines

            lines.extend(
                [
                    f"{pad}cdef unsigned long long new_v = ((c.wide_mem_{mid}_val[word_addr] & ~bit_mask)"
                    f" | (((rval & ~rmask) & 1) << word_lsb))",
                    f"{pad}cdef unsigned long long new_m = ((c.wide_mem_{mid}_mask[word_addr] & ~bit_mask)"
                    f" | ((rmask & 1) << word_lsb))",
                ]
            )
            # Always change-aware: an unconditional marker toggle would make
            # combo processes that rewrite identical data spin forever in the
            # delta loop (the marker value flips every write).
            lines.extend(
                [
                    f"{pad}if new_v != c.wide_mem_{mid}_val[word_addr] or new_m != c.wide_mem_{mid}_mask[word_addr]:",
                    f"{pad}    c.wide_mem_{mid}_val[word_addr] = new_v",
                    f"{pad}    c.wide_mem_{mid}_mask[word_addr] = new_m",
                    f"{pad}    c.val[{marker_sid}] ^= 1",
                    f"{pad}    c.dirty[{marker_sid}] = 1",
                ]
            )
            return lines

        if is_nba:
            return [
                f"{pad}c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                f"{pad}c.nba_mem_range_addr[c.nba_mem_range_count] = ({idx_expr})",
                f"{pad}c.nba_mem_range_msb[c.nba_mem_range_count] = ({bit_expr})",
                f"{pad}c.nba_mem_range_lsb[c.nba_mem_range_count] = ({bit_expr})",
                f"{pad}c.nba_mem_range_val[c.nba_mem_range_count] = ({rhs_val_expr}) & 1",
                f"{pad}c.nba_mem_range_mask[c.nba_mem_range_count] = ({rhs_mask_expr}) & 1",
                f"{pad}c.nba_mem_range_count += 1",
                f"{pad}c.nba_pending = 1",
            ]

        lines = [
            f"{pad}cdef long long idx = ({idx_expr})",
            f"{pad}cdef long long bit = ({bit_expr}) & 0x3f",
            f"{pad}cdef long long rval = ({rhs_val_expr}) & 1",
            f"{pad}cdef long long m = ({rhs_mask_expr}) & 1",
            f"{pad}cdef long long old_v = c.mem_{mid}_val[idx]",
            f"{pad}cdef long long old_m = c.mem_{mid}_mask[idx]",
            f"{pad}cdef long long new_v = (old_v & ~(1LL << bit)) | ((rval & ~m) << bit)",
            f"{pad}cdef long long new_m = (old_m & ~(1LL << bit)) | (m << bit)",
        ]
        # Always change-aware (see comment in the wide branch above).
        lines.extend(
            [
                f"{pad}if new_v != old_v or new_m != old_m:",
                f"{pad}    c.mem_{mid}_val[idx] = new_v",
                f"{pad}    c.mem_{mid}_mask[idx] = new_m",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_wide_mem_dynamic_range_lines(
        self,
        mid: int,
        idx_expr: str,
        msb_expr: str,
        lsb_expr: str,
        rhs_val_expr: str,
        rhs_mask_expr: str,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
    ) -> list[str]:
        pad = "    " * indent
        words = self._mem_words(mid)
        lines = [
            f"{pad}cdef long long idx = ({idx_expr})",
            f"{pad}cdef int range_msb = ({msb_expr})",
            f"{pad}cdef int range_lsb = ({lsb_expr})",
            f"{pad}cdef long long idx_word_base = idx * {words}",
            f"{pad}cdef int start_word = range_lsb >> 6",
            f"{pad}cdef int end_word = range_msb >> 6",
            f"{pad}cdef int word_index = start_word",
            f"{pad}cdef int chunk_lsb",
            f"{pad}cdef int chunk_msb",
            f"{pad}cdef int chunk_w",
            f"{pad}cdef int src_shift",
            f"{pad}cdef int word_lsb",
            f"{pad}cdef long long word_addr",
            f"{pad}cdef unsigned long long chunk_mask",
            f"{pad}cdef unsigned long long word_mask",
            f"{pad}cdef unsigned long long chunk_val",
            f"{pad}cdef unsigned long long chunk_rmask",
            f"{pad}_mchg = 0",
            f"{pad}while word_index <= end_word:",
            f"{pad}    chunk_lsb = range_lsb if range_lsb > (word_index << 6) else (word_index << 6)",
            f"{pad}    chunk_msb = range_msb if range_msb < (((word_index + 1) << 6) - 1) else (((word_index + 1) << 6) - 1)",
            f"{pad}    chunk_w = chunk_msb - chunk_lsb + 1",
            f"{pad}    src_shift = chunk_lsb - range_lsb",
            f"{pad}    word_lsb = chunk_lsb - (word_index << 6)",
            f"{pad}    chunk_mask = _word_mask64(chunk_w)",
            f"{pad}    word_mask = chunk_mask << word_lsb",
            f"{pad}    chunk_val = (<unsigned long long>((({rhs_val_expr}) >> src_shift))) & chunk_mask",
            f"{pad}    chunk_rmask = (<unsigned long long>((({rhs_mask_expr}) >> src_shift))) & chunk_mask",
            f"{pad}    word_addr = idx_word_base + word_index",
        ]
        if is_nba:
            lines.extend(
                [
                    f"{pad}    c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                    f"{pad}    c.nba_mem_range_addr[c.nba_mem_range_count] = word_addr",
                    f"{pad}    c.nba_mem_range_msb[c.nba_mem_range_count] = word_lsb + chunk_w - 1",
                    f"{pad}    c.nba_mem_range_lsb[c.nba_mem_range_count] = word_lsb",
                    f"{pad}    c.nba_mem_range_val[c.nba_mem_range_count] = <long long>chunk_val",
                    f"{pad}    c.nba_mem_range_mask[c.nba_mem_range_count] = <long long>chunk_rmask",
                    f"{pad}    c.nba_mem_range_count += 1",
                    f"{pad}    word_index += 1",
                    f"{pad}c.nba_pending = 1",
                ]
            )
            return lines

        lines.extend(
            [
                f"{pad}    _mwvu = ((c.wide_mem_{mid}_val[word_addr] & ~word_mask)"
                f" | (((chunk_val & ~chunk_rmask) << word_lsb) & word_mask))",
                f"{pad}    _mwmu = ((c.wide_mem_{mid}_mask[word_addr] & ~word_mask)"
                f" | (((chunk_rmask & chunk_mask) << word_lsb) & word_mask))",
                f"{pad}    if c.wide_mem_{mid}_val[word_addr] != _mwvu or c.wide_mem_{mid}_mask[word_addr] != _mwmu:",
                f"{pad}        c.wide_mem_{mid}_val[word_addr] = _mwvu",
                f"{pad}        c.wide_mem_{mid}_mask[word_addr] = _mwmu",
                f"{pad}        _mchg = 1",
                f"{pad}    word_index += 1",
                f"{pad}if _mchg:",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_wide_mem_insert_mem_slice_lines(  # noqa: PLR0913
        self,
        mid: int,
        idx_expr: str,
        dst_lsb_expr: str,
        rhs_mid: int,
        rhs_idx_expr: str,
        rhs_lsb_expr: str,
        width_expr: str,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
    ) -> list[str]:
        pad = "    " * indent
        words = self._mem_words(mid)
        lines = [
            f"{pad}cdef int _dst_addr = ({idx_expr})",
            f"{pad}cdef int _src_addr = ({rhs_idx_expr})",
            f"{pad}cdef int _dst_lsb = <int>({dst_lsb_expr})",
            f"{pad}cdef int _src_lsb = <int>({rhs_lsb_expr})",
            f"{pad}cdef int _copy_width = <int>({width_expr})",
            f"{pad}cdef int _start_word",
            f"{pad}cdef int _end_word",
            f"{pad}cdef int _word_index",
            f"{pad}cdef int _chunk_lsb",
            f"{pad}cdef int _chunk_msb",
            f"{pad}cdef int _chunk_w",
            f"{pad}cdef int _src_shift",
            f"{pad}cdef int _word_lsb",
            f"{pad}cdef int _word_addr",
            f"{pad}cdef unsigned long long _chunk_mask",
            f"{pad}cdef unsigned long long _word_mask",
            f"{pad}cdef unsigned long long _chunk_val",
            f"{pad}cdef unsigned long long _chunk_rmask",
            f"{pad}cdef unsigned long long _new_val",
            f"{pad}cdef unsigned long long _new_mask",
            f"{pad}_mchg = 0",
            f"{pad}if _copy_width > 0:",
            f"{pad}    _start_word = _dst_lsb >> 6",
            f"{pad}    _end_word = (_dst_lsb + _copy_width - 1) >> 6",
            f"{pad}    _word_index = _start_word",
            f"{pad}    while _word_index <= _end_word:",
            f"{pad}        _chunk_lsb = _dst_lsb if _dst_lsb > (_word_index << 6) else (_word_index << 6)",
            f"{pad}        _chunk_msb = _dst_lsb + _copy_width - 1",
            f"{pad}        if _chunk_msb > (((_word_index + 1) << 6) - 1):",
            f"{pad}            _chunk_msb = (((_word_index + 1) << 6) - 1)",
            f"{pad}        _chunk_w = _chunk_msb - _chunk_lsb + 1",
            f"{pad}        _src_shift = _chunk_lsb - _dst_lsb",
            f"{pad}        _word_lsb = _chunk_lsb - (_word_index << 6)",
            f"{pad}        _chunk_mask = _word_mask64(_chunk_w)",
            f"{pad}        _chunk_val = _wmem{rhs_mid}_extract_val(c, _src_addr, _src_lsb + _src_shift) & _chunk_mask",
            f"{pad}        _chunk_rmask = _wmem{rhs_mid}_extract_mask(c, _src_addr, _src_lsb + _src_shift) & _chunk_mask",
        ]
        if is_nba:
            lines.extend(
                [
                    f"{pad}        _word_addr = (_dst_addr * {words}) + _word_index",
                    f"{pad}        c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                    f"{pad}        c.nba_mem_range_addr[c.nba_mem_range_count] = _word_addr",
                    f"{pad}        c.nba_mem_range_msb[c.nba_mem_range_count] = _word_lsb + _chunk_w - 1",
                    f"{pad}        c.nba_mem_range_lsb[c.nba_mem_range_count] = _word_lsb",
                    f"{pad}        c.nba_mem_range_val[c.nba_mem_range_count] = <long long>_chunk_val",
                    f"{pad}        c.nba_mem_range_mask[c.nba_mem_range_count] = <long long>_chunk_rmask",
                    f"{pad}        c.nba_mem_range_count += 1",
                    f"{pad}        _word_index += 1",
                    f"{pad}    c.nba_pending = 1",
                ]
            )
            return lines

        lines.extend(
            [
                f"{pad}        _word_addr = (_dst_addr * {words}) + _word_index",
                f"{pad}        _word_mask = _chunk_mask << _word_lsb",
                f"{pad}        _new_val = ((c.wide_mem_{mid}_val[_word_addr] & ~_word_mask)"
                f" | (((_chunk_val & ~_chunk_rmask) & _chunk_mask) << _word_lsb))",
                f"{pad}        _new_mask = ((c.wide_mem_{mid}_mask[_word_addr] & ~_word_mask)"
                f" | ((_chunk_rmask & _chunk_mask) << _word_lsb))",
                f"{pad}        if c.wide_mem_{mid}_val[_word_addr] != _new_val or c.wide_mem_{mid}_mask[_word_addr] != _new_mask:",
                f"{pad}            c.wide_mem_{mid}_val[_word_addr] = _new_val",
                f"{pad}            c.wide_mem_{mid}_mask[_word_addr] = _new_mask",
                f"{pad}            _mchg = 1",
                f"{pad}        _word_index += 1",
                f"{pad}    if _mchg:",
                f"{pad}        c.val[{marker_sid}] ^= 1",
                f"{pad}        c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_whole_mem_copy_lines(
        self,
        lhs_mid: int,
        rhs_mid: int,
        *,
        marker_sid: int,
        indent: int,
        is_nba: bool,
    ) -> list[str]:
        pad = "    " * indent
        elem_w, depth = self._mem_info[lhs_mid]
        if lhs_mid == rhs_mid:
            return [f"{pad}pass"]
        if elem_w > _WORD_BITS:
            words = self._mem_words(lhs_mid)
            if is_nba:
                lines: list[str] = []
                for addr in range(depth):
                    for word_index in range(words):
                        word_addr = addr * words + word_index
                        lines.extend(
                            [
                                f"{pad}c.nba_mem_mid[c.nba_mem_count] = {lhs_mid}",
                                f"{pad}c.nba_mem_addr[c.nba_mem_count] = {word_addr}",
                                f"{pad}c.nba_mem_val[c.nba_mem_count] = <long long>c.wide_mem_{rhs_mid}_val[{word_addr}]",
                                f"{pad}c.nba_mem_mask[c.nba_mem_count] = <long long>c.wide_mem_{rhs_mid}_mask[{word_addr}]",
                                f"{pad}c.nba_mem_count += 1",
                            ]
                        )
                lines.append(f"{pad}c.nba_pending = 1")
                return lines

            lines = [f"{pad}cdef int changed = 0"]
            for addr in range(depth):
                for word_index in range(words):
                    word_addr = addr * words + word_index
                    lines.extend(
                        [
                            f"{pad}if c.wide_mem_{lhs_mid}_val[{word_addr}] != c.wide_mem_{rhs_mid}_val[{word_addr}] or c.wide_mem_{lhs_mid}_mask[{word_addr}] != c.wide_mem_{rhs_mid}_mask[{word_addr}]:",
                            f"{pad}    c.wide_mem_{lhs_mid}_val[{word_addr}] = c.wide_mem_{rhs_mid}_val[{word_addr}]",
                            f"{pad}    c.wide_mem_{lhs_mid}_mask[{word_addr}] = c.wide_mem_{rhs_mid}_mask[{word_addr}]",
                            f"{pad}    changed = 1",
                        ]
                    )
            lines.extend(
                [
                    f"{pad}if changed:",
                    f"{pad}    c.val[{marker_sid}] ^= 1",
                    f"{pad}    c.dirty[{marker_sid}] = 1",
                ]
            )
            return lines

        if is_nba:
            lines = []
            for addr in range(depth):
                lines.extend(
                    [
                        f"{pad}c.nba_mem_mid[c.nba_mem_count] = {lhs_mid}",
                        f"{pad}c.nba_mem_addr[c.nba_mem_count] = {addr}",
                        f"{pad}c.nba_mem_val[c.nba_mem_count] = c.mem_{rhs_mid}_val[{addr}]",
                        f"{pad}c.nba_mem_mask[c.nba_mem_count] = c.mem_{rhs_mid}_mask[{addr}]",
                        f"{pad}c.nba_mem_count += 1",
                    ]
                )
            lines.append(f"{pad}c.nba_pending = 1")
            return lines

        lines = [f"{pad}cdef int changed = 0"]
        for addr in range(depth):
            lines.extend(
                [
                    f"{pad}if c.mem_{lhs_mid}_val[{addr}] != c.mem_{rhs_mid}_val[{addr}] or c.mem_{lhs_mid}_mask[{addr}] != c.mem_{rhs_mid}_mask[{addr}]:",
                    f"{pad}    c.mem_{lhs_mid}_val[{addr}] = c.mem_{rhs_mid}_val[{addr}]",
                    f"{pad}    c.mem_{lhs_mid}_mask[{addr}] = c.mem_{rhs_mid}_mask[{addr}]",
                    f"{pad}    changed = 1",
                ]
            )
        lines.extend(
            [
                f"{pad}if changed:",
                f"{pad}    c.val[{marker_sid}] ^= 1",
                f"{pad}    c.dirty[{marker_sid}] = 1",
            ]
        )
        return lines

    def _emit_flat_concat_whole_assign(self, dst_sid: int, flat_parts: list[tuple[str, int, str, str]]) -> list[str]:
        dst_width = self._signal_widths[dst_sid]
        dst_words = (dst_width + (_WORD_BITS - 1)) // _WORD_BITS if dst_width > _WORD_BITS else 0
        lines: list[str] = []

        if dst_words > 0:
            for word_index in range(dst_words):
                base = word_index * _WORD_BITS
                wide_index = f"c.wide_offset[{dst_sid}] + {word_index}"
                masked_v, masked_m = self._masked_flat_concat_word_exprs(flat_parts, base, dst_width)
                lines.extend(
                    [
                        f"    if {masked_v} != c.wide_val[{wide_index}] or {masked_m} != c.wide_mask[{wide_index}]:",
                        f"        c.wide_val[{wide_index}] = {masked_v}",
                        f"        c.wide_mask[{wide_index}] = {masked_m}",
                        f"        c.dirty[{dst_sid}] = 1",
                    ]
                )

            low_v_expr, low_m_expr = self._masked_flat_concat_word_exprs(flat_parts, 0, dst_width)
            low_v = f"<long long>{low_v_expr}"
            low_m = f"<long long>{low_m_expr}"
            lines.extend(
                [
                    f"    if {low_v} != c.val[{dst_sid}] or {low_m} != c.mask[{dst_sid}]:",
                    f"        c.val[{dst_sid}] = {low_v}",
                    f"        c.mask[{dst_sid}] = {low_m}",
                    f"        c.dirty[{dst_sid}] = 1",
                ]
            )
            return lines

        masked_v_expr, masked_m_expr = self._masked_flat_concat_word_exprs(flat_parts, 0, dst_width)
        masked_v = f"<long long>{masked_v_expr}"
        masked_m = f"<long long>{masked_m_expr}"
        return [
            f"    if {masked_v} != c.val[{dst_sid}] or {masked_m} != c.mask[{dst_sid}]:",
            f"        c.val[{dst_sid}] = {masked_v}",
            f"        c.mask[{dst_sid}] = {masked_m}",
            f"        c.dirty[{dst_sid}] = 1",
        ]

    @staticmethod
    def _concat_shift_word_expr(
        flat_parts: list[tuple[str, int, str, str]], base: int, shift: int, op: str, *, mask: bool
    ) -> str:
        terms: list[str] = []
        offsets: list[tuple[str, int, str, str, int]] = []
        running_lsb = 0
        for kind, width, expr_a, expr_b in reversed(flat_parts):
            offsets.append((kind, width, expr_a, expr_b, running_lsb))
            running_lsb += width

        helper = "_sig_extract_word_mask" if mask else "_sig_extract_word_val"
        for kind, width, expr_a, expr_b, part_lsb in offsets:
            shifted_lsb = part_lsb + shift if op == "<<" else part_lsb - shift
            overlap_lo = max(base, shifted_lsb)
            overlap_hi = min(base + (_WORD_BITS - 1), shifted_lsb + width - 1)
            if overlap_lo > overlap_hi:
                continue
            overlap_w = overlap_hi - overlap_lo + 1
            src_offset = overlap_lo - shifted_lsb
            dst_shift = overlap_lo - base
            if kind == "sig":
                src_lsb = f"(({expr_b}) + {src_offset})" if src_offset else f"({expr_b})"
                term = f"({helper}(c, {expr_a}, {src_lsb}) & _word_mask64({overlap_w}))"
            else:
                src_expr = expr_b if mask else expr_a
                shifted = (
                    f"((<unsigned long long>({src_expr})) >> {src_offset})"
                    if src_offset
                    else f"(<unsigned long long>({src_expr}))"
                )
                term = f"({shifted} & _word_mask64({overlap_w}))"
            if dst_shift > 0:
                term = f"({term} << {dst_shift})"
            terms.append(term)

        return " | ".join(terms) if terms else "0"

    def _emit_flat_concat_shift_whole_assign(
        self,
        dst_sid: int,
        flat_parts: list[tuple[str, int, str, str]],
        op: str,
        shift: int,
        *,
        indent: str = "    ",
        is_nba: bool = False,
    ) -> list[str]:
        dst_width = self._signal_widths[dst_sid]
        dst_words = (dst_width + (_WORD_BITS - 1)) // _WORD_BITS if dst_width > _WORD_BITS else 0
        lines: list[str] = []

        if is_nba:
            if dst_words > 0:
                for word_index in range(dst_words):
                    base = word_index * _WORD_BITS
                    word_v = self._concat_shift_word_expr(flat_parts, base, shift, op, mask=False)
                    word_m = self._concat_shift_word_expr(flat_parts, base, shift, op, mask=True)
                    tail_mask = f"_word_mask64({dst_width - base})"
                    wide_index = f"c.wide_offset[{dst_sid}] + {word_index}"
                    masked_v = f"(({word_v}) & {tail_mask})"
                    masked_m = f"(({word_m}) & {tail_mask})"
                    lines.append(f"{indent}c.wide_nba_val[{wide_index}] = {masked_v}")
                    lines.append(f"{indent}c.wide_nba_mask[{wide_index}] = {masked_m}")
                low_v = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=False)}) & _word_mask64({dst_width}))"
                low_m = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=True)}) & _word_mask64({dst_width}))"
            else:
                low_v = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=False)}) & _word_mask64({dst_width}))"
                low_m = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=True)}) & _word_mask64({dst_width}))"
            lines.extend(
                [
                    f"{indent}c.nba_val[{dst_sid}] = {low_v}",
                    f"{indent}c.nba_mask[{dst_sid}] = {low_m}",
                    f"{indent}c.nba_dirty[{dst_sid}] = 1",
                    f"{indent}c.nba_pending = 1",
                ]
            )
            return lines

        if dst_words > 0:
            for word_index in range(dst_words):
                base = word_index * _WORD_BITS
                word_v = self._concat_shift_word_expr(flat_parts, base, shift, op, mask=False)
                word_m = self._concat_shift_word_expr(flat_parts, base, shift, op, mask=True)
                tail_mask = f"_word_mask64({dst_width - base})"
                wide_index = f"c.wide_offset[{dst_sid}] + {word_index}"
                masked_v = f"(({word_v}) & {tail_mask})"
                masked_m = f"(({word_m}) & {tail_mask})"
                lines.extend(
                    [
                        f"{indent}if {masked_v} != c.wide_val[{wide_index}] or {masked_m} != c.wide_mask[{wide_index}]:",
                        f"{indent}    c.wide_val[{wide_index}] = {masked_v}",
                        f"{indent}    c.wide_mask[{wide_index}] = {masked_m}",
                        f"{indent}    c.dirty[{dst_sid}] = 1",
                    ]
                )

            low_v = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=False)}) & _word_mask64({dst_width}))"
            low_m = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=True)}) & _word_mask64({dst_width}))"
            lines.extend(
                [
                    f"{indent}if {low_v} != c.val[{dst_sid}] or {low_m} != c.mask[{dst_sid}]:",
                    f"{indent}    c.val[{dst_sid}] = {low_v}",
                    f"{indent}    c.mask[{dst_sid}] = {low_m}",
                    f"{indent}    c.dirty[{dst_sid}] = 1",
                ]
            )
            return lines

        masked_v = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=False)}) & _word_mask64({dst_width}))"
        masked_m = f"<long long>(({self._concat_shift_word_expr(flat_parts, 0, shift, op, mask=True)}) & _word_mask64({dst_width}))"
        return [
            f"{indent}if {masked_v} != c.val[{dst_sid}] or {masked_m} != c.mask[{dst_sid}]:",
            f"{indent}    c.val[{dst_sid}] = {masked_v}",
            f"{indent}    c.mask[{dst_sid}] = {masked_m}",
            f"{indent}    c.dirty[{dst_sid}] = 1",
        ]

    def _emit_signed_literal_xor_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op == ">>>"
            and isinstance(rhs.left, FunctionCall)
            and rhs.left.name.lower() == "$signed"
            and len(rhs.left.arguments) == 1
            and isinstance(rhs.left.arguments[0], BinaryOp)
            and rhs.left.arguments[0].op == "^"
            and isinstance(rhs.left.arguments[0].left, BinaryOp)
            and isinstance(rhs.left.arguments[0].right, Identifier)
            and isinstance(rhs.right, Literal | Identifier)
        ):
            return None

        inner_expr = rhs.left.arguments[0].left
        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        stem: str | None = None

        if inner_expr.op == "-":
            if isinstance(inner_expr.left, Identifier) and isinstance(inner_expr.right, Literal):
                stem = "sub_const_xor"
                signal_expr = inner_expr.left
                literal_expr = inner_expr.right
            elif isinstance(inner_expr.left, Literal) and isinstance(inner_expr.right, Identifier):
                stem = "const_sub_xor"
                signal_expr = inner_expr.right
                literal_expr = inner_expr.left
        elif inner_expr.op in {"+", "&", "|", "^"}:
            left_expr = inner_expr.left
            right_expr = inner_expr.right
            if isinstance(left_expr, Identifier) and isinstance(right_expr, Literal):
                signal_expr = left_expr
                literal_expr = right_expr
            elif isinstance(left_expr, Literal) and isinstance(right_expr, Identifier):
                signal_expr = right_expr
                literal_expr = left_expr
            if signal_expr is not None and literal_expr is not None:
                stem = {
                    "+": "add_const_xor",
                    "&": "mask_xor",
                    "|": "or_const_xor",
                    "^": "xor_const_xor",
                }[inner_expr.op]

        if stem is None or signal_expr is None or literal_expr is None:
            return None

        operand_name = signal_expr.name
        if signal_expr.hierarchy:
            operand_name = ".".join(signal_expr.hierarchy) + "." + operand_name
        xor_name = rhs.left.arguments[0].right.name
        if rhs.left.arguments[0].right.hierarchy:
            xor_name = ".".join(rhs.left.arguments[0].right.hierarchy) + "." + xor_name
        operand_sid = self._signal_map.get(operand_name)
        xor_sid = self._signal_map.get(xor_name)
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            operand_sid is None
            or xor_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[operand_sid] <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        if isinstance(rhs.right, Literal):
            shift_amt = _const_int(rhs.right, self._param_env)
            if shift_amt is None or shift_amt < 0:
                return None
            return [
                f"{pad}with gil:",
                f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {operand_sid}, (<unsigned long long>{literal_word}), {literal_width}, {xor_sid}, {shift_amt})",
            ]

        if not isinstance(rhs.right, Identifier):
            return None
        shift_name = rhs.right.name
        if rhs.right.hierarchy:
            shift_name = ".".join(rhs.right.hierarchy) + "." + shift_name
        shift_sid = self._signal_map.get(shift_name)
        if shift_sid is None:
            return None
        shift_expr = f"<int>(c.val[{shift_sid}] & wmask(c.width[{shift_sid}]))"
        return [
            f"{pad}with gil:",
            f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {operand_sid}, (<unsigned long long>{literal_word}), {literal_width}, {xor_sid}, {shift_expr})",
        ]

    def _emit_signed_identifier_xor_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op == ">>>"
            and isinstance(rhs.left, FunctionCall)
            and rhs.left.name.lower() == "$signed"
            and len(rhs.left.arguments) == 1
            and isinstance(rhs.left.arguments[0], BinaryOp)
            and rhs.left.arguments[0].op == "^"
            and isinstance(rhs.left.arguments[0].left, BinaryOp)
            and rhs.left.arguments[0].left.op in {"+", "-", "&", "|", "^"}
            and isinstance(rhs.left.arguments[0].left.left, Identifier)
            and isinstance(rhs.left.arguments[0].left.right, Identifier)
            and isinstance(rhs.left.arguments[0].right, Identifier)
            and isinstance(rhs.right, Literal | Identifier)
        ):
            return None

        inner_expr = rhs.left.arguments[0].left
        lhs1_sid = self._signal_map.get(self._identifier_name(inner_expr.left))
        lhs2_sid = self._signal_map.get(self._identifier_name(inner_expr.right))
        xor_sid = self._signal_map.get(self._identifier_name(rhs.left.arguments[0].right))
        lhs_w = self._signal_widths[dst_sid]
        if (
            lhs1_sid is None
            or lhs2_sid is None
            or xor_sid is None
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[lhs1_sid] <= _WORD_BITS
                and self._signal_widths[lhs2_sid] <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
            )
        ):
            return None

        stem = {
            "+": "add_xor",
            "-": "sub_xor",
            "&": "and_xor",
            "|": "or_xor",
            "^": "xor_xor",
        }[inner_expr.op]
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        if isinstance(rhs.right, Literal):
            shift_amt = _const_int(rhs.right, self._param_env)
            if shift_amt is None or shift_amt < 0:
                return None
            return [
                f"{pad}with gil:",
                f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {lhs1_sid}, {lhs2_sid}, {xor_sid}, {shift_amt})",
            ]

        if not isinstance(rhs.right, Identifier):
            return None
        shift_sid = self._signal_map.get(self._identifier_name(rhs.right))
        if shift_sid is None:
            return None
        shift_expr = f"<int>(c.val[{shift_sid}] & wmask(c.width[{shift_sid}]))"
        return [
            f"{pad}with gil:",
            f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {lhs1_sid}, {lhs2_sid}, {xor_sid}, {shift_expr})",
        ]

    def _emit_signed_binop_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op == ">>>"
            and isinstance(rhs.left, FunctionCall)
            and rhs.left.name.lower() == "$signed"
            and len(rhs.left.arguments) == 1
            and isinstance(rhs.left.arguments[0], BinaryOp)
            and rhs.left.arguments[0].op in {"+", "-", "&", "|", "^"}
            and isinstance(rhs.left.arguments[0].left, Identifier)
            and isinstance(rhs.left.arguments[0].right, Identifier)
            and isinstance(rhs.right, Literal | Identifier)
        ):
            return None

        left_sid = self._signal_map.get(self._identifier_name(rhs.left.arguments[0].left))
        right_sid = self._signal_map.get(self._identifier_name(rhs.left.arguments[0].right))
        lhs_w = self._signal_widths[dst_sid]
        if (
            left_sid is None
            or right_sid is None
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[left_sid] <= _WORD_BITS
                and self._signal_widths[right_sid] <= _WORD_BITS
            )
        ):
            return None

        stem = {
            "+": "add",
            "-": "sub",
            "&": "and",
            "|": "or",
            "^": "xor",
        }[rhs.left.arguments[0].op]
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        if isinstance(rhs.right, Literal):
            shift_amt = _const_int(rhs.right, self._param_env)
            if shift_amt is None or shift_amt < 0:
                return None
            return [
                f"{pad}with gil:",
                f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {left_sid}, {right_sid}, {shift_amt})",
            ]

        shift_sid = self._signal_map.get(self._identifier_name(rhs.right))
        if shift_sid is None:
            return None
        shift_expr = f"<int>(_sig_word_val(c, {shift_sid}, 0) & _word_mask64(31))"
        return [
            f"{pad}with gil:",
            f"{pad}    _whole_{phase}_sar_{stem}_signal(c, {dst_sid}, {left_sid}, {right_sid}, {shift_expr})",
        ]

    def _emit_signed_signal_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op == ">>>"
            and isinstance(rhs.left, FunctionCall)
            and rhs.left.name.lower() == "$signed"
            and len(rhs.left.arguments) == 1
            and isinstance(rhs.left.arguments[0], Identifier)
            and isinstance(rhs.right, Literal | Identifier)
        ):
            return None

        src_sid = self._signal_map.get(self._identifier_name(rhs.left.arguments[0]))
        lhs_w = self._signal_widths[dst_sid]
        if src_sid is None or lhs_w <= _WORD_BITS:
            return None

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        if isinstance(rhs.right, Literal):
            shift_amt = _const_int(rhs.right, self._param_env)
            if shift_amt is None:
                return None
            return [f"{pad}_whole_{phase}_sar_signal(c, {dst_sid}, {src_sid}, {shift_amt})"]

        if not isinstance(rhs.right, Identifier):
            return None
        shift_sid = self._signal_map.get(self._identifier_name(rhs.right))
        if shift_sid is None:
            return None
        shift_expr = f"<int>(_sig_word_val(c, {shift_sid}, 0) & _word_mask64(31))"
        return [f"{pad}_whole_{phase}_sar_signal(c, {dst_sid}, {src_sid}, {shift_expr})"]

    def _emit_wide_const_signal_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and isinstance(rhs.left, Literal) and isinstance(rhs.right, Identifier)):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.right))
        literal_width = self._expr_width(rhs.left)
        literal_value = _const_int(rhs.left, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or rhs.op not in {"+", "-", "&", "|", "^"}
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        if rhs.op == "-":
            return [
                f"{pad}_whole_{phase}_const_sub_signal(c, {dst_sid}, (<unsigned long long>{literal_word}), {signal_sid})"
            ]

        stem = {
            "+": "add_const",
            "&": "and_const",
            "|": "or_const",
            "^": "xor_const",
        }[rhs.op]
        return [f"{pad}_whole_{phase}_{stem}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}))"]

    def _emit_wide_signal_const_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and isinstance(rhs.left, Identifier) and isinstance(rhs.right, Literal)):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left))
        literal_width = self._expr_width(rhs.right)
        literal_value = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or rhs.op not in {"+", "-", "&", "|", "^"}
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        stem = {
            "+": "add_const",
            "-": "sub_const",
            "&": "and_const",
            "|": "or_const",
            "^": "xor_const",
        }[rhs.op]
        return [f"{pad}_whole_{phase}_{stem}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}))"]

    def _emit_wide_signal_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op in {"<<", ">>"}):
            return None

        signal_expr: Identifier | None = None
        if isinstance(rhs.left, Identifier):
            signal_expr = rhs.left
        elif isinstance(rhs.left, UnaryOp) and rhs.left.op == "+" and isinstance(rhs.left.operand, Identifier):
            signal_expr = rhs.left.operand

        if signal_expr is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        lhs_w = self._signal_widths[dst_sid]
        if signal_sid is None or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS):
            return None

        shift_expr: str | None = None
        if isinstance(rhs.right, Literal):
            shift_amt = _const_int(rhs.right, self._param_env)
            if shift_amt is not None:
                shift_expr = str(shift_amt)
        elif isinstance(rhs.right, Identifier):
            shift_sid = self._signal_map.get(self._identifier_name(rhs.right))
            if shift_sid is not None:
                shift_expr = f"<int>(_sig_word_val(c, {shift_sid}, 0) & _word_mask64(31))"

        if shift_expr is None:
            return None

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        helper = {"<<": "shl_signal", ">>": "shr_signal"}[rhs.op]
        return [f"{pad}_whole_{phase}_{helper}(c, {dst_sid}, {signal_sid}, {shift_expr})"]

    def _emit_wide_neg_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, UnaryOp)
            and rhs.left.op == "-"
            and isinstance(rhs.left.operand, Identifier)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left.operand))
        lhs_w = self._signal_widths[dst_sid]
        shift_amt = _const_int(rhs.right, self._param_env)
        if (
            signal_sid is None
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        helper = {"<<": "neg_shl", ">>": "neg_shr"}[rhs.op]
        return [f"{pad}_whole_{phase}_{helper}(c, {dst_sid}, {signal_sid}, {shift_amt})"]

    def _emit_wide_not_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, UnaryOp)
            and rhs.left.op == "~"
            and isinstance(rhs.left.operand, Identifier)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left.operand))
        lhs_w = self._signal_widths[dst_sid]
        shift_amt = _const_int(rhs.right, self._param_env)
        if (
            signal_sid is None
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        helper = {"<<": "not_shl", ">>": "not_shr"}[rhs.op]
        return [f"{pad}_whole_{phase}_{helper}(c, {dst_sid}, {signal_sid}, {shift_amt})"]

    def _emit_wide_lnot_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, UnaryOp)
            and rhs.left.op == "!"
            and isinstance(rhs.left.operand, Identifier)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left.operand))
        lhs_w = self._signal_widths[dst_sid]
        shift_amt = _const_int(rhs.right, self._param_env)
        if (
            signal_sid is None
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [f"{pad}_whole_{phase}_lnot_shl(c, {dst_sid}, {signal_sid}, {shift_amt})"]

    def _emit_wide_reduction_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, UnaryOp)
            and rhs.left.op in _REDUCTION_OPS
            and isinstance(rhs.left.operand, Identifier)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left.operand))
        lhs_w = self._signal_widths[dst_sid]
        shift_amt = _const_int(rhs.right, self._param_env)
        if (
            signal_sid is None
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        reduce_op = rhs.left.op
        if reduce_op in {"|", "~|"}:
            stem = "reduce_or_shift"
            invert = 1 if reduce_op == "~|" else 0
        elif reduce_op in {"&", "~&"}:
            stem = "reduce_and_shift"
            invert = 1 if reduce_op == "~&" else 0
        else:
            stem = "reduce_xor_shift"
            invert = 0 if reduce_op == "^" else 1

        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [f"{pad}_whole_{phase}_{stem}(c, {dst_sid}, {signal_sid}, {shift_amt}, {invert})"]

    def _emit_wide_signal_binop_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op in {"+", "-", "&", "|", "^", "*", "/", "%"}
            and isinstance(rhs.left.left, Identifier)
            and isinstance(rhs.left.right, Identifier)
            and isinstance(rhs.right, Literal)
        ):
            return None

        left_sid = self._signal_map.get(self._identifier_name(rhs.left.left))
        right_sid = self._signal_map.get(self._identifier_name(rhs.left.right))
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            left_sid is None
            or right_sid is None
            or shift_amt is None
            or shift_amt < 0
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[left_sid] <= _WORD_BITS
                and self._signal_widths[right_sid] <= _WORD_BITS
            )
        ):
            return None

        stem = {
            "+": "add",
            "-": "sub",
            "&": "and",
            "|": "or",
            "^": "xor",
            "*": "mul",
            "/": "div",
            "%": "mod",
        }[rhs.left.op]
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        helper = f"_whole_{phase}_{stem}_signal_{direction}"
        if rhs.left.op in {"+", "-"}:
            fallback_lines = self._emit_wide_py_bits_lines(
                dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba
            )
            if fallback_lines is None:
                return None
            unknown_cond = f"_sig_has_unknown(c, {left_sid}) or _sig_has_unknown(c, {right_sid})"
            return [
                f"{pad}if {unknown_cond}:",
                *fallback_lines,
                f"{pad}else:",
                f"{pad}    {helper}(c, {dst_sid}, {left_sid}, {right_sid}, {shift_amt})",
            ]
        if rhs.left.op in {"/", "%"}:
            return [
                f"{pad}with gil:",
                f"{pad}    {helper}(c, {dst_sid}, {left_sid}, {right_sid}, {shift_amt})",
            ]
        return [f"{pad}{helper}(c, {dst_sid}, {left_sid}, {right_sid}, {shift_amt})"]

    def _emit_wide_signal_binop_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, Identifier)
            and isinstance(rhs.right, Identifier)
            and rhs.op in {"+", "-", "&", "|", "^"}
        ):
            return None

        left_sid = self._signal_map.get(self._identifier_name(rhs.left))
        right_sid = self._signal_map.get(self._identifier_name(rhs.right))
        lhs_w = self._signal_widths[dst_sid]
        if (
            left_sid is None
            or right_sid is None
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[left_sid] <= _WORD_BITS
                and self._signal_widths[right_sid] <= _WORD_BITS
            )
        ):
            return None

        stem = {
            "+": "add",
            "-": "sub",
            "&": "and",
            "|": "or",
            "^": "xor",
        }[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [f"{pad}_whole_{phase}_{stem}_signal(c, {dst_sid}, {left_sid}, {right_sid})"]

    def _emit_wide_signal_copy_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not isinstance(rhs, Identifier):
            return None

        rhs_sid = self._signal_map.get(self._identifier_name(rhs))
        lhs_w = self._signal_widths[dst_sid]
        if rhs_sid is None or (lhs_w <= _WORD_BITS and self._signal_widths[rhs_sid] <= _WORD_BITS):
            return None

        rhs_signed = rhs_sid < len(self._signal_signed) and self._signal_signed[rhs_sid]
        rhs_w = self._signal_widths[rhs_sid]

        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        if rhs_signed and lhs_w > rhs_w:
            return [f"{pad}_whole_{phase}_signal_s(c, {dst_sid}, {rhs_sid})"]
        return [f"{pad}_whole_{phase}_signal(c, {dst_sid}, {rhs_sid})"]

    def _emit_wide_replication_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if self._signal_widths[dst_sid] <= _WORD_BITS or not isinstance(rhs, Replication):
            return None

        count = _const_int(rhs.count, self._param_env)
        elem_expr = self._normalize_replication_value(rhs.value)
        elem_width = self._expr_width(elem_expr)
        if count is None or count <= 0:
            return None

        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        if elem_width <= _WORD_BITS:
            elem_val = self._emit_expr(elem_expr, elem_width)
            elem_mask = self._emit_mask_expr(elem_expr, elem_width)
            return [
                f"{pad}_whole_{phase}_repeat_word(c, {dst_sid}, <unsigned long long>(({elem_val}) & wmask({elem_width})), <unsigned long long>(({elem_mask}) & wmask({elem_width})), {elem_width}, {count})"
            ]

        elem_source = self._resolve_signal_slice_source(elem_expr)
        if elem_source is None:
            return None

        elem_sid, elem_lsb = elem_source
        return [
            f"{pad}_whole_{phase}_repeat_signal_slice(c, {dst_sid}, {elem_sid}, <int>({elem_lsb}), {elem_width}, {count})"
        ]

    def _emit_wide_const_word_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not isinstance(rhs, Literal):
            return None

        if self._signal_widths[dst_sid] <= _WORD_BITS:
            return None

        literal_low_word = self._literal_low_word(rhs)
        if literal_low_word is None:
            return None

        literal_val, literal_mask = literal_low_word
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_const_word(c, {dst_sid}, (<unsigned long long>{literal_val}), (<unsigned long long>{literal_mask}))"
        ]

    def _emit_wide_ternary_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, TernaryOp)
            and isinstance(rhs.left.condition, Identifier)
            and isinstance(rhs.left.true_expr, Identifier)
            and isinstance(rhs.left.false_expr, Identifier)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        cond_sid = self._signal_map.get(self._identifier_name(rhs.left.condition))
        true_sid = self._signal_map.get(self._identifier_name(rhs.left.true_expr))
        false_sid = self._signal_map.get(self._identifier_name(rhs.left.false_expr))
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            cond_sid is None
            or true_sid is None
            or false_sid is None
            or shift_amt is None
            or shift_amt < 0
            or self._signal_widths[cond_sid] > _WORD_BITS
            or self._signal_widths[true_sid] != self._signal_widths[false_sid]
            or lhs_w > self._signal_widths[true_sid]
            or lhs_w <= _WORD_BITS
        ):
            return None

        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_ternary_{direction}_signal(c, {dst_sid}, {cond_sid}, {true_sid}, {false_sid}, {shift_amt})"
        ]

    def _emit_wide_flat_concat_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and isinstance(rhs.left, Concatenation)
            and isinstance(rhs.right, Literal)
            and rhs.op in {"<<", ">>"}
        ):
            return None

        flat_parts = self._flatten_concat_identifier_parts(rhs.left)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if flat_parts is None or shift_amt is None or shift_amt < 0:
            return None

        total_width = sum(width for _, width, _, _ in flat_parts)
        if total_width < _WORD_BITS and lhs_w < _WORD_BITS:
            return None

        pad = "    " * indent
        return self._emit_flat_concat_shift_whole_assign(
            dst_sid, flat_parts, rhs.op, shift_amt, indent=pad, is_nba=is_nba
        )

    def _rhs_max_accessed_signal_width(self, expr: Expression) -> int:
        """Return the maximum width of any signal actually read inside expr.

        Used by _emit_wide_py_bits_lines to widen the evaluation context when
        a narrow-result expression reads from wide signals (e.g. {a[127:96], a[63:32]} >> 5).
        """
        et = type(expr)
        if et is Identifier:
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            return self._signal_widths[sid] if sid is not None else 0
        if et in {RangeSelect, PartSelect}:
            return self._rhs_max_accessed_signal_width(expr.target)  # type: ignore[union-attr]
        if et is BinaryOp:
            return max(
                self._rhs_max_accessed_signal_width(expr.left),  # type: ignore[union-attr]
                self._rhs_max_accessed_signal_width(expr.right),  # type: ignore[union-attr]
            )
        if et is UnaryOp:
            return self._rhs_max_accessed_signal_width(expr.operand)  # type: ignore[union-attr]
        if et is Concatenation:
            return max(
                (self._rhs_max_accessed_signal_width(p) for p in expr.parts),  # type: ignore[union-attr]
                default=0,
            )
        if et is FunctionCall:
            return max(
                (self._rhs_max_accessed_signal_width(a) for a in expr.arguments),  # type: ignore[union-attr]
                default=0,
            )
        if et is TernaryOp:
            return max(
                self._rhs_max_accessed_signal_width(expr.condition),  # type: ignore[union-attr]
                self._rhs_max_accessed_signal_width(expr.true_expr),  # type: ignore[union-attr]
                self._rhs_max_accessed_signal_width(expr.false_expr),  # type: ignore[union-attr]
            )
        return 0

    def _emit_wide_py_bits_lines(
        self, dst_sid: int, rhs: Expression, *, eval_width: int, indent: int, is_nba: bool
    ) -> list[str] | None:
        # B1: narrow LHS is always handled by the Cython fallback below; skip the
        # Python path so _rhs_max_accessed_signal_width can't inflate eval_width
        # and accidentally trigger the wide Python emitters on a ≤64-bit signal.
        if self._signal_widths[dst_sid] <= _WORD_BITS:
            return None
        # Reset per-assign Python expression caches so different assigns don't
        # share memoized strings from different AST nodes that happen to reuse ids.
        self._py_val_cache = {}
        self._py_mask_cache = {}
        eval_width = max(eval_width, self._expr_width(rhs), self._rhs_max_accessed_signal_width(rhs))
        if eval_width <= _WORD_BITS:
            return None

        rhs_py = self._emit_py_expr(rhs, eval_width)
        mask_py = self._emit_py_mask_expr(rhs, eval_width)
        if rhs_py is None or mask_py is None:
            return None

        obj_mask = self._emit_py_width_mask(eval_width)
        bits_value = f"((({rhs_py}) & {obj_mask}) & ~((({mask_py}) & {obj_mask})))"
        bits_mask = f"(({mask_py}) & {obj_mask})"
        pad = "    " * indent
        helper = "_whole_stage_py_bits" if is_nba else "_whole_assign_py_bits"
        return [
            f"{pad}with gil:",
            f"{pad}    {helper}(c, {dst_sid}, {bits_value}, {bits_mask})",
        ]

    def _emit_wide_add_const_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op == "+"
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
            signal_expr = rhs.left.left
            literal_expr = rhs.left.right
        elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
            signal_expr = rhs.left.right
            literal_expr = rhs.left.left

        if signal_expr is None or literal_expr is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_add_const_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {shift_amt})"
        ]

    def _emit_wide_sub_const_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op == "-"
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        stem: str | None = None
        if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
            signal_expr = rhs.left.left
            literal_expr = rhs.left.right
            stem = "sub_const"
        elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
            signal_expr = rhs.left.right
            literal_expr = rhs.left.left
            stem = "const_sub"

        if signal_expr is None or literal_expr is None or stem is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        if stem == "sub_const":
            return [
                f"{pad}_whole_{phase}_{stem}_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {shift_amt})"
            ]
        return [
            f"{pad}_whole_{phase}_{stem}_{direction}(c, {dst_sid}, (<unsigned long long>{literal_word}), {signal_sid}, {shift_amt})"
        ]

    def _emit_wide_mul_const_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op == "*"
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
            signal_expr = rhs.left.left
            literal_expr = rhs.left.right
        elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
            signal_expr = rhs.left.right
            literal_expr = rhs.left.left

        if signal_expr is None or literal_expr is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_mul_const_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {literal_width}, {shift_amt})"
        ]

    def _emit_wide_divmod_const_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op in {"/", "%"}
            and isinstance(rhs.left.left, Identifier)
            and isinstance(rhs.left.right, Literal)
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_sid = self._signal_map.get(self._identifier_name(rhs.left.left))
        literal_width = self._expr_width(rhs.left.right)
        literal_value = _const_int(rhs.left.right, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        src_w = self._signal_widths[signal_sid] if signal_sid is not None else 0
        if (
            signal_sid is None
            or literal_value is None
            or literal_value <= 0
            or literal_width > 32
            or shift_amt is None
            or shift_amt < 0
            or src_w <= _WORD_BITS
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        stem = {"/": "div", "%": "mod"}[rhs.left.op]
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        if lhs_w != src_w:
            py_helper = "_whole_stage_py_value" if is_nba else "_whole_assign_py_value"
            x_helper = f"_whole_{phase}_x_signal_{direction}"
            op = "//" if rhs.left.op == "/" else "%"
            if rhs.op == "<<":
                src_mask = self._emit_py_width_mask(src_w)
                value_expr = (
                    f"(((_sig_py_unsigned(c, {signal_sid}) {op} (<object>{literal_word})) << {shift_amt}) & {src_mask})"
                )
            else:
                value_expr = f"((_sig_py_unsigned(c, {signal_sid}) {op} (<object>{literal_word})) >> {shift_amt})"
            return [
                f"{pad}if _signal_has_x(c, {signal_sid}):",
                f"{pad}    {x_helper}(c, {dst_sid}, {signal_sid}, {shift_amt})",
                f"{pad}else:",
                f"{pad}    with gil:",
                f"{pad}        {py_helper}(c, {dst_sid}, {value_expr})",
            ]
        if lhs_w <= _WORD_BITS:
            return None
        return [
            f"{pad}_whole_{phase}_{stem}_const_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {shift_amt})"
        ]

    def _emit_wide_mask_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op == "&"
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
            signal_expr = rhs.left.left
            literal_expr = rhs.left.right
        elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
            signal_expr = rhs.left.right
            literal_expr = rhs.left.left

        if signal_expr is None or literal_expr is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_mask_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {shift_amt})"
        ]

    def _emit_wide_or_xor_const_shift_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (
            isinstance(rhs, BinaryOp)
            and rhs.op in {"<<", ">>"}
            and isinstance(rhs.left, BinaryOp)
            and rhs.left.op in {"|", "^"}
            and isinstance(rhs.right, Literal)
        ):
            return None

        signal_expr: Identifier | None = None
        literal_expr: Literal | None = None
        if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
            signal_expr = rhs.left.left
            literal_expr = rhs.left.right
        elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
            signal_expr = rhs.left.right
            literal_expr = rhs.left.left

        if signal_expr is None or literal_expr is None:
            return None

        signal_sid = self._signal_map.get(self._identifier_name(signal_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        shift_amt = _const_int(rhs.right, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            signal_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or shift_amt is None
            or shift_amt < 0
            or (lhs_w <= _WORD_BITS and self._signal_widths[signal_sid] <= _WORD_BITS)
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        stem = {"|": "or_const", "^": "xor_const"}[rhs.left.op]
        direction = {"<<": "shl", ">>": "shr"}[rhs.op]
        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
        return [
            f"{pad}_whole_{phase}_{stem}_{direction}(c, {dst_sid}, {signal_sid}, (<unsigned long long>{literal_word}), {shift_amt})"
        ]

    def _emit_wide_const_mixed_add_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "+"):
            return None

        add1_expr: Identifier | None = None
        add2_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "+" and isinstance(rhs.right, Identifier):
            add2_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                add1_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                add1_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "+":
            add2_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                add1_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                add1_expr = rhs.right.right
                literal_expr = rhs.right.left

        if add1_expr is None or add2_expr is None or literal_expr is None:
            return None

        add1_sid = self._signal_map.get(self._identifier_name(add1_expr))
        add2_sid = self._signal_map.get(self._identifier_name(add2_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            add1_sid is None
            or add2_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[add1_sid] <= _WORD_BITS
                and self._signal_widths[add2_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {add1_sid}) or _sig_has_unknown(c, {add2_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_add_const_add_signal(c, {dst_sid}, {add1_sid}, (<unsigned long long>{literal_word}), {add2_sid})",
        ]

    def _emit_wide_const_sub_add_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "+"):
            return None

        helper_stem: str | None = None
        sub_expr: Identifier | None = None
        add_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "-" and isinstance(rhs.right, Identifier):
            add_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                helper_stem = "sub_const_add_signal"
                sub_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                helper_stem = "const_sub_add_signal"
                sub_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "-":
            add_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                helper_stem = "sub_const_add_signal"
                sub_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                helper_stem = "const_sub_add_signal"
                sub_expr = rhs.right.right
                literal_expr = rhs.right.left

        if helper_stem is None or sub_expr is None or add_expr is None or literal_expr is None:
            return None

        sub_sid = self._signal_map.get(self._identifier_name(sub_expr))
        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            sub_sid is None
            or add_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[sub_sid] <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {sub_sid}) or _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {sub_sid}, (<unsigned long long>{literal_word}), {add_sid})",
        ]

    def _emit_wide_const_xor_add_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "+"):
            return None

        xor_expr: Identifier | None = None
        add_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "^" and isinstance(rhs.right, Identifier):
            add_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                xor_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                xor_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "^":
            add_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                xor_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                xor_expr = rhs.right.right
                literal_expr = rhs.right.left

        if xor_expr is None or add_expr is None or literal_expr is None:
            return None

        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            xor_sid is None
            or add_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {xor_sid}) or _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_xor_const_add_signal(c, {dst_sid}, {xor_sid}, (<unsigned long long>{literal_word}), {add_sid})",
        ]

    def _emit_wide_const_xor_and_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "&"):
            return None

        xor_expr: Identifier | None = None
        and_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "^" and isinstance(rhs.right, Identifier):
            and_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                xor_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                xor_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "^":
            and_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                xor_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                xor_expr = rhs.right.right
                literal_expr = rhs.right.left

        if xor_expr is None or and_expr is None or literal_expr is None:
            return None

        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        and_sid = self._signal_map.get(self._identifier_name(and_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            xor_sid is None
            or and_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
                and self._signal_widths[and_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_xor_const_and_signal(c, {dst_sid}, {xor_sid}, (<unsigned long long>{literal_word}), {and_sid})"
        ]

    def _emit_wide_const_xor_or_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "|"):
            return None

        xor_expr: Identifier | None = None
        or_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "^" and isinstance(rhs.right, Identifier):
            or_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                xor_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                xor_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "^":
            or_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                xor_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                xor_expr = rhs.right.right
                literal_expr = rhs.right.left

        if xor_expr is None or or_expr is None or literal_expr is None:
            return None

        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            xor_sid is None
            or or_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_xor_const_or_signal(c, {dst_sid}, {xor_sid}, (<unsigned long long>{literal_word}), {or_sid})"
        ]

    def _emit_wide_const_xor_xor_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "^"):
            return None

        xor_expr: Identifier | None = None
        rhs_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "^" and isinstance(rhs.right, Identifier):
            rhs_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                xor_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                xor_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "^":
            rhs_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                xor_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                xor_expr = rhs.right.right
                literal_expr = rhs.right.left

        if xor_expr is None or rhs_expr is None or literal_expr is None:
            return None

        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        rhs_sid = self._signal_map.get(self._identifier_name(rhs_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            xor_sid is None
            or rhs_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
                and self._signal_widths[rhs_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_xor_const_xor_signal(c, {dst_sid}, {xor_sid}, (<unsigned long long>{literal_word}), {rhs_sid})"
        ]

    def _emit_wide_const_add_xor_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "^"):
            return None

        add_expr: Identifier | None = None
        xor_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "+" and isinstance(rhs.right, Identifier):
            xor_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                add_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                add_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "+":
            xor_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                add_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                add_expr = rhs.right.right
                literal_expr = rhs.right.left

        if add_expr is None or xor_expr is None or literal_expr is None:
            return None

        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            add_sid is None
            or xor_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_add_const_xor_signal(c, {dst_sid}, {add_sid}, (<unsigned long long>{literal_word}), {xor_sid})",
        ]

    def _emit_wide_const_add_and_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "&"):
            return None

        add_expr: Identifier | None = None
        and_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "+" and isinstance(rhs.right, Identifier):
            and_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                add_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                add_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "+":
            and_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                add_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                add_expr = rhs.right.right
                literal_expr = rhs.right.left

        if add_expr is None or and_expr is None or literal_expr is None:
            return None

        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        and_sid = self._signal_map.get(self._identifier_name(and_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            add_sid is None
            or and_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
                and self._signal_widths[and_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_add_const_and_signal(c, {dst_sid}, {add_sid}, (<unsigned long long>{literal_word}), {and_sid})",
        ]

    def _emit_wide_const_add_or_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "|"):
            return None

        add_expr: Identifier | None = None
        or_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "+" and isinstance(rhs.right, Identifier):
            or_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                add_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                add_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "+":
            or_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                add_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                add_expr = rhs.right.right
                literal_expr = rhs.right.left

        if add_expr is None or or_expr is None or literal_expr is None:
            return None

        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            add_sid is None
            or or_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_add_const_or_signal(c, {dst_sid}, {add_sid}, (<unsigned long long>{literal_word}), {or_sid})",
        ]

    def _emit_wide_const_sub_sub_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "-"):
            return None

        helper_stem: str | None = None
        primary_expr: Identifier | None = None
        secondary_expr: Identifier | None = None
        literal_expr: Literal | None = None
        literal_last = True

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "-" and isinstance(rhs.right, Identifier):
            secondary_expr = rhs.right
            literal_last = False
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                helper_stem = "sub_const_sub_signal"
                primary_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                helper_stem = "const_sub_sub_signal"
                primary_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "-":
            primary_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                helper_stem = "sub_signal_sub_const"
                secondary_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                helper_stem = "sub_signal_const_sub"
                secondary_expr = rhs.right.right
                literal_expr = rhs.right.left
                literal_last = False

        if helper_stem is None or primary_expr is None or secondary_expr is None or literal_expr is None:
            return None

        primary_sid = self._signal_map.get(self._identifier_name(primary_expr))
        secondary_sid = self._signal_map.get(self._identifier_name(secondary_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            primary_sid is None
            or secondary_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[primary_sid] <= _WORD_BITS
                and self._signal_widths[secondary_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        if literal_last:
            return [
                f"{pad}if _sig_has_unknown(c, {primary_sid}) or _sig_has_unknown(c, {secondary_sid}):",
                *fallback_lines,
                f"{pad}else:",
                f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {primary_sid}, {secondary_sid}, (<unsigned long long>{literal_word}))",
            ]
        return [
            f"{pad}if _sig_has_unknown(c, {primary_sid}) or _sig_has_unknown(c, {secondary_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {primary_sid}, (<unsigned long long>{literal_word}), {secondary_sid})",
        ]

    def _emit_wide_const_sub_and_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "&"):
            return None

        helper_stem: str | None = None
        sub_expr: Identifier | None = None
        and_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "-" and isinstance(rhs.right, Identifier):
            and_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                helper_stem = "sub_const_and_signal"
                sub_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                helper_stem = "const_sub_and_signal"
                sub_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "-":
            and_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                helper_stem = "sub_const_and_signal"
                sub_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                helper_stem = "const_sub_and_signal"
                sub_expr = rhs.right.right
                literal_expr = rhs.right.left

        if helper_stem is None or sub_expr is None or and_expr is None or literal_expr is None:
            return None

        sub_sid = self._signal_map.get(self._identifier_name(sub_expr))
        and_sid = self._signal_map.get(self._identifier_name(and_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            sub_sid is None
            or and_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[sub_sid] <= _WORD_BITS
                and self._signal_widths[and_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {sub_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {sub_sid}, (<unsigned long long>{literal_word}), {and_sid})",
        ]

    def _emit_wide_const_sub_or_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "|"):
            return None

        helper_stem: str | None = None
        sub_expr: Identifier | None = None
        or_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "-" and isinstance(rhs.right, Identifier):
            or_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                helper_stem = "sub_const_or_signal"
                sub_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                helper_stem = "const_sub_or_signal"
                sub_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "-":
            or_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                helper_stem = "sub_const_or_signal"
                sub_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                helper_stem = "const_sub_or_signal"
                sub_expr = rhs.right.right
                literal_expr = rhs.right.left

        if helper_stem is None or sub_expr is None or or_expr is None or literal_expr is None:
            return None

        sub_sid = self._signal_map.get(self._identifier_name(sub_expr))
        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            sub_sid is None
            or or_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[sub_sid] <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {sub_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {sub_sid}, (<unsigned long long>{literal_word}), {or_sid})",
        ]

    def _emit_wide_or_mask_and_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "&"):
            return None

        or_expr: Identifier | None = None
        and_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "|" and isinstance(rhs.right, Identifier):
            and_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                or_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "|":
            and_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                or_expr = rhs.right.left
                literal_expr = rhs.right.right

        if or_expr is None or and_expr is None or literal_expr is None:
            return None

        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        and_sid = self._signal_map.get(self._identifier_name(and_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            or_sid is None
            or and_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
                and self._signal_widths[and_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_or_mask_and_signal(c, {dst_sid}, {or_sid}, (<unsigned long long>{literal_word}), {and_sid})"
        ]

    def _emit_wide_or_mask_or_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "|"):
            return None

        primary_or_expr: Identifier | None = None
        secondary_or_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "|" and isinstance(rhs.right, Identifier):
            secondary_or_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                primary_or_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "|":
            secondary_or_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                primary_or_expr = rhs.right.left
                literal_expr = rhs.right.right

        if primary_or_expr is None or secondary_or_expr is None or literal_expr is None:
            return None

        primary_or_sid = self._signal_map.get(self._identifier_name(primary_or_expr))
        secondary_or_sid = self._signal_map.get(self._identifier_name(secondary_or_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            primary_or_sid is None
            or secondary_or_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[primary_or_sid] <= _WORD_BITS
                and self._signal_widths[secondary_or_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_or_mask_or_signal(c, {dst_sid}, {primary_or_sid}, (<unsigned long long>{literal_word}), {secondary_or_sid})"
        ]

    def _emit_wide_or_mask_xor_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "^"):
            return None

        or_expr: Identifier | None = None
        xor_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "|" and isinstance(rhs.right, Identifier):
            xor_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                or_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "|":
            xor_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                or_expr = rhs.right.left
                literal_expr = rhs.right.right

        if or_expr is None or xor_expr is None or literal_expr is None:
            return None

        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            or_sid is None
            or xor_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_or_mask_xor_signal(c, {dst_sid}, {or_sid}, (<unsigned long long>{literal_word}), {xor_sid})"
        ]

    def _emit_wide_mask_or_lines(self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "|"):
            return None

        mask_expr: Identifier | None = None
        or_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "&" and isinstance(rhs.right, Identifier):
            or_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                mask_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "&":
            or_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                mask_expr = rhs.right.left
                literal_expr = rhs.right.right

        if mask_expr is None or or_expr is None or literal_expr is None:
            return None

        mask_sid = self._signal_map.get(self._identifier_name(mask_expr))
        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            mask_sid is None
            or or_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[mask_sid] <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_mask_or_signal(c, {dst_sid}, {mask_sid}, (<unsigned long long>{literal_word}), {or_sid})"
        ]

    def _emit_wide_mask_and_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "&"):
            return None

        mask_expr: Identifier | None = None
        and_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "&" and isinstance(rhs.right, Identifier):
            and_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                mask_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "&":
            and_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                mask_expr = rhs.right.left
                literal_expr = rhs.right.right

        if mask_expr is None or and_expr is None or literal_expr is None:
            return None

        mask_sid = self._signal_map.get(self._identifier_name(mask_expr))
        and_sid = self._signal_map.get(self._identifier_name(and_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            mask_sid is None
            or and_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[mask_sid] <= _WORD_BITS
                and self._signal_widths[and_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_mask_and_signal(c, {dst_sid}, {mask_sid}, (<unsigned long long>{literal_word}), {and_sid})"
        ]

    def _emit_wide_mask_xor_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "^"):
            return None

        mask_expr: Identifier | None = None
        xor_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "&" and isinstance(rhs.right, Identifier):
            xor_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                mask_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "&":
            xor_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                mask_expr = rhs.right.left
                literal_expr = rhs.right.right

        if mask_expr is None or xor_expr is None or literal_expr is None:
            return None

        mask_sid = self._signal_map.get(self._identifier_name(mask_expr))
        xor_sid = self._signal_map.get(self._identifier_name(xor_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            mask_sid is None
            or xor_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[mask_sid] <= _WORD_BITS
                and self._signal_widths[xor_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        return [
            f"{pad}_whole_{phase}_mask_xor_signal(c, {dst_sid}, {mask_sid}, (<unsigned long long>{literal_word}), {xor_sid})"
        ]

    def _emit_wide_mask_add_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "+"):
            return None

        mask_expr: Identifier | None = None
        add_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "&" and isinstance(rhs.right, Identifier):
            add_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                mask_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "&":
            add_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                mask_expr = rhs.right.left
                literal_expr = rhs.right.right

        if mask_expr is None or add_expr is None or literal_expr is None:
            return None

        mask_sid = self._signal_map.get(self._identifier_name(mask_expr))
        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            mask_sid is None
            or add_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[mask_sid] <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {mask_sid}) or _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_mask_add_signal(c, {dst_sid}, {mask_sid}, (<unsigned long long>{literal_word}), {add_sid})",
        ]

    def _emit_wide_or_mask_add_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "+"):
            return None

        or_expr: Identifier | None = None
        add_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "|" and isinstance(rhs.right, Identifier):
            add_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                or_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "|":
            add_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                or_expr = rhs.right.left
                literal_expr = rhs.right.right

        if or_expr is None or add_expr is None or literal_expr is None:
            return None

        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        add_sid = self._signal_map.get(self._identifier_name(add_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            or_sid is None
            or add_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
                and self._signal_widths[add_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        return [
            f"{pad}if _sig_has_unknown(c, {or_sid}) or _sig_has_unknown(c, {add_sid}):",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_or_mask_add_signal(c, {dst_sid}, {or_sid}, (<unsigned long long>{literal_word}), {add_sid})",
        ]

    def _emit_wide_mask_sub_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "-"):
            return None

        helper_stem: str | None = None
        mask_expr: Identifier | None = None
        sub_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "&" and isinstance(rhs.right, Identifier):
            helper_stem = "mask_sub_signal"
            sub_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                mask_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "&":
            helper_stem = "sub_signal_mask"
            sub_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                mask_expr = rhs.right.left
                literal_expr = rhs.right.right

        if helper_stem is None or mask_expr is None or sub_expr is None or literal_expr is None:
            return None

        mask_sid = self._signal_map.get(self._identifier_name(mask_expr))
        sub_sid = self._signal_map.get(self._identifier_name(sub_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            mask_sid is None
            or sub_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[mask_sid] <= _WORD_BITS
                and self._signal_widths[sub_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        unknown_cond = f"_sig_has_unknown(c, {mask_sid}) or _sig_has_unknown(c, {sub_sid})"
        if helper_stem == "mask_sub_signal":
            return [
                f"{pad}if {unknown_cond}:",
                *fallback_lines,
                f"{pad}else:",
                f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {mask_sid}, (<unsigned long long>{literal_word}), {sub_sid})",
            ]
        return [
            f"{pad}if {unknown_cond}:",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {sub_sid}, {mask_sid}, (<unsigned long long>{literal_word}))",
        ]

    def _emit_wide_or_mask_sub_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "-"):
            return None

        helper_stem: str | None = None
        or_expr: Identifier | None = None
        sub_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "|" and isinstance(rhs.right, Identifier):
            helper_stem = "or_mask_sub_signal"
            sub_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                or_expr = rhs.left.left
                literal_expr = rhs.left.right
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op == "|":
            helper_stem = "sub_signal_or_mask"
            sub_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                or_expr = rhs.right.left
                literal_expr = rhs.right.right

        if helper_stem is None or or_expr is None or sub_expr is None or literal_expr is None:
            return None

        or_sid = self._signal_map.get(self._identifier_name(or_expr))
        sub_sid = self._signal_map.get(self._identifier_name(sub_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            or_sid is None
            or sub_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[or_sid] <= _WORD_BITS
                and self._signal_widths[sub_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        unknown_cond = f"_sig_has_unknown(c, {or_sid}) or _sig_has_unknown(c, {sub_sid})"
        if helper_stem == "or_mask_sub_signal":
            return [
                f"{pad}if {unknown_cond}:",
                *fallback_lines,
                f"{pad}else:",
                f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {or_sid}, (<unsigned long long>{literal_word}), {sub_sid})",
            ]
        return [
            f"{pad}if {unknown_cond}:",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {sub_sid}, {or_sid}, (<unsigned long long>{literal_word}))",
        ]

    def _emit_wide_const_xor_sub_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not isinstance(rhs, BinaryOp):
            return None

        helper_stem: str | None = None
        primary_expr: Identifier | None = None
        secondary_expr: Identifier | None = None
        literal_expr: Literal | None = None

        if isinstance(rhs.left, BinaryOp) and isinstance(rhs.right, Identifier):
            secondary_expr = rhs.right
            if rhs.op == "-" and rhs.left.op == "^":
                helper_stem = "xor_const_sub_signal"
                if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                    primary_expr = rhs.left.left
                    literal_expr = rhs.left.right
                elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                    primary_expr = rhs.left.right
                    literal_expr = rhs.left.left
            elif rhs.op == "^" and rhs.left.op == "-":
                if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                    helper_stem = "sub_const_xor_signal"
                    primary_expr = rhs.left.left
                    literal_expr = rhs.left.right
                elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                    helper_stem = "const_sub_xor_signal"
                    primary_expr = rhs.left.right
                    literal_expr = rhs.left.left
        elif (
            isinstance(rhs.left, Identifier)
            and isinstance(rhs.right, BinaryOp)
            and rhs.op == "^"
            and rhs.right.op == "-"
        ):
            secondary_expr = rhs.left
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                helper_stem = "sub_const_xor_signal"
                primary_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                helper_stem = "const_sub_xor_signal"
                primary_expr = rhs.right.right
                literal_expr = rhs.right.left

        if helper_stem is None or primary_expr is None or secondary_expr is None or literal_expr is None:
            return None

        primary_sid = self._signal_map.get(self._identifier_name(primary_expr))
        secondary_sid = self._signal_map.get(self._identifier_name(secondary_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            primary_sid is None
            or secondary_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[primary_sid] <= _WORD_BITS
                and self._signal_widths[secondary_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        if helper_stem == "xor_const_sub_signal":
            unknown_cond = f"_sig_has_unknown(c, {primary_sid}) or _sig_has_unknown(c, {secondary_sid})"
        else:
            unknown_cond = f"_sig_has_unknown(c, {primary_sid})"
        return [
            f"{pad}if {unknown_cond}:",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {primary_sid}, (<unsigned long long>{literal_word}), {secondary_sid})",
        ]

    def _emit_wide_const_mixed_sub_lines(
        self, dst_sid: int, rhs: Expression, *, indent: int, is_nba: bool
    ) -> list[str] | None:
        if not (isinstance(rhs, BinaryOp) and rhs.op == "-"):
            return None

        helper_stem: str | None = None
        primary_expr: Identifier | None = None
        secondary_expr: Identifier | None = None
        literal_expr: Literal | None = None
        literal_last = True

        if isinstance(rhs.left, BinaryOp) and rhs.left.op == "+" and isinstance(rhs.right, Identifier):
            helper_stem = "add_const_sub_signal"
            literal_last = False
            secondary_expr = rhs.right
            if isinstance(rhs.left.left, Identifier) and isinstance(rhs.left.right, Literal):
                primary_expr = rhs.left.left
                literal_expr = rhs.left.right
            elif isinstance(rhs.left.left, Literal) and isinstance(rhs.left.right, Identifier):
                primary_expr = rhs.left.right
                literal_expr = rhs.left.left
        elif isinstance(rhs.left, Identifier) and isinstance(rhs.right, BinaryOp) and rhs.right.op in {"+", "^"}:
            primary_expr = rhs.left
            helper_stem = {
                "+": "sub_signal_add_const",
                "^": "sub_signal_xor_const",
            }[rhs.right.op]
            if isinstance(rhs.right.left, Identifier) and isinstance(rhs.right.right, Literal):
                secondary_expr = rhs.right.left
                literal_expr = rhs.right.right
            elif isinstance(rhs.right.left, Literal) and isinstance(rhs.right.right, Identifier):
                secondary_expr = rhs.right.right
                literal_expr = rhs.right.left

        if helper_stem is None or primary_expr is None or secondary_expr is None or literal_expr is None:
            return None

        primary_sid = self._signal_map.get(self._identifier_name(primary_expr))
        secondary_sid = self._signal_map.get(self._identifier_name(secondary_expr))
        literal_width = self._expr_width(literal_expr)
        literal_value = _const_int(literal_expr, self._param_env)
        lhs_w = self._signal_widths[dst_sid]
        if (
            primary_sid is None
            or secondary_sid is None
            or literal_value is None
            or literal_value < 0
            or literal_width > _WORD_BITS
            or (
                lhs_w <= _WORD_BITS
                and self._signal_widths[primary_sid] <= _WORD_BITS
                and self._signal_widths[secondary_sid] <= _WORD_BITS
            )
        ):
            return None

        literal_mask = (1 << literal_width) - 1 if literal_width < _WORD_BITS else ((1 << _WORD_BITS) - 1)
        literal_word = literal_value & literal_mask
        pad = "    " * indent
        phase = "stage" if is_nba else "assign"
        fallback_lines = self._emit_wide_py_bits_lines(dst_sid, rhs, eval_width=lhs_w, indent=indent + 1, is_nba=is_nba)
        if fallback_lines is None:
            return None
        unknown_cond = f"_sig_has_unknown(c, {primary_sid}) or _sig_has_unknown(c, {secondary_sid})"
        if literal_last:
            return [
                f"{pad}if {unknown_cond}:",
                *fallback_lines,
                f"{pad}else:",
                f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {primary_sid}, {secondary_sid}, (<unsigned long long>{literal_word}))",
            ]
        return [
            f"{pad}if {unknown_cond}:",
            *fallback_lines,
            f"{pad}else:",
            f"{pad}    _whole_{phase}_{helper_stem}(c, {dst_sid}, {primary_sid}, (<unsigned long long>{literal_word}), {secondary_sid})",
        ]

    # ── Phase 1: Recursive wide expression emitter ───────────────────────

    _WIDE_BINARY_PRIMS: ClassVar[dict[str, str]] = {
        "&": "wide_and",
        "|": "wide_or",
        "^": "wide_xor",
        "~^": "wide_xor",
        "^~": "wide_xor",
        "+": "wide_add",
        "-": "wide_sub",
        "*": "wide_mul",
        "/": "wide_div",
        "%": "wide_mod",
    }
    _WIDE_SHIFT_PRIMS: ClassVar[dict[str, str]] = {
        "<<": "wide_shl",
        ">>": "wide_shr",
        ">>>": "wide_ashr",
    }
    # Comparison ops: value is prim name; key with "_r" suffix means swap operands.
    # >  → wide_cmp_lt(b, a)  and  >= → wide_cmp_le(b, a)
    _WIDE_CMP_PRIMS: ClassVar[dict[str, tuple[str, bool]]] = {
        "==": ("wide_cmp_eq", False),
        "===": ("wide_cmp_eq", False),
        "!=": ("wide_cmp_ne", False),
        "!==": ("wide_cmp_ne", False),
        "<": ("wide_cmp_lt", False),
        "<=": ("wide_cmp_le", False),
        ">": ("wide_cmp_lt", True),  # swap: a > b  ≡  b < a
        ">=": ("wide_cmp_le", True),  # swap: a >= b ≡  b <= a
    }

    def _literal_wide_words(self, expr: Literal, n_words: int) -> tuple[list[int], list[int]] | None:
        """Return (val_words, mask_words) lists for a wide or narrow Literal.

        Returns None if the literal cannot be resolved to a concrete value.
        Each list has exactly n_words entries (64-bit chunks, LSW first).
        """
        if expr.original_text:
            try:
                value = Value.from_verilog(expr.original_text)
            except ValueError:
                return None
            val_int = value.val
            mask_int = value.mask
        elif (hasattr(expr, "is_x") and expr.is_x) or (hasattr(expr, "is_z") and expr.is_z):
            lit_w = expr.width or 32
            val_int = 0
            mask_int = (1 << lit_w) - 1
        else:
            if isinstance(expr.value, (int, float)):
                val_int = int(expr.value)
            elif isinstance(expr.value, str) and expr.value.strip():
                try:
                    val_int = int(expr.value.strip(), 0)
                except (ValueError, TypeError):
                    return None
            else:
                return None
            mask_int = 0

        chunk = 0xFFFF_FFFF_FFFF_FFFF
        val_words = [(val_int >> (i * 64)) & chunk for i in range(n_words)]
        mask_words = [(mask_int >> (i * 64)) & chunk for i in range(n_words)]
        return val_words, mask_words

    def _emit_wide_expr_to_scratch(
        self,
        expr: Expression,
        slot: int,
        n_words: int,
        dst_width: int,
        indent: int,
        *,
        signed_override: bool | None = None,
    ) -> list[str] | None:
        """Recursively evaluate *expr* into scratch slot *slot*.

        Emits Cython lines that write the result into ``_sc{slot}_v`` /
        ``_sc{slot}_m``.  Returns None if the expression type is not yet handled
        by the new emitter (caller falls back to existing pattern matchers).

        ``n_words``  — number of 64-bit words in each scratch array.
        ``dst_width`` — actual bit width of the result (for tail masking).
        ``signed_override`` — if set, forces sign- (True) or zero- (False)
        extension for a narrower Identifier source, overriding the signal's
        own declared signedness (used by the ``$signed``/``$unsigned`` cast
        cases below). ``None`` means "use the signal's own declared
        signedness", the same as an uncast identifier.
        """
        pad = "    " * indent
        et = type(expr)

        # ── Identifier ──────────────────────────────────────────────────────
        if et is Identifier:
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            if sid is not None:
                src_signed = (
                    signed_override
                    if signed_override is not None
                    else (sid < len(self._signal_signed) and self._signal_signed[sid])
                )
                if src_signed and dst_width > self._signal_widths[sid]:
                    # `wide_load_signal_s` only understands whole WORDS --
                    # it always sign-fills every bit through the end of
                    # word `n_words - 1`, regardless of how many of those
                    # bits actually belong to `dst_width`. That over-fills
                    # whenever `dst_width` isn't a multiple of 64 (e.g. a
                    # 1-bit signed operand widened to a 2-bit comparison
                    # context, `n_words=1`: the sign-extend fills the
                    # ENTIRE 64-bit word, not just the low 2 bits), leaving
                    # extra high-order 1 bits set beyond `dst_width` that
                    # the caller assumes are 0 -- silently corrupting a
                    # SIBLING operand's own bit-pattern comparison against
                    # this one, since both were meant to be masked to the
                    # same shared `dst_width` first. Mirrors the identical
                    # `_wide_sign_extend_to_dst_lines` fix for
                    # `wide_sign_extend` -- explicit tail masking after the
                    # call restricts the result to precisely `dst_width`
                    # bits. Confirmed against Icarus (cross-engine) for
                    # `($signed(a3[8:7]) != $signed(a7))` widened to a
                    # 256-bit destination: `a7` (1-bit) sign-extended via
                    # `wide_load_signal_s` to the full 64-bit word instead
                    # of just the comparison's own 2-bit width, so its
                    # raw word no longer bit-for-bit matched the OTHER
                    # (correctly 2-bit-masked) operand's word even though
                    # both represented the same 2-bit value, spuriously
                    # making `!=` true.
                    lines = [f"{pad}wide_load_signal_s(c, {sid}, _sc{slot}_v, _sc{slot}_m, {n_words})"]
                    dst_n = (dst_width + 63) // 64
                    tail_bits = dst_width - (dst_n - 1) * 64
                    if tail_bits < _WORD_BITS:
                        lines.append(f"{pad}_sc{slot}_v[{dst_n - 1}] &= _word_mask64({tail_bits})")
                        lines.append(f"{pad}_sc{slot}_m[{dst_n - 1}] &= _word_mask64({tail_bits})")
                    for wi in range(dst_n, n_words):
                        lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                        lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                    return lines
                return [f"{pad}wide_load_signal(c, {sid}, _sc{slot}_v, _sc{slot}_m, {n_words})"]

            # Try struct field or memory element field access.
            storage_info = self._resolve_struct_storage_access(name)
            if storage_info is not None:
                kind, id_, index_spec, field_lsb, field_width = storage_info
                n_dst = (field_width + 63) // 64
                if kind == "signal":
                    base_sid = id_
                    base_width = self._signal_widths[base_sid]
                    n_base = (base_width + 63) // 64
                    base_slot = self._alloc_scratch()
                    lines: list[str] = [
                        f"{pad}wide_load_signal(c, {base_sid}, _sc{base_slot}_v, _sc{base_slot}_m, {n_base})",
                        f"{pad}wide_slice_extract(_sc{slot}_v, _sc{slot}_m, _sc{base_slot}_v, _sc{base_slot}_m, {field_lsb}, {field_width}, {n_base}, {n_dst})",
                    ]
                    # Same signed_override reasoning as RangeSelect below --
                    # a struct field is a slice, always unsigned in its own
                    # right, but a $signed(...) wrapper still needs sign
                    # extension beyond its own width. The fill must stop at
                    # dst_width (this call's own requested width), NOT
                    # n_words (the scratch array's max size for the WHOLE
                    # statement) -- e.g. a $signed()-wrapped concat member
                    # only needs its own few bits filled, not the full
                    # array, otherwise it corrupts neighboring concat
                    # members sharing the same scratch words.
                    if signed_override:
                        lines.extend(
                            self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(field_width), indent)
                        )
                    else:
                        for wi in range(n_dst, n_words):
                            lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                            lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                    self._free_scratch(base_slot)
                    return lines
                if kind == "memory":
                    mid = id_
                    addr_expr = self._emit_struct_storage_index_expr(index_spec)
                    if addr_expr is None:
                        return None
                    n_elem = self._mem_words(mid)
                    mem_slot = self._alloc_scratch()
                    lines = [
                        f"{pad}wide_load_wmem{mid}(c, {addr_expr}, _sc{mem_slot}_v, _sc{mem_slot}_m, {n_elem})",
                        f"{pad}wide_slice_extract(_sc{slot}_v, _sc{slot}_m, _sc{mem_slot}_v, _sc{mem_slot}_m, {field_lsb}, {field_width}, {n_elem}, {n_dst})",
                    ]
                    if signed_override:
                        lines.extend(
                            self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(field_width), indent)
                        )
                    else:
                        for wi in range(n_dst, n_words):
                            lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                            lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                    self._free_scratch(mem_slot)
                    return lines

            return None  # local var — not yet handled

        # ── Literal ─────────────────────────────────────────────────────────
        if et is Literal:
            words = self._literal_wide_words(expr, n_words)
            if words is None:
                return None
            val_words, mask_words = words
            lines: list[str] = []
            for wi in range(n_words):
                lines.append(f"{pad}_sc{slot}_v[{wi}] = {_cy_u64_hex(val_words[wi])}")
                lines.append(f"{pad}_sc{slot}_m[{wi}] = {_cy_u64_hex(mask_words[wi])}")
            # _literal_wide_words always zero-fills beyond the literal's own
            # declared width -- correct for an unsigned literal, but wrong
            # for a declared-signed literal (e.g. `4'sb1000` = -8) whose own
            # top bit is 1: that needs sign-extension into a wider
            # destination, same as a signed Identifier/RangeSelect/etc.
            # `signed_override`, when set (a $signed() cast or an enclosing
            # ternary/bitwise-op's forced signedness), takes precedence over
            # the literal's own declared signedness, same as everywhere else.
            eff_signed = signed_override if signed_override is not None else expr.signed
            lit_w = self._expr_width(expr)
            if eff_signed and lit_w < dst_width:
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(lit_w), indent))
            return lines

        # ── BitSelect ───────────────────────────────────────────────────────
        # Always exactly 1 bit, self-determined and unsigned (IEEE 1364-2005
        # §5.5.1) regardless of the target's own width or signedness -- reuse
        # the existing narrow (scalar) emitters, which already correctly
        # handle bit-selects on wide targets via word-extraction helpers, and
        # just drop the 1-bit result into scratch word 0. Without this case,
        # a BitSelect nested anywhere inside a wide-context expression (e.g.
        # `{a1[0], a3[8:7]}` assigned to a >64-bit destination) makes the
        # whole recursive emission bail out to None here, silently falling
        # through to the narrow scalar LHS-write fallback -- which is wrong
        # for a >64-bit destination (`c.val`/`c.mask` are 64-bit fields) and
        # was observed to drop the x-mask entirely for NBA assignments,
        # which -- unlike continuous assigns -- have no separate/redundant
        # wide-array-updating code path to fall back on.
        if et is BitSelect:
            val_expr = self._emit_expr(expr, 1)
            mask_expr = self._emit_mask_expr(expr, 1)
            if signed_override:
                # An explicit $signed() cast forces sign-extension of this
                # 1-bit value: bit=1 sign-extends to all-1s (a 1-bit two's-
                # complement 1 represents -1), bit=0 to all-0s, and if the
                # bit itself is x/z, the fill is x/z too (not forced to a
                # defined 0 -- that earlier "conservative" choice was wrong,
                # see wide_load_signal_s's identical fix elsewhere in this
                # file). Reuses the shared sign-extend-to-dst-width helper
                # (bounds the fill to dst_width, not n_words -- needed when
                # this BitSelect is a small concat member) after seeding
                # word 0 with the raw 1-bit result.
                lines = [
                    f"{pad}_sc{slot}_v[0] = <unsigned long long>({val_expr}) & 1",
                    f"{pad}_sc{slot}_m[0] = <unsigned long long>({mask_expr}) & 1",
                ]
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, "1", indent))
                return lines
            lines = [
                f"{pad}_sc{slot}_v[0] = <unsigned long long>({val_expr}) & 1",
                f"{pad}_sc{slot}_m[0] = <unsigned long long>({mask_expr}) & 1",
            ]
            for wi in range(1, n_words):
                lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
            return lines

        # ── UnaryOp ─────────────────────────────────────────────────────────
        if et is UnaryOp:
            op = expr.op

            # Unary identity — evaluate operand directly into the destination slot
            if op == "+":
                return self._emit_wide_expr_to_scratch(expr.operand, slot, n_words, dst_width, indent)

            # Bitwise invert / negate — operator applies at the context
            # (dst_width) width per IEEE 1364-2005 (context-determined, not
            # self-determined — see notes/known_issues.md): the operand must
            # be extended to the FULL dst_width *before* wide_not/wide_neg
            # runs, not computed at its own self-width and patched up
            # afterward -- computing `~x` at x's own narrow self-width then
            # zero/sign-padding the *result* is not equivalent to extending
            # x to the context width first and complementing all of it
            # (e.g. x=0 at width 1: `~x` self-determined = 1, zero-padded =
            # 0x1 -- but extending x to width 96 first (=0) then
            # complementing gives 0xFFF...FFF). So recurse directly at
            # dst_width, exactly like the `+` case above -- the operand's
            # own recursive emission (leaf or further nested
            # context-determined operator) already handles the
            # signed/unsigned extension up to whatever width it is asked
            # for. `signed_override`, when set (this UnaryOp is itself a
            # ternary branch), forces that decision instead of the
            # operand's own declared signedness (IEEE 1364-2005 §5.5.1),
            # propagated into the recursive call so a nested signed
            # Identifier further down agrees with this node's own decision.
            if op in {"~", "-"}:
                prim = "wide_not" if op == "~" else "wide_neg"
                # EXCEPTION to the "recurse at dst_width" rule above: when
                # the operand's own result is ALWAYS fixed at 1 bit
                # regardless of context (IEEE 1364-2005 Table 5-22:
                # comparisons, &&/||, reduction ops, !), recursing at
                # dst_width doesn't actually widen the VALUE (the operand's
                # own wide-emitter case ignores dst_width and always
                # produces a proper 1-bit result zero-filled into the rest
                # of the scratch words) -- but `wide_not`/`wide_neg`
                # afterward then complements/negates ALL dst_width bits of
                # that zero-filled operand, which is wrong the same way the
                # narrow emitter's identical bug was (zero-extending a 1-bit
                # `&&` result before complementing gives `~0...01` instead
                # of the correct `resize(~1'b1)`). Compute at the operand's
                # own fixed 1-bit width instead, THEN extend the RESULT.
                # Confirmed against Icarus for
                # `$signed(~({a0, a6, a0} && a7)))`; mirrors the identical
                # fix in `_expr_emitter.py`/`sim/evaluator.py`/
                # `sim/vm/compiler.py`.
                #
                # This fixed-width special case applies to `~` ONLY, not
                # `-` (unary minus): `~` is bitwise/per-bit-independent, so
                # zero-extending before complementing flips the padding
                # bits too (wrong, per the confirmed case above) -- but
                # `-` is a genuine two's-complement ARITHMETIC negation,
                # where zero-extending the operand and THEN negating gives
                # exactly the modular wraparound representation of "minus
                # that value" at the wider width, which is what real
                # hardware (and Icarus) actually computes. Confirmed wrong
                # the other way (compute-at-1-bit-then-extend-result gives
                # `1`, not Icarus's `all-ones`/-1) for
                # `-(~&{2{(a5[5:2] < a0)}})` widened into a 96-bit
                # destination -- `-` must always fall through to the
                # normal "recurse at dst_width" path above instead.
                if op == "~" and _is_fixed_self_determined(expr.operand):
                    op_slot = self._alloc_scratch()
                    lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, 1, 1, indent)
                    if lines is None:
                        self._free_scratch(op_slot)
                        return None
                    lines.append(f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, 1, 1)")
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                    if eff_signed:
                        lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, "1", indent))
                    else:
                        for wi in range(1, n_words):
                            lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                            lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                    self._free_scratch(op_slot)
                    return lines
                if op == "-":
                    # `-` (unlike `~`, handled by the plain `n_words`-sized
                    # path below) needs its operand computed at its OWN
                    # full width, not just `n_words`/`dst_width` -- a
                    # single x/z bit ANYWHERE in the operand makes unary
                    # minus's ENTIRE result x (no per-bit borrow-chain
                    # precision, matches Icarus/`wide_neg`'s own "any x/z
                    # in source -> result is all-x" rule), so if the
                    # operand's true width exceeds `dst_width` (e.g. a
                    # Concatenation whose x-bearing member sits above the
                    # visible truncation point), computing it directly at
                    # `n_words`/`dst_width` would silently discard those
                    # x-bearing high bits BEFORE `wide_neg` ever sees them,
                    # turning a genuinely-all-x result into a spurious
                    # fully-defined one. Mirrors the identical "truncate
                    # before widen" fix for the wide shift left operand a
                    # few hundred lines up, and the identical fix in
                    # `sim/vm/compiler.py`'s own UnaryOp `-` handling.
                    # Compute the operand at its own full `op_width`/`op_n`
                    # in a separately-sized scratch slot, explicitly check
                    # for x ANYWHERE across that full range, and only then
                    # either force the all-x result directly or delegate
                    # to `wide_neg` at the normal `n_words` size (safe in
                    # that branch precisely because no x exists anywhere
                    # to miss). Confirmed against Icarus (cross-engine) for
                    # `(-{a3, (^a5[16]), {3{(a7 ? a6[66:21] : a3)}}})`
                    # widened into a 96-bit destination, with `a3` (63
                    # bits, fully x) positioned as the concatenation's
                    # MSB-most member -- the concatenation's own true
                    # width is 202 bits, so `a3`'s x-bearing bits sit at
                    # positions 139-201, entirely above the 96-bit
                    # truncation point.
                    op_width = max(dst_width, self._expr_width(expr.operand))
                    op_n = max(n_words, (op_width + 63) // 64)
                    self._dynamic_max_wide_words = max(self._dynamic_max_wide_words, op_n)
                    op_slot = self._alloc_scratch()
                    lines = self._emit_wide_expr_to_scratch(
                        expr.operand, op_slot, op_n, op_width, indent, signed_override=signed_override
                    )
                    if lines is None:
                        self._free_scratch(op_slot)
                        return None
                    has_x_expr = " or ".join(f"_sc{op_slot}_m[{i}] != 0" for i in range(op_n))
                    lines.append(f"{pad}if {has_x_expr}:")
                    for i in range(n_words):
                        lines.append(f"{pad}    _sc{slot}_v[{i}] = 0")
                        lines.append(f"{pad}    _sc{slot}_m[{i}] = _word_mask64({dst_width} - {i} * 64)")
                    lines.append(f"{pad}else:")
                    lines.append(
                        f"{pad}    {prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{op_slot}_v, _sc{op_slot}_m, {n_words}, {dst_width})"
                    )
                    self._free_scratch(op_slot)
                    return lines
                op_slot = self._alloc_scratch()
                lines = self._emit_wide_expr_to_scratch(
                    expr.operand, op_slot, n_words, dst_width, indent, signed_override=signed_override
                )
                if lines is None:
                    self._free_scratch(op_slot)
                    return None
                lines.append(
                    f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, {n_words}, {dst_width})"
                )
                self._free_scratch(op_slot)
                return lines

            # Reduction operators — 1-bit result in slot[0]. Always unsigned
            # in their own right (IEEE 1364-2005 SS5.5.1): upper words are
            # normally zero, but see the `!` case below for why a
            # `$signed(...)`-wrapped result (signed_override True) must
            # instead replicate bit 0's value AND mask into those words.
            _REDUCE_PRIMS: dict[str, tuple[str, bool]] = {
                "|": ("wide_reduce_or", False),
                "&": ("wide_reduce_and", True),
                "^": ("wide_reduce_xor", True),
                "~|": ("wide_reduce_or", False),
                "~&": ("wide_reduce_and", True),
                "~^": ("wide_reduce_xor", True),
                "^~": ("wide_reduce_xor", True),
            }
            if op in _REDUCE_PRIMS:
                prim_name, needs_src_width = _REDUCE_PRIMS[op]
                op_slot = self._alloc_scratch()
                op_width = self._expr_width(expr.operand)
                op_n = (op_width + 63) // 64
                # Load the operand at ITS OWN required word count, not the
                # inherited (enclosing) `n_words` -- when the reduction's
                # own result feeds into a narrower enclosing context (e.g.
                # `~^a6` as one AND-operand of a 20-bit subtraction, itself
                # a ternary condition routed through wide scratch),
                # `n_words` can be smaller than the operand's true width
                # requires, silently loading only PART of a wide (>64-bit)
                # signal and leaving the rest of its scratch words
                # uninitialized garbage that the reduction then reads.
                # Mirrors the identical, already-correct pattern in the
                # `!` case right below. Confirmed against Icarus for
                # `((^a3) - ({2{a5[21:12]}} & (~^a6))) ? a1 : ...` where
                # `a6` is 80 bits and the enclosing subtraction is only 20.
                #
                # `_dynamic_max_wide_words` (the per-module running peak
                # scratch-array word count, sized once at the END of
                # codegen -- see `_module_max_wide_words`) must ALSO be
                # bumped here: every OTHER "allocate a scratch slot at a
                # width not already implied by an enclosing wide
                # destination or a wide SIGNAL" call site in this file
                # already does this (mirrors the identical update a few
                # dozen lines up, in unary `-`'s own wide handling), but
                # this reduction dispatch never did -- previously masked
                # because every PRIOR caller reaching this code already
                # sat inside an already-wide context (or the operand was
                # itself a wide SIGNAL, separately tracked by
                # `_module_max_wide_words`'s own signal-width scan), so
                # `_dynamic_max_wide_words` always happened to already
                # cover `op_n` by the time array declarations were sized.
                # Confirmed against Icarus for a reduction over a
                # >64-bit COMPUTED (non-signal) operand -- e.g. a
                # concatenation -- reached from a narrow (<=64-bit)
                # calling context with no OTHER wide computation anywhere
                # else in the module: the scratch arrays got declared at
                # only 1 word, one word short of what `wide_or`/
                # `wide_reduce_or` (both called with `n=2` immediately
                # below) actually need, which the Cython compiler itself
                # caught as a type error rather than silently
                # miscompiling.
                self._dynamic_max_wide_words = max(self._dynamic_max_wide_words, n_words, op_n)
                lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, max(n_words, op_n), op_width, indent)
                if lines is None:
                    self._free_scratch(op_slot)
                    return None
                if needs_src_width:
                    lines.append(
                        f"{pad}{prim_name}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{op_slot}_v, _sc{op_slot}_m, {op_n}, {op_width})"
                    )
                else:
                    lines.append(f"{pad}{prim_name}(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, {op_n})")
                if op in {"~|", "~&", "~^", "^~"}:
                    lines.append(f"{pad}_sc{slot}_v[0] = (~_sc{slot}_v[0]) & (~_sc{slot}_m[0]) & 1ULL")
                if signed_override:
                    lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, "1", indent))
                else:
                    for wi in range(1, n_words):
                        lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                        lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                self._free_scratch(op_slot)
                return lines

            # Logical NOT — equivalent to NOR reduction (~|): 1 if operand is
            # all-zero. Always unsigned in its own right (IEEE 1364-2005
            # SS5.5.1), so bits beyond bit 0 are normally zero -- but when
            # wrapped in `$signed(...)` (signed_override True) and the
            # destination is wider than 1 bit, those bits must instead
            # replicate bit 0's own value AND mask (an X result stays X
            # across the whole sign-extended width), not be forced to zero.
            if op == "!":
                op_slot = self._alloc_scratch()
                op_width = self._expr_width(expr.operand)
                op_n = (op_width + 63) // 64
                # Same `_dynamic_max_wide_words` gap and fix as the
                # `_REDUCE_PRIMS` branch just above -- see its own
                # comment for the full rationale.
                self._dynamic_max_wide_words = max(self._dynamic_max_wide_words, n_words, op_n)
                lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, op_n, op_width, indent)
                if lines is None:
                    self._free_scratch(op_slot)
                    return None
                lines.append(f"{pad}wide_reduce_or(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, {op_n})")
                lines.append(f"{pad}_sc{slot}_v[0] = (~_sc{slot}_v[0]) & (~_sc{slot}_m[0]) & 1ULL")
                if signed_override:
                    lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, "1", indent))
                else:
                    for wi in range(1, n_words):
                        lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                        lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                self._free_scratch(op_slot)
                return lines

            return None

        # ── BinaryOp ────────────────────────────────────────────────────────
        if et is BinaryOp:
            op = expr.op

            if op in self._WIDE_BINARY_PRIMS:
                prim = self._WIDE_BINARY_PRIMS[op]
                lslot = self._alloc_scratch()
                rslot = self._alloc_scratch()
                # Bitwise ops (&, |, ^, ~^, ^~) must see all bits of their
                # OPERANDS before combining -- computing at a narrower
                # enclosing context would silently drop upper operand bits
                # (mirrors `_expr_emitter.py`'s `_NATURAL_WIDTH_OPS`
                # handling). `+`/`-`/`*`/`/`/`%` ALL need the same widening
                # treatment, for two SEPARATE reasons depending on the
                # operator:
                #
                # `/`/`%` are not "residue-safe" for VALUE at all --
                # `(a/b) mod N != ((a mod N)/(b mod N)) mod N` in general,
                # so truncating the DIVIDEND to `dst_width` before dividing
                # changes the quotient whenever the dividend's own natural
                # width exceeds `dst_width`.
                #
                # `+`/`-`/`*` ARE residue-safe for VALUE (`(a+b) mod N ==
                # ((a mod N) + (b mod N)) mod N` holds unconditionally, and
                # likewise for `*`) -- but they are NOT safe for X-
                # PROPAGATION, a separate property this file's comments
                # previously conflated with value-safety. Each primitive's
                # `has_x` check (`wide_add`/`wide_sub`/`wide_mul`'s own
                # "any x bit ANYWHERE in either operand makes the ENTIRE
                # result x" rule) must see the FULL width of each operand
                # to correctly discover x bits that live ABOVE `dst_width`
                # -- an x bit that only exists in an operand's own upper,
                # value-truncated-away bits is INVISIBLE to a `has_x` check
                # that only ever received the low `dst_width` bits, even
                # though real Verilog's conservative x-propagation rule
                # doesn't care whether that x bit would have affected the
                # truncated VALUE or not. Confirmed against Icarus (cross-
                # engine) for `(0 * {3{{(ambiguous-condition ternary), (176
                # known bits)}}})` widened into a 96-bit destination: the
                # replication's own natural width (723 bits) puts its
                # ambiguous-ternary-derived x bits entirely above bit 176
                # of each 241-bit unit, which a 96-bit-truncated operand
                # computation never touches at all -- `has_x` saw a clean
                # (never-computed, so implicitly all-zero) upper region and
                # missed the x that a full-width computation would have
                # found, wrongly computing a definite `0` (via the OTHER
                # operand being definitely all-zero) instead of Icarus's
                # all-x. `(a wide, >dst_width concatenation) / (a6[44:0] |
                # 1)` is the original repro for the `/`/`%` value-safety
                # issue: the concatenation's own true width (202 bits)
                # exceeded the assignment's 96-bit context, and computing
                # it AT 96 bits before dividing discarded the high bits the
                # correct quotient depended on. Mirrors the shift left-
                # operand's identical `l_dst_width = max(lw, dst_width)`
                # widening a few lines below, for the same underlying
                # reason: a context-determined operator must WIDEN to fit
                # an operand that's naturally larger than the context,
                # never silently truncate it first.
                if op in _NATURAL_WIDTH_OPS:
                    op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
                elif op in ("+", "-", "*", "/", "%"):
                    op_width = max(dst_width, self._expr_width(expr.left), self._expr_width(expr.right))
                else:
                    op_width = dst_width
                # Division/modulus is NOT "residue-safe" like +/-/* (whose
                # modular arithmetic is invariant to whether each operand
                # was individually sign- or zero-extended, as long as each
                # operand's OWN bit pattern is correct at the target
                # width): `wide_div`/`wide_mod` are UNSIGNED-only bit-by-
                # bit primitives, so the operands fed into them must
                # already be uniformly reinterpreted as unsigned whenever
                # the operator's OWN combined signedness (both operands
                # signed) is false -- extending each operand by its own
                # individual signedness would let a signed operand's
                # sign-extension leak in as a huge unsigned magnitude. This
                # combined-signedness requirement is NOT unique to `/`/`%`
                # -- `+`/`-`/`*` need it too, for a DIFFERENT reason than
                # the op_width-truncation-safety comment above: that
                # comment is about safely TRUNCATING an already-correctly-
                # signed/zero-extended operand to a narrower `op_width`
                # (a genuinely truncation-invariant property of modular
                # arithmetic), not about which choice (sign- vs zero-
                # extend) correctly WIDENS a narrower operand into
                # `op_width` in the first place -- those are two different
                # questions, and conflating them (the "each operand's own
                # individual signedness is fine for +/-/*" reasoning this
                # codebase used to rely on here) is wrong: sign- vs zero-
                # extending a signed operand produces a DIFFERENT integer
                # value, and `(a - b) mod N` genuinely differs depending on
                # WHICH of those two different values `a` is taken to be.
                # `combined_override` is threaded into BOTH operands'
                # scratch computation, propagating into whatever nested
                # operator either one is (exactly like a ternary's combined
                # signedness overrides its branches), so this combined
                # decision -- not each operand's own type -- governs every
                # extension nested within either operand too. Mirrors the
                # identical fix in `sim/evaluator.py`/`_expr_emitter.py`;
                # confirmed wrong (cross-engine, against the reference
                # oracle) for `a3 % (a0 | 1))` (a0 a signed 1-bit register
                # nested inside the divisor's own `|`), and (cross-engine,
                # against Icarus) `(sa - ub)` with `sa` a signed 1-bit
                # register holding `1` (i.e. -1) and `ub` an unsigned 2-bit
                # `0`: Icarus gives `1` (zero-extending `sa` per the
                # pair's combined-unsigned type), not `-1`/`3`.
                # Bitwise ops (&,|,^,~^,^~) are excluded from the
                # `signed_override`-forwarding used below for +/-/*//: a
                # bitwise op's own combined signedness is entirely SELF-
                # CONTAINED (governed solely by its own two operands' types),
                # unlike +/-/*//, which genuinely need an outer decision
                # (e.g. a ternary branch, or a `%`'s divisor-widening
                # override) to reach in. Forwarding the incoming
                # `signed_override` here would leak an unrelated outer
                # decision into a NESTED, structurally-unrelated operand's
                # own independent signed/unsigned computation -- exactly the
                # bug shape already fixed in `sim/evaluator.py`'s mirror-
                # image branch (see its docstring for the concrete Icarus-
                # confirmed repro, `(|a3[45]) % (($signed(a4[23]) - a0) |
                # 1)`: the dividend's unsigned reduction forces `%`'s
                # combined decision unsigned, but forwarding that `False`
                # into the nested `-`'s own operands wrongly zero-extended
                # `a0` instead of sign-extending it). Passing `None` here
                # lets `expr.left`/`expr.right` each widen to `op_width`
                # using their OWN self-determined signedness (computed
                # internally by the recursive call itself, same as any
                # other unforced call) instead of the outer override; the
                # RESULT-level extension below (`if op_width < dst_width`)
                # still correctly consults `signed_override` for how the
                # bitwise op's OWN already-computed result should be read
                # by whatever outer context requested it.
                if op in ("&", "|", "^", "~^", "^~"):
                    combined_override = None
                elif signed_override is not None:
                    combined_override = signed_override
                else:
                    combined_override = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                llines = self._emit_wide_expr_to_scratch(
                    expr.left, lslot, n_words, op_width, indent, signed_override=combined_override
                )
                if llines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                rlines = self._emit_wide_expr_to_scratch(
                    expr.right, rslot, n_words, op_width, indent, signed_override=combined_override
                )
                if rlines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                lines = llines + rlines
                # `wide_div`/`wide_mod`'s `dst_width` parameter isn't just
                # output tail-masking (as it is for the bitwise/arithmetic
                # primitives, where `min(op_width, dst_width)` is safe to
                # truncate either way) -- it also bounds how many bits of
                # the DIVIDEND the restoring-division algorithm itself
                # iterates over (`for bit in range(dst_width - 1, -1,
                # -1)`), so capping it back down to `dst_width` here would
                # silently discard the same high dividend bits `op_width`
                # was just widened above specifically to keep. Use the
                # (possibly wider) `op_width` directly for `/`/`%` instead
                # of re-truncating to `dst_width` -- the eventual signal
                # store still only reads the destination's own true width
                # out of the scratch array, so any extra high words beyond
                # that are simply never read, no separate narrowing step
                # needed here.
                prim_width = op_width if op in ("/", "%") else min(op_width, dst_width)
                lines.append(
                    f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
                    f" _sc{lslot}_v, _sc{lslot}_m,"
                    f" _sc{rslot}_v, _sc{rslot}_m, {n_words}, {prim_width})"
                )
                # Bitwise-op result only naturally fills op_width bits; when
                # the enclosing context is wider, extend the RESULT itself
                # (sign- or zero-, per this BinaryOp's own effective
                # signedness) -- not each operand individually, since it's
                # the combined expression's signedness that governs here
                # (IEEE 1364-2005 SS5.5.2).
                if op_width < dst_width:
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                    if eff_signed:
                        lines.extend(
                            self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(op_width), indent)
                        )
                self._free_scratch(lslot, rslot)
                return lines

            if op in self._WIDE_SHIFT_PRIMS:
                prim = self._WIDE_SHIFT_PRIMS[op]
                lslot = self._alloc_scratch()
                lw = self._expr_width(expr.left)
                # Source may be wider than destination (e.g. 65-bit >> 4 into 33-bit dst).
                # Load enough words to capture the full source so the shift sees all bits.
                n_src = max(n_words, (lw + 63) // 64)
                # The left operand's OWN recursive computation must be
                # asked for at least `dst_width` bits, not just its own
                # narrower self-width `lw` -- a shift's left operand is
                # context-determined (IEEE 1364-2005 Table 5-22), and when
                # it's e.g. a `$signed(...)`-wrapped 1-bit value, capping
                # its recursive `dst_width` at 1 stops that wrapper's own
                # sign-extension from filling anywhere past bit 0 (the
                # rest of the scratch words end up zeroed instead of
                # correctly sign-extended), even though `n_src` above
                # already correctly sized the WORD COUNT for it. Confirmed
                # against Icarus for `$signed((!a6[52])) << (...)`.
                l_dst_width = max(lw, dst_width)
                # `>>` is ALWAYS a logical (unsigned/zero-fill) shift in
                # Verilog regardless of the left operand's own declared
                # signedness -- only `>>>` sign-extends. Before `l_dst_width`
                # could exceed `lw`, a signed left operand never needed
                # widening here at all (dst_width==lw), so this never
                # mattered; now that it can be asked to widen, an
                # unqualified recursive call would let a plain signed
                # Identifier fall back to ITS OWN declared signedness (via
                # `signed_override=None`) and get sign-extended -- wrong for
                # `>>` specifically. Force unsigned explicitly for `>>` only;
                # `<<`/`<<<`/`>>>` keep deferring to the operand's own
                # signedness (or an active override), matching Verilog and
                # the already-correct `$signed(...) << ...` case. Confirmed
                # against Icarus for `a2 >> (...)` with `a2` declared
                # `signed [15:0]` and a shift amount >= 16.
                # `>>` forcing unsigned is a property of how the shift reads
                # its ALREADY-COMPUTED left operand's bit pattern (no
                # sign-bit replication into vacated positions) -- it does
                # NOT mean every leaf reached within that operand must be
                # individually re-typed as unsigned. When `expr.left` is
                # itself a natural-width combining op (`&`/`|`/`^`/`~^`/
                # `^~`), that operand's OWN internal computation is a
                # self-contained Verilog sub-expression whose value is
                # fixed by each of ITS OWN operands' declared types,
                # entirely independent of whatever operator later consumes
                # the result -- forcing `False` here would incorrectly leak
                # into that nested op's OWN per-operand extension (its
                # `div_mod_override = signed_override` forwarding below),
                # corrupting its computed VALUE, not just how it's read
                # afterward. Only apply the force when `expr.left` actually
                # needs WIDENING for this shift's own context (`lw <
                # dst_width`) -- that's the one case an outer
                # unsigned-widening decision genuinely must reach this deep
                # (matches the already-confirmed `a2 >> ...` fix this
                # override exists for, where `a2` is a plain signed
                # Identifier). Confirmed against Icarus (cross-engine) for
                # `(a6 & a0) >> a7` with `a0` a signed 1-bit register: `a0`
                # must sign-extend to -1 (matching Verilog's `a6 & a0`,
                # independent of the `>>`), but the unconditional force
                # made `a0` zero-extend to +1 instead, giving `a6 & 1`
                # rather than `a6 & (-1) == a6`.
                if op == ">>" and lw >= dst_width:
                    l_signed_override = None
                else:
                    l_signed_override = False if op == ">>" else signed_override
                llines = self._emit_wide_expr_to_scratch(
                    expr.left, lslot, n_src, l_dst_width, indent, signed_override=l_signed_override
                )
                if llines is None:
                    self._free_scratch(lslot)
                    return None
                # The shift COUNT is self-determined (IEEE 1364-2005 Table
                # 5-22 / SS5.6): it must be evaluated at its OWN natural
                # width, never an outer context width -- requesting a
                # fixed 32 bits here (merely for the `<int>` cast's
                # convenience) previously let a context-determined
                # operator WITHIN the amount expression (e.g. `~` in
                # `~(cond ? a : b)`) wrongly treat 32 as its enclosing
                # context and widen its own operand to 32 bits before
                # complementing, corrupting the amount (`~0` at 32 bits =
                # 0xFFFFFFFF, not the correct 1-bit `~0 = 1`). Also always
                # interpreted as an unsigned magnitude regardless of its
                # own declared signedness -- `signed_override=False`
                # forces zero- rather than sign-extension for any
                # Identifier reached within it. Confirmed against Icarus
                # for `$unsigned(a1) << a0` (signedness) and
                # `$signed((!a6[52])) << (~(cond ? a4[13] : (~^a1[2])))`
                # (width).
                amount_w = self._shift_amount_width(expr.right)
                aslot: int | None = None
                if self._expr_uses_wide_signal(expr.right) or self._expr_max_internal_width(expr.right) > _WORD_BITS:
                    # The shift COUNT either touches a signal wider than 64
                    # bits somewhere in its own tree (e.g. a comparison
                    # against an 80-bit operand), or its own internal
                    # computation exceeds 64 bits even from narrow signals
                    # (e.g. a reduction over a >64-bit Replication of a
                    # narrow signal) -- the narrow/scalar emitter below
                    # represents every Identifier as a single `c.val[sid]`
                    # word and every intermediate as a single `long long`,
                    # silently truncating either way, so it must never be
                    # used here. Route through the wide scratch emitter
                    # instead (the same _WIDE_CMP_PRIMS/etc. primitives
                    # already used correctly elsewhere) and read back a
                    # scalar amount from the low word. Confirmed against
                    # Icarus for `a2 << {2{$signed((a4 <= a6))}}` with a6
                    # 80 bits.
                    amount_n = max(1, (amount_w + 63) // 64)
                    aslot = self._alloc_scratch()
                    alines = self._emit_wide_expr_to_scratch(
                        expr.right, aslot, amount_n, amount_w, indent, signed_override=False
                    )
                    if alines is None:
                        self._free_scratch(lslot, aslot)
                        return None
                    lines = llines + alines
                    # Reading only `_sc{aslot}_v[0]` (the LOW word) silently
                    # dropped the shift amount's own high word(s) whenever
                    # its true (multi-word) value genuinely needed them --
                    # e.g. a >64-bit Concatenation/Replication-built amount
                    # whose value is astronomically larger than the
                    # shifted operand's own width, but whose LOW 64 bits
                    # alone happen to look like a small, in-range count.
                    # The shift primitives below only need to know
                    # "definitely saturating" vs. "this exact small count"
                    # (they already saturate for any `amount >= 64`-ish
                    # value), so clamp to a large sentinel (guaranteed to
                    # trigger that saturation) whenever ANY high word is
                    # nonzero OR the low word alone exceeds `<int>`'s own
                    # range (a plain `<int>` cast of a `long long`/
                    # `unsigned long long` that overflows 32 bits is
                    # implementation-defined, not a safe truncation).
                    # Confirmed against Icarus (cross-engine) for
                    # `(a5 << {a1[2], (a1[5] ? a7 : a6), (~a6[1])})` with
                    # `a6` 80 bits (making the shift amount's own combined
                    # width 82 bits, needing 2 words): the true amount is
                    # vastly larger than `a5`'s own 65-bit width (should
                    # shift to all-zero), but reading only the low word
                    # gave a small, wrong, non-saturating count.
                    overflow_checks = [f"(_sc{aslot}_v[0] > <unsigned long long>0x7FFFFFFF)"]
                    overflow_checks += [f"(_sc{aslot}_v[{wi}] != 0)" for wi in range(1, amount_n)]
                    amount_expr = f"(0x7FFFFFFF if ({' or '.join(overflow_checks)}) else <int>(_sc{aslot}_v[0]))"
                    amount_mask_expr = " | ".join(f"_sc{aslot}_m[{wi}]" for wi in range(amount_n))
                else:
                    lines = llines
                    # Mask to `amount_w` bits before the `<int>` cast: a
                    # `$signed(...)`-wrapped shift count (or any other
                    # context-determined sub-expression whose narrow-
                    # emitter codegen unconditionally sign-extends the
                    # underlying native C register, e.g. `_emit_func_call`'s
                    # `$signed` branch, which fills bits beyond its own
                    # self-width with the sign bit regardless of whether
                    # the caller only wants those bits read as an unsigned
                    # magnitude) would otherwise leak that sign-fill
                    # straight into the `<int>` cast, turning a small
                    # positive shift amount into a large negative one --
                    # the shift primitives below then misinterpret a
                    # negative amount as "shift the other direction"
                    # instead of the IEEE 1364-2005 SS5.6 rule that a shift
                    # count is ALWAYS an unsigned magnitude regardless of
                    # its own signedness/casts. Confirmed wrong against
                    # Icarus for `{a1[0], (-a1)} >> $signed({3{a0}})`
                    # assigned into a >64-bit destination (routes through
                    # this wide path): `$signed({3{a0}})`'s raw 3-bit
                    # pattern (0b111 = 7 unsigned) was being read back as
                    # `-1`, and the wide shift primitive then shifted the
                    # dividend left instead of right.
                    # `<int>` is only a 32-bit register -- a masked
                    # `amount_w`-bit value (up to 64 bits wide here, since
                    # this is the narrow/non-wide-routed branch) can still
                    # exceed `INT_MAX` on its own, even from a single
                    # 64-bit word with no multi-word truncation involved;
                    # clamp the same way the wide-routed sibling branch
                    # above does, for the identical "definitely saturating"
                    # reason.
                    masked_amount = f"(({self._emit_expr(expr.right, amount_w, False)}) & wmask({amount_w}))"
                    amount_expr = (
                        f"(0x7FFFFFFF if (<unsigned long long>({masked_amount}) >"
                        f" <unsigned long long>0x7FFFFFFF) else <int>({masked_amount}))"
                    )
                    amount_mask_expr = self._emit_mask_expr(expr.right, amount_w)
                # An x/z shift COUNT makes the whole shift result x/z
                # (there's no way to know how many positions to shift) --
                # the shift primitives below only ever consult the
                # amount's VALUE, silently treating unknown-position bits
                # as a bogus concrete shift instead of propagating the
                # unknown-ness through. Confirmed against Icarus for
                # `a2 >> ((^(a1 ? a5[4:3] : a5)) + a3[25:16])` with a3
                # fully x.
                lines.append(f"{pad}if ({amount_mask_expr}):")
                for wi in range(n_words):
                    remaining = dst_width - wi * 64
                    lines.append(f"{pad}    _sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}    _sc{slot}_m[{wi}] = _word_mask64({remaining})")
                lines.append(f"{pad}else:")
                if op == ">>>":
                    lines.append(
                        f"{pad}    {prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{lslot}_v, _sc{lslot}_m,"
                        f" {amount_expr}, {n_src}, {lw}, {dst_width})"
                    )
                else:
                    lines.append(
                        f"{pad}    {prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{lslot}_v, _sc{lslot}_m,"
                        f" {amount_expr}, {n_src}, {dst_width})"
                    )
                if aslot is not None:
                    self._free_scratch(lslot, aslot)
                else:
                    self._free_scratch(lslot)
                return lines

            if op in self._WIDE_CMP_PRIMS:
                prim, swap = self._WIDE_CMP_PRIMS[op]
                # Use the signed comparison primitive whenever BOTH sides
                # are genuinely signed per IEEE 1364-2005 §5.5.1 (mirrors
                # `_expr_signed`'s general rule, used everywhere else in
                # this codebase) -- NOT only when both are literally a
                # syntactic `$signed(...)` call. A side can be signed
                # without being a bare `$signed(...)` node too, e.g. a
                # ternary whose OWN combined signedness is true (both
                # branches individually signed) already sign-extends its
                # selected branch internally when recursed into below, or
                # a plain Identifier declared `signed`. Using the UNSIGNED
                # primitive on operands that are actually meant to be
                # negative silently gives the wrong comparison result.
                # Confirmed against Icarus for `(a4[6] ? $signed(a4[52:24])
                # : (~a6)) < a4` where only the ternary's `true_expr` is a
                # literal `$signed(...)` call but its `false_expr` (`~a6`,
                # picked here) is signed via `a6`'s own declared
                # signedness, and `a4` (the RHS) is a plain signed
                # Identifier -- neither syntactically a `$signed(...)`
                # call, yet the comparison is fully signed.
                use_signed = (
                    op in ("<", "<=", ">", ">=") and self._expr_signed(expr.left) and self._expr_signed(expr.right)
                )
                # `use_signed` above is deliberately scoped to `< <= > >=`
                # only (equality doesn't need a signed vs. unsigned
                # PRIMITIVE -- bit-pattern equality is the same either
                # way) -- but OPERAND EXTENSION must still respect each
                # operand's combined signedness for `==`/`!=` too, exactly
                # like the relational ops: a narrower signed operand (e.g.
                # a 1-bit signed register) compared against a wider
                # operand must still be sign-extended to the shared
                # comparison width, or an x/z sign bit's ambiguity never
                # propagates into the newly-filled upper words, silently
                # corrupting `wide_cmp_eq`'s own "known bit differs"
                # precision check. Using `use_signed` (always False for
                # `==`/`!=`) as the extension override here was wrong.
                # Confirmed wrong (cross-engine, against the reference
                # oracle) for `(a0 == a6)` with `a0` a signed 1-bit
                # x-valued register and `a6` a large defined 80-bit
                # signed value.
                combined_signed = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                # Unwrapping `$signed(x)` down to `x` and recursing `x`
                # directly at the comparison's own (wider) `cmp_w`, forcing
                # `signed_override=combined_signed`, is only equivalent to
                # `$signed`'s real IEEE 1364-2005 Table 5-22 semantics
                # (self-determined: compute the ARGUMENT at its OWN natural
                # width, THEN sign/zero-extend that already-computed
                # result to the outer width) when `x` is a plain leaf read
                # (Identifier/Literal/BitSelect/RangeSelect/PartSelect) --
                # for those, there is no operator whose OWN result depends
                # on which width it computes at, so "read directly at the
                # wider width" and "read at own width then extend" produce
                # identical values. For a COMPOUND `x` involving a
                # context-determined operator (confirmed case: unary `-`),
                # the two orders genuinely differ: e.g. negating a
                # small UNSIGNED value AFTER zero-extending it to a much
                # wider width wraps around to a huge magnitude-near-2^cmp_w
                # result, whereas IEEE's actual self-determined-then-
                # extend order negates at the operand's own narrow width
                # first (a small wrapped result) and only THEN extends
                # it -- entirely different values. Restricting the unwrap
                # to leaf `x` here (falling through to the un-unwrapped
                # `$signed(...)` FunctionCall for anything else, which
                # already correctly implements the self-determined-then-
                # extend order in its own dedicated handler below) fixes
                # this without touching the leaf fast path the unwrap
                # exists for. Confirmed against Icarus (cross-engine) for
                # `($unsigned({a0, a6}) < $signed((-a4[9:5])))`: unwrapping
                # `$signed((-a4[9:5]))` and negating `a4[9:5]` directly at
                # the comparison's 81-bit combined width gave a huge
                # (~2^81) wraparound value instead of the correct small
                # negated-at-5-bits-then-extended one.
                #
                # A SEPARATE bug in the same unwrap, found immediately
                # after fixing the one above: even for a genuine leaf `x`,
                # forcing `signed_override=combined_signed` is only a
                # no-op (hence safe) when `combined_signed` already equals
                # `True` -- `$signed(x)`'s OWN forced-signed nature is an
                # explicit, unconditional override (that's the entire
                # point of the cast: to force sign-extension regardless of
                # what the comparison's own combined type would otherwise
                # decide for an un-cast operand), not something the
                # comparison's combined type gets to override. Requiring
                # `combined_signed` restores that: when the comparison's
                # own combined type is unsigned (not both operands
                # independently signed -- true whenever the OTHER operand
                # isn't signed, exactly the case a `$signed(...)` cast is
                # most often used to override), unwrapping would force the
                # cast's argument to zero-extend instead, silently
                # discarding the cast entirely. Falling through to the
                # un-unwrapped `$signed(...)` FunctionCall here still
                # correctly widens (its own dedicated handler always
                # sign-extends, unconditionally, per `$signed`'s real
                # semantics). Confirmed against Icarus (cross-engine) for
                # `((a5[4] ? a4 : a1[1]) != $signed(a5[27]))` widened to a
                # 96-bit destination: the ternary's combined type is
                # unsigned (its false branch, a1[1], is unsigned), and
                # `!=`'s own combined type is therefore also unsigned, so
                # unwrapping forced `a5[27]` to zero- instead of
                # sign-extend, even though `$signed(...)` explicitly
                # requires sign-extension regardless.
                _cmp_leaf_types = (Identifier, Literal, BitSelect, RangeSelect, PartSelect)
                left_is_signed_call = (
                    isinstance(expr.left, FunctionCall)
                    and expr.left.name.lower() == "$signed"
                    and len(expr.left.arguments) == 1
                    and isinstance(expr.left.arguments[0], _cmp_leaf_types)
                    and combined_signed
                )
                right_is_signed_call = (
                    isinstance(expr.right, FunctionCall)
                    and expr.right.name.lower() == "$signed"
                    and len(expr.right.arguments) == 1
                    and isinstance(expr.right.arguments[0], _cmp_leaf_types)
                    and combined_signed
                )
                left_expr = expr.left.arguments[0] if left_is_signed_call else expr.left
                right_expr = expr.right.arguments[0] if right_is_signed_call else expr.right
                lslot = self._alloc_scratch()
                rslot = self._alloc_scratch()
                lw = self._expr_width(left_expr)
                rw = self._expr_width(right_expr)
                # Operands are compared at max(lw, rw), which can be wider
                # than the (1-bit) comparison RESULT's own destination word
                # count -- recurse using n_operands (sized for the widest
                # OPERAND), not the outer n_words (sized for dst_width),
                # otherwise the upper words of a wide operand are left
                # unpopulated/garbage and the comparison reads past what was
                # actually computed.
                n_operands = (max(lw, rw) + 63) // 64
                cmp_w = max(lw, rw)
                # Thread `combined_signed` (the comparison's own COMBINED
                # signedness decision, IEEE 1364-2005 §5.5.2 -- computed
                # above, deliberately NOT `use_signed`, which is scoped to
                # relational-only) into both recursive calls -- not just
                # used to pick the comparison PRIMITIVE above -- so a
                # nested context-determined operator within either operand
                # (e.g. a unary `-`) sees this combined decision rather
                # than falling back to its own individual type. Mirrors
                # the identical fix in `_expr_emitter.py`/
                # `sim/evaluator.py`/`sim/vm/compiler.py`.
                #
                # `dst_width` (4th positional arg) must be `cmp_w` (=
                # `max(lw, rw)`, the comparison's own SHARED width), not
                # each operand's own self-width `lw`/`rw` -- an Identifier
                # operand narrower than the OTHER operand (e.g. a 1-bit
                # signed register compared against an 80-bit value) only
                # decides to sign-extend via `wide_load_signal_s` when its
                # OWN self-width is less than the requested `dst_width`;
                # passing its own self-width back as `dst_width` makes
                # that check always false, so it silently falls through to
                # the plain (zero-filling) `wide_load_signal` instead --
                # an x/z sign bit's ambiguity never propagates into the
                # extra (now stale-zero) words, corrupting the
                # comparison's own "known bit differs" precision check in
                # `wide_cmp_eq`/etc. `n_operands`-sized scratch already
                # anticipated this width; only the `dst_width` argument
                # itself was wrong. Confirmed wrong (cross-engine, against
                # the reference oracle) for `(a0 == a6)` with `a0` a
                # signed 1-bit x-valued register and `a6` a large defined
                # 80-bit value.
                llines = self._emit_wide_expr_to_scratch(
                    left_expr, lslot, n_operands, cmp_w, indent, signed_override=combined_signed
                )
                if llines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                rlines = self._emit_wide_expr_to_scratch(
                    right_expr, rslot, n_operands, cmp_w, indent, signed_override=combined_signed
                )
                if rlines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                lines = llines + rlines
                a_slot, b_slot = (rslot, lslot) if swap else (lslot, rslot)
                if use_signed:
                    signed_prim = prim + "_signed"
                    lines.append(
                        f"{pad}{signed_prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{a_slot}_v, _sc{a_slot}_m,"
                        f" _sc{b_slot}_v, _sc{b_slot}_m, {n_operands}, {cmp_w})"
                    )
                else:
                    lines.append(
                        f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{a_slot}_v, _sc{a_slot}_m,"
                        f" _sc{b_slot}_v, _sc{b_slot}_m, {n_operands})"
                    )
                for wi in range(1, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                self._free_scratch(lslot, rslot)
                return lines

            # ── Logical AND / OR ──────────────────────────────────────────────
            if op in {"&&", "||"}:
                lw = self._expr_width(expr.left)
                rw = self._expr_width(expr.right)
                ln = max(1, (lw + 63) // 64)
                rn = max(1, (rw + 63) // 64)
                lslot = self._alloc_scratch()
                rslot = self._alloc_scratch()
                bl_slot = self._alloc_scratch()
                br_slot = self._alloc_scratch()
                llines = self._emit_wide_expr_to_scratch(expr.left, lslot, ln, lw, indent)
                if llines is None:
                    self._free_scratch(lslot, rslot, bl_slot, br_slot)
                    return None
                rlines = self._emit_wide_expr_to_scratch(expr.right, rslot, rn, rw, indent)
                if rlines is None:
                    self._free_scratch(lslot, rslot, bl_slot, br_slot)
                    return None
                lines = llines + rlines
                lines.append(f"{pad}wide_reduce_or(_sc{bl_slot}_v, _sc{bl_slot}_m, _sc{lslot}_v, _sc{lslot}_m, {ln})")
                lines.append(f"{pad}wide_reduce_or(_sc{br_slot}_v, _sc{br_slot}_m, _sc{rslot}_v, _sc{rslot}_m, {rn})")
                # wide_reduce_or already gives each operand's truthiness
                # correctly (v=1,m=0 if any known-1 bit; v=0,m=1 if no
                # known-1 but some x/z; v=0,m=0 if exactly zero) -- a
                # known-nonzero operand forces || definitely true, and a
                # known-EXACTLY-zero (v=0,m=0) operand forces && definitely
                # false, regardless of unrelated x/z bits in the OTHER
                # operand (mirrors Value.logical_and/logical_or's precision
                # note in sim/value.py); the previous blanket
                # `_sc{slot}_m[0] = bl_m | br_m` missed this short-circuit.
                if op == "&&":
                    lines.append(
                        f"{pad}if (_sc{bl_slot}_m[0] == 0 and _sc{bl_slot}_v[0] == 0)"
                        f" or (_sc{br_slot}_m[0] == 0 and _sc{br_slot}_v[0] == 0):"
                    )
                    lines.append(f"{pad}    _sc{slot}_v[0] = 0")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 0")
                    lines.append(f"{pad}elif _sc{bl_slot}_v[0] != 0 and _sc{br_slot}_v[0] != 0:")
                    lines.append(f"{pad}    _sc{slot}_v[0] = 1")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 0")
                    lines.append(f"{pad}else:")
                    lines.append(f"{pad}    _sc{slot}_v[0] = 0")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 1")
                else:
                    lines.append(f"{pad}if _sc{bl_slot}_v[0] != 0 or _sc{br_slot}_v[0] != 0:")
                    lines.append(f"{pad}    _sc{slot}_v[0] = 1")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 0")
                    lines.append(f"{pad}elif _sc{bl_slot}_m[0] == 0 and _sc{br_slot}_m[0] == 0:")
                    lines.append(f"{pad}    _sc{slot}_v[0] = 0")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 0")
                    lines.append(f"{pad}else:")
                    lines.append(f"{pad}    _sc{slot}_v[0] = 0")
                    lines.append(f"{pad}    _sc{slot}_m[0] = 1")
                for wi in range(1, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                self._free_scratch(lslot, rslot, bl_slot, br_slot)
                return lines

            return None  # unhandled binary op

        # ── TernaryOp ───────────────────────────────────────────────────────
        # IEEE 1364-2005 §5.5.1: the ternary's OWN combined signedness
        # (signed only if BOTH branches are signed) governs sign- vs
        # zero-extension of whichever branch is selected -- not each
        # branch's own individual signedness. Evaluate each branch at the
        # FULL destination width with that decision forced via
        # `signed_override` (not each branch's own self-determined width
        # left to auto-decide), so a branch that is itself a
        # context-determined operator (UnaryOp ~/+/-, arithmetic BinaryOp,
        # a signed Identifier) gets its own operand(s) extended using the
        # override before the operator runs -- mirrors the identical fix
        # in `_emit_ternary_value_mask_exprs` (_expr_emitter.py), which
        # only covers the <=64-bit destination path; this is the same bug
        # in the separate wide (>64-bit destination) recursive emitter.
        if et is TernaryOp:
            # The condition is self-determined (IEEE 1364-2005 Table 5-22):
            # it must be evaluated at its OWN natural width, not forced down
            # to 1 bit -- a condition that is itself a further
            # TernaryOp/Concatenation/Replication uses the incoming width to
            # size its OWN internal merge/shift computation. Mirrors the
            # identical fix in the narrow emitter's
            # `_emit_ternary_value_mask_exprs` (_expr_emitter.py). Note this
            # scalar reduction is still fundamentally limited to 64 bits by
            # the `<unsigned long long>` cast below -- a condition wider
            # than 64 bits can only be evaluated this way, not fully
            # word-by-word (a documented residual gap, see
            # notes/known_issues.md).
            cond_w = self._expr_width(expr.condition)
            cond_mask_bits = f"wmask({min(cond_w, 64)})"
            # `_emit_expr`'s raw C `long long` result is only meaningful
            # within its own `cond_w` bits -- e.g. `$signed(1'b1)` emits
            # `_sign_ext(1, 1)`, which is -1 (ALL 64 bits set) as a raw C
            # value, matching the natural C representation of "signed
            # -1" but NOT scoped to cond_w. `wide_mux` (below) treats
            # `cond_v`/`cond_m` as its OWN "known-1 bit anywhere forces
            # true" check (`cond_v & ~cond_m`) -- without masking here
            # first, those spurious high bits (mask=0, so never excluded
            # by `~cond_m`) get read as a bogus "known-1", forcing a
            # branch to be selected outright instead of correctly falling
            # through to the ambiguous per-word merge. Confirmed against
            # Icarus for `-($signed((a7 == a2[4])) ? ... : ...)`.
            # A condition that reads a signal wider than 64 bits ANYWHERE
            # in its tree cannot go through the scalar path above at all:
            # `_emit_expr`'s Identifier case always returns `c.val[sid]`
            # regardless of the signal's true width, which for a >64-bit
            # signal only ever holds its LOW word -- and reduction helpers
            # like `_xor_reduce` then shift that same 64-bit value by
            # amounts >= 64 (undefined behavior, wraps on typical
            # platforms) when asked to reduce over the signal's full
            # (wider) width, silently corrupting the result instead of
            # just dropping high bits. Route these through wide scratch +
            # `wide_logical_truth` instead (already-correct: any known-1
            # bit anywhere -> true, else x if any x/z, else false),
            # reusing the same real word-by-word storage the rest of the
            # wide emitter already uses. Confirmed against Icarus for
            # `((^a3) - ({2{a5[21:12]}} & (~^a6))) ? a1 : ...` where `a6`
            # is 80 bits.
            # `_alloc_scratch`/`_free_scratch` is a plain LIFO stack-depth
            # counter (see codegen.py), not a real slot pool -- slots MUST
            # be freed in exact reverse-allocation order, or a later
            # `_alloc_scratch()` call hands out a number that's still
            # "live" (still holding data another in-flight computation
            # needs). `cond_slot`/`truth_slot` below therefore stay
            # allocated all the way through the `tslot`/`fslot` allocation
            # and the final `wide_mux` call (which reads `cond_v_expr`/
            # `cond_m_expr`, i.e. `truth_slot`'s data), and are freed
            # LAST, after `tslot`/`fslot` -- freeing them any earlier
            # (once briefly attempted here) let `tslot` collide with
            # `truth_slot`'s number, silently overwriting the
            # already-computed condition truthiness with the true
            # branch's own data before `wide_mux` read it.
            cond_lines: list[str]
            cond_slot: int | None = None
            truth_slot: int | None = None
            # `_expr_uses_wide_signal` alone misses a condition that's a
            # Concatenation/Replication of several individually-narrow
            # (<=64-bit) signals whose COMBINED width still exceeds 64 --
            # e.g. `{{2{a2[8:7]}}, (~a3[11]), {a3, a4[18]}}` with `a2`
            # 16 bits, `a3` 63 bits, `a4` 64 bits (none individually wide)
            # but a 69-bit combined width. `cond_w > _WORD_BITS` catches
            # that case directly; `_expr_max_internal_width` catches the
            # dual gap of an internal sub-computation exceeding 64 bits
            # even when the condition's own RESULT width doesn't (mirrors
            # the identical three-way check already used for statement
            # conditions in `_stmt_emitters.py`'s
            # `_emit_condition_lines_and_expr`). Without this, the
            # scalar/narrow path below silently truncates the condition
            # to its low 64 bits before ever checking truthiness -- e.g.
            # dropping a known-1 bit that lived in the truncated-away high
            # bits, which should have forced the condition definitely true
            # per the "known-1 bit anywhere" precision rule, but instead
            # got merged as if the condition were ambiguous. Confirmed
            # against Icarus (cross-engine) for `(a1[3] ^ ({{2{a2[8:7]}},
            # (~a3[11]), {a3, a4[18]}} ? a4[51:40] : $signed((a0 ? a2 :
            # a6[10]))))` with `a3` fully x: `a2[8:7]`'s own known-1 bit
            # (in the truncated-away high 5 bits) never got seen, so the
            # ternary's selected branch degraded from a clean, fully
            # defined value into a spuriously-ambiguous per-bit merge of
            # both branches.
            if (
                cond_w > _WORD_BITS
                or self._expr_uses_wide_signal(expr.condition)
                or self._expr_max_internal_width(expr.condition) > _WORD_BITS
            ):
                cond_n = max(1, (cond_w + 63) // 64)
                cond_slot = self._alloc_scratch()
                maybe_cond_lines = self._emit_wide_expr_to_scratch(expr.condition, cond_slot, cond_n, cond_w, indent)
                if maybe_cond_lines is None:
                    self._free_scratch(cond_slot)
                    return None
                cond_lines = maybe_cond_lines
                truth_slot = self._alloc_scratch()
                cond_lines.append(
                    f"{pad}wide_logical_truth(_sc{truth_slot}_v, _sc{truth_slot}_m,"
                    f" _sc{cond_slot}_v, _sc{cond_slot}_m, {cond_n})"
                )
                cond_v_expr = f"_sc{truth_slot}_v[0]"
                cond_m_expr = f"_sc{truth_slot}_m[0]"
            else:
                cond_lines = []
                cond_v_expr = f"<unsigned long long>(({self._emit_expr(expr.condition, cond_w)}) & {cond_mask_bits})"
                cond_m_expr = (
                    f"<unsigned long long>(({self._emit_mask_expr(expr.condition, cond_w)}) & {cond_mask_bits})"
                )
            tslot = self._alloc_scratch()
            fslot = self._alloc_scratch()
            own_signed = self._expr_signed(expr)
            # `own_signed` (the ternary's OWN combined branch signedness)
            # is meant to govern WIDENING a branch's independently-computed
            # value up to `dst_width` -- not to override how a
            # CONTEXT-DETERMINED arithmetic branch (`+`/`-`/`*`) types ITS
            # OWN operands. Those ops already extend directly to whatever
            # width they're asked for (`op_width == dst_width` here,
            # always -- unlike `&`/`|`/`^`, which have their own smaller
            # natural width and a genuinely separate later widening step),
            # so there is no legitimate "widen afterward" use for
            # `signed_override` to serve here at all -- forwarding it only
            # reaches down into `combined_override`/each operand's own
            # extension decision (mirrors `_emit_binary`'s identical
            # per-operand logic), silently overriding an operand's own
            # declared type. Division/modulus keep the ternary's override
            # (their dedicated `div_mod_override` computation is a
            # deliberate, separate, already-confirmed exception -- IEEE
            # 1364-2005 SS5.5.1 genuinely requires a division's ENTIRE
            # divisor sub-expression read uniformly per its own combined
            # decision). Confirmed against Icarus (cross-engine) for
            # `cond ? a5 : ({3{{a5, a7, a0}}} - a2)` with `a5` unsigned and
            # `a2` a signed identifier: the ternary's own combined type is
            # unsigned (replication is always unsigned, so not both
            # branches are signed), and forwarding that into the
            # subtraction forced `a2` to zero- instead of sign-extend,
            # even though IEEE governs `a2`'s OWN extension by its own
            # declared type here, independent of the ternary.
            t_signed_override = (
                None if isinstance(expr.true_expr, BinaryOp) and expr.true_expr.op in ("+", "-", "*") else own_signed
            )
            f_signed_override = (
                None if isinstance(expr.false_expr, BinaryOp) and expr.false_expr.op in ("+", "-", "*") else own_signed
            )
            tlines = self._emit_wide_expr_to_scratch(
                expr.true_expr, tslot, n_words, dst_width, indent, signed_override=t_signed_override
            )
            if tlines is None:
                self._free_scratch(tslot, fslot)
                if truth_slot is not None:
                    self._free_scratch(truth_slot, cond_slot)
                return None
            flines = self._emit_wide_expr_to_scratch(
                expr.false_expr, fslot, n_words, dst_width, indent, signed_override=f_signed_override
            )
            if flines is None:
                self._free_scratch(tslot, fslot)
                if truth_slot is not None:
                    self._free_scratch(truth_slot, cond_slot)
                return None
            lines = cond_lines + tlines + flines
            lines.append(
                f"{pad}wide_mux(_sc{slot}_v, _sc{slot}_m,"
                f" {cond_v_expr}, {cond_m_expr},"
                f" _sc{tslot}_v, _sc{tslot}_m,"
                f" _sc{fslot}_v, _sc{fslot}_m, {n_words}, {dst_width})"
            )
            self._free_scratch(tslot, fslot)
            if truth_slot is not None:
                self._free_scratch(truth_slot, cond_slot)
            return lines

        # ── RangeSelect ─────────────────────────────────────────────────────
        if et is RangeSelect:
            if not isinstance(expr.target, Identifier):
                return None
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                lsb_expr: str = str(int(expr.lsb.value))
                slice_w_expr: str = str(int(expr.msb.value) - int(expr.lsb.value) + 1)
                n_dst = (int(expr.msb.value) - int(expr.lsb.value) + 1 + 63) // 64
            else:
                msb_v = _const_int(expr.msb, self._param_env)
                lsb_v = _const_int(expr.lsb, self._param_env)
                if msb_v is not None and lsb_v is not None:
                    lsb_expr = str(lsb_v)
                    slice_w_expr = str(msb_v - lsb_v + 1)
                    n_dst = (msb_v - lsb_v + 1 + 63) // 64
                else:
                    # Dynamic bounds: use runtime expressions; n_dst = n_words (conservative)
                    lsb_c = self._emit_expr(expr.lsb, 32)
                    msb_c = self._emit_expr(expr.msb, 32)
                    lsb_expr = f"<int>({lsb_c})"
                    slice_w_expr = f"<int>(({msb_c}) - ({lsb_c}) + 1)"
                    n_dst = n_words  # conservative: wide_slice_extract zeros out-of-range words
            src_w = self._expr_width(expr.target)
            n_src = (src_w + 63) // 64
            tslot = self._alloc_scratch()
            # Load the target at its own `n_src` (word count sized for ITS
            # OWN true width), not the caller-supplied `n_words` (sized
            # for THIS RangeSelect's own, possibly narrower, result) --
            # `wide_slice_extract` below is called with `n_src` and reads
            # that many words back out of `_sc{tslot}`, so populating
            # fewer words than that leaves the extra high word(s)
            # uninitialized stack garbage whenever the slice's own bit
            # range crosses a 64-bit word boundary (e.g. `a6[66:41]` on an
            # 80-bit `a6`, spanning bit 64, inside an enclosing expression
            # whose own `n_words` is only 1). Mirrors the identical
            # "recurse at the operand's own required word count, not the
            # caller's" fix already applied to the wide shift left operand
            # and unary minus elsewhere in this file. Confirmed against
            # Icarus (cross-engine) for `(a1[7:2] ^ a6[66:41]) == {...}`
            # with `a6` fully x: the garbage-filled high word of the
            # loaded `a6` scratch buffer read as spuriously non-ambiguous,
            # corrupting the slice's mask and, downstream, the comparison
            # result -- but only when OTHER, unrelated code elsewhere in
            # the same function happened to leave different stale data in
            # that stack slot, making the bug appear to depend on sibling
            # statements that were never actually executed.
            lines = self._emit_wide_expr_to_scratch(expr.target, tslot, n_src, src_w, indent)
            if lines is None:
                self._free_scratch(tslot)
                return None
            lines.append(
                f"{pad}wide_slice_extract(_sc{slot}_v, _sc{slot}_m,"
                f" _sc{tslot}_v, _sc{tslot}_m, {lsb_expr}, {slice_w_expr}, {n_src}, {n_dst})"
            )
            # A range-select is always unsigned in its OWN right (IEEE
            # 1364-2005 SS5.5.1), so bits beyond its own width are
            # normally zero -- but when wrapped in `$signed(...)`
            # (signed_override True) and the destination is wider, those
            # bits must instead sign-extend from the slice's own top bit.
            # The fill must stop at dst_width, NOT n_words (the scratch
            # array's max size for the WHOLE statement) -- e.g. a
            # $signed()-wrapped concat member only needs its own few bits
            # filled, not the full array, otherwise it corrupts
            # neighboring concat members sharing the same scratch words.
            if signed_override:
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, slice_w_expr, indent))
            else:
                for wi in range(n_dst, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
            self._free_scratch(tslot)
            return lines

        # ── PartSelect (constant or dynamic base) ───────────────────────────
        if et is PartSelect:
            if not isinstance(expr.target, Identifier):
                return None
            tname = self._identifier_name(expr.target)
            width_v = _const_int(expr.width, self._param_env)
            if width_v is None:
                return None  # variable part-select width — extremely rare, not supported
            base_v = _const_int(expr.base, self._param_env)
            sig_base = self._signal_bases.get(tname, 0)
            if base_v is not None:
                # Static base — compile-time lsb
                if expr.direction == "+:":
                    lsb_expr = str(base_v - sig_base)
                else:
                    lsb_expr = str(base_v - width_v + 1 - sig_base)
            else:
                # Dynamic base — emit runtime lsb expression
                base_code = self._emit_expr(expr.base, 32)
                if expr.direction == "+:":
                    lsb_expr = f"<int>({base_code}) - {sig_base}" if sig_base else f"<int>({base_code})"
                else:
                    adj = width_v - 1 + sig_base
                    lsb_expr = f"<int>({base_code}) - {adj}" if adj else f"<int>({base_code})"
            src_w = self._expr_width(expr.target)
            n_src = (src_w + 63) // 64
            n_dst = (width_v + 63) // 64
            tslot = self._alloc_scratch()
            lines = self._emit_wide_expr_to_scratch(expr.target, tslot, n_words, src_w, indent)
            if lines is None:
                self._free_scratch(tslot)
                return None
            lines.append(
                f"{pad}wide_slice_extract(_sc{slot}_v, _sc{slot}_m,"
                f" _sc{tslot}_v, _sc{tslot}_m, {lsb_expr}, {width_v}, {n_src}, {n_dst})"
            )
            # Same signed_override reasoning as RangeSelect above.
            if signed_override:
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(width_v), indent))
            else:
                for wi in range(n_dst, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
            self._free_scratch(tslot)
            return lines

        # ── Concatenation ────────────────────────────────────────────────────
        # Verilog {a, b, c}: a=MSB, c=LSB; process reversed (LSB first).
        if et is Concatenation:
            lines = []
            # Zero the destination slot
            for wi in range(n_words):
                lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
            bit_offset = 0
            for part in reversed(expr.parts):
                pw = self._expr_width(part)
                pslot = self._alloc_scratch()
                plines = self._emit_wide_expr_to_scratch(part, pslot, n_words, pw, indent)
                if plines is None:
                    self._free_scratch(pslot)
                    return None
                lines += plines
                if bit_offset == 0:
                    # First (LSB) part — no shift; OR into zeroed slot
                    lines.append(
                        f"{pad}wide_or(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{slot}_v, _sc{slot}_m,"
                        f" _sc{pslot}_v, _sc{pslot}_m, {n_words}, {dst_width})"
                    )
                else:
                    # Shift part up by bit_offset, OR into slot
                    tmpslot = self._alloc_scratch()
                    lines.append(
                        f"{pad}wide_shl(_sc{tmpslot}_v, _sc{tmpslot}_m,"
                        f" _sc{pslot}_v, _sc{pslot}_m,"
                        f" {bit_offset}, {n_words}, {dst_width})"
                    )
                    lines.append(
                        f"{pad}wide_or(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{slot}_v, _sc{slot}_m,"
                        f" _sc{tmpslot}_v, _sc{tmpslot}_m, {n_words}, {dst_width})"
                    )
                    self._free_scratch(tmpslot)
                self._free_scratch(pslot)
                bit_offset += pw
            # A concatenation is always unsigned in its OWN right (IEEE
            # 1364-2005 SS5.5.1), so bits beyond its own total width
            # (bit_offset, after the loop) are normally zero -- but when
            # the WHOLE concatenation is wrapped in `$signed(...)`
            # (signed_override True) and the destination is wider, those
            # bits must instead sign-extend from the concat's own top bit.
            # Individual MEMBERS never see signed_override (concat members
            # are always self-determined, per the omitted keyword in the
            # recursive call above) -- this only concerns the concat's own
            # aggregate result.
            if signed_override and bit_offset < dst_width:
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(bit_offset), indent))
            return lines

        # ── Replication ──────────────────────────────────────────────────────
        # Replication is always unsigned in its OWN right (IEEE 1364-2005
        # §5.5.1): its natural width is exactly count*elem_width, and
        # wide_replicate leaves any bits above that zero. But when this node
        # is itself wrapped in `$signed(...)` (signed_override True) and the
        # destination context is wider than that natural width, those upper
        # bits must be sign-filled from the replicated value's own top bit
        # instead -- confirmed against Icarus (see notes/known_issues.md).
        if et is Replication:
            count = _const_int(expr.count, self._param_env)
            if count is None or count <= 0:
                return None
            elem_expr = self._normalize_replication_value(expr.value)
            elem_width = self._expr_width(elem_expr)
            rep_width = count * elem_width
            pslot = self._alloc_scratch()
            lines = self._emit_wide_expr_to_scratch(elem_expr, pslot, n_words, elem_width, indent)
            if lines is None:
                self._free_scratch(pslot)
                return None
            lines.append(
                f"{pad}wide_replicate(_sc{slot}_v, _sc{slot}_m,"
                f" _sc{pslot}_v, _sc{pslot}_m, {count}, {elem_width}, {n_words}, {dst_width})"
            )
            if signed_override and rep_width < dst_width:
                lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(rep_width), indent))
            self._free_scratch(pslot)
            return lines

        # ── FunctionCall ($signed/$unsigned force the extension mode for a
        # narrower Identifier operand -- otherwise transparent) ──
        if et is FunctionCall:
            fname = expr.name.lower()
            if fname in {"$signed", "$unsigned"} and len(expr.arguments) == 1:
                # $signed/$unsigned are themselves SELF-DETERMINED (IEEE
                # 1364-2005 Table 5-22): the argument must be computed at
                # its OWN self-determined width, not `dst_width` (the width
                # requested by whatever outer context-determined operator
                # is asking for this cast's value) -- the cast's job is
                # only to decide sign- vs zero-extension when the (already
                # self-width-computed) scratch contents are widened to
                # `dst_width` afterward. Forwarding `dst_width` straight
                # into the argument used to force a nested context-
                # determined operator inside the cast (e.g. `%`) to
                # propagate that OUTER width into ITS OWN operands too,
                # which is wrong. Mirrors the identical fix in
                # `sim/evaluator.py`/`sim/vm/compiler.py`; confirmed wrong
                # (cross-engine, against the reference oracle) for
                # `$signed((a3 % (a0 | 1))) + a1`.
                inner = expr.arguments[0]
                inner_w = self._expr_width(inner)
                lines = self._emit_wide_expr_to_scratch(inner, slot, n_words, inner_w, indent)
                if lines is None:
                    return None
                if dst_width > inner_w and fname == "$signed":
                    lines.extend(self._wide_sign_extend_to_dst_lines(slot, dst_width, n_words, str(inner_w), indent))
                return lines
            # User-defined function: `_user_func_XXX` always returns a
            # `long long` (at most 64 bits, by construction of its own
            # calling convention -- see `_gen_sections.py`'s
            # `_gen_user_functions`), so its result always fits in scratch
            # word 0 regardless of how wide `dst_width` (the OUTER
            # context) is. Previously this branch fell through to `return
            # None` -- the generic "not yet handled" signal callers use to
            # fall back to OTHER wide-RHS pattern matchers, all of which
            # ALSO don't understand FunctionCall, eventually reaching the
            # narrow-scalar last-resort fallback that's wrong for a
            # >64-bit destination (silently computing a value that never
            # accounted for this operand's own contribution at all).
            # Confirmed against Icarus (cross-engine, `vm`/`vm-fast`/
            # `reference` all already agreed) for `(~^$signed({a3,
            # fn_sub16s(a7, a1[6:4])}))`: the 79-bit-wide concat containing
            # a function call is an operand of a reduction, forcing this
            # wide path even though the FINAL destination (`y`) is only
            # 64 bits -- the missing case here silently dropped the
            # function call's contribution to the concat entirely.
            func = self._function_map.get(expr.name)
            if func is not None:
                call_expr = self._emit_user_func_call_expr(func, expr)
                ret_sid = self._signal_map.get(f"__func_{func.name}.{func.name}")
                if ret_sid is None:
                    return None
                ret_w = self._signal_widths[ret_sid]
                src_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                lines = [
                    f"{pad}_sc{slot}_v[0] = ({call_expr}) & wmask({ret_w})",
                    f"{pad}_sc{slot}_m[0] = (c.mask[{ret_sid}]) & wmask({ret_w})",
                ]
                for wi in range(1, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                if dst_width > ret_w and src_signed:
                    lines = lines[:2] + self._wide_sign_extend_to_dst_lines(
                        slot, dst_width, n_words, str(ret_w), indent
                    )
                elif dst_width < ret_w:
                    dst_n = (dst_width + 63) // 64
                    tail_bits = dst_width - (dst_n - 1) * 64
                    if tail_bits < _WORD_BITS:
                        lines.append(f"{pad}_sc{slot}_v[{dst_n - 1}] &= _word_mask64({tail_bits})")
                        lines.append(f"{pad}_sc{slot}_m[{dst_n - 1}] &= _word_mask64({tail_bits})")
                return lines
            return None

        return None

    def _expr_uses_wide_signal(self, expr: Expression) -> bool:
        """True if *expr* reads from any signal wider than _WORD_BITS.

        Used as a catch-all in _rhs_needs_wide_eval to detect cases where the
        scalar fallback would silently lose X bits (e.g. range-select from a
        wide signal, or concatenation of wide-signal slices).
        """
        et = type(expr)
        if et is Identifier:
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            return sid is not None and self._signal_widths[sid] > _WORD_BITS
        if et is Literal:
            return False
        if et is UnaryOp:
            return self._expr_uses_wide_signal(expr.operand)
        if et is BinaryOp:
            return self._expr_uses_wide_signal(expr.left) or self._expr_uses_wide_signal(expr.right)
        if et is TernaryOp:
            return (
                self._expr_uses_wide_signal(expr.condition)
                or self._expr_uses_wide_signal(expr.true_expr)
                or self._expr_uses_wide_signal(expr.false_expr)
            )
        if et is RangeSelect or et is PartSelect:
            return self._expr_uses_wide_signal(expr.target)
        if et is FunctionCall:
            return any(self._expr_uses_wide_signal(a) for a in expr.arguments)
        if et is Concatenation:
            return any(self._expr_uses_wide_signal(p) for p in expr.parts)
        if et is Replication:
            return self._expr_uses_wide_signal(expr.value)
        return False

    def _rhs_needs_wide_eval(self, rhs: Expression) -> bool:
        """True when a narrow LHS still requires wide-path evaluation.

        Covers cases like comparisons (==, <, >) and reductions where the
        operands are wide but the result is 1 bit.  Also covers shifts where
        the source operand is wide — the scalar path misses x-bit propagation
        across word boundaries.

        Delegates entirely to `_expr_max_internal_width` (already used for
        scratch-array sizing), which recurses into every node type -- this
        used to instead be a hand-maintained list of per-op special cases
        (comparisons via `_WIDE_CMP_PRIMS`, shifts, `_WIDE_BINARY_PRIMS`,
        reductions, `&&`/`||`, ternary branches) ending in a catch-all of
        `_expr_uses_wide_signal`, which only detects an individually wide
        *signal* reference anywhere in the tree -- never a *computed* wide
        value assembled from several individually-narrow signals or
        literals (a concatenation/replication whose own combined width
        exceeds 64 bits, or such a concatenation passed as a function-call
        argument). `_expr_max_internal_width` is a strict superset of every
        one of those per-op checks (each one only ever inspects `_expr_
        width` of an operand, which `_expr_max_internal_width` already
        folds into its own `max(...)` at every level of the recursion) and
        additionally has dedicated `Concatenation`/`Replication`/
        `FunctionCall`-argument cases the old per-op checks and `_expr_
        uses_wide_signal` both lacked. Confirmed against Icarus for a
        function-call argument built from several 8-bit signals
        concatenated together into a combined width over 64 bits (no
        individual signal itself wide): the old catch-all returned False,
        skipping wide-path evaluation entirely and silently truncating the
        argument. Being more inclusive than strictly necessary is safe --
        `_emit_wide_lhs_write_new`/`_emit_wide_expr_to_scratch` already
        return `None` (falling through to the narrow path, exactly as
        before) for any node shape the wide emitter doesn't yet support,
        so a wider net here can only ever additionally CORRECT a case that
        used to be silently wrong, never regress a case that used to work.
        """
        return self._expr_max_internal_width(rhs) > _WORD_BITS

    def _wide_sign_extend_to_dst_lines(
        self, slot: int, dst_width: int, n_words: int, src_width_expr: str, indent: int
    ) -> list[str]:
        """Sign-extend scratch slot *slot* to EXACTLY dst_width bits.

        `wide_sign_extend`'s own `n` parameter only understands whole
        words -- it always fills every bit through the end of word `n-1`,
        regardless of how many of those bits actually belong to dst_width.
        That's wrong whenever dst_width isn't a multiple of 64 (the common
        case for a small $signed()-wrapped concat member: e.g. dst_width=4
        needs NO extension at all when it equals the slice's own width,
        but wide_sign_extend would still smear the sign bit across the
        rest of word 0 -- corrupting neighboring concat members sharing
        that scratch word). An explicit tail mask after the call restricts
        the result to precisely dst_width bits either way.
        """
        pad = "    " * indent
        dst_n = (dst_width + 63) // 64
        lines = [f"{pad}wide_sign_extend(_sc{slot}_v, _sc{slot}_m, {dst_n}, {src_width_expr})"]
        tail_bits = dst_width - (dst_n - 1) * 64
        if tail_bits < 64:
            lines.append(f"{pad}_sc{slot}_v[{dst_n - 1}] &= _word_mask64({tail_bits})")
            lines.append(f"{pad}_sc{slot}_m[{dst_n - 1}] &= _word_mask64({tail_bits})")
        for wi in range(dst_n, n_words):
            lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
            lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
        return lines

    def _expr_max_internal_width(self, expr: Expression) -> int:
        """Maximum bit-width needed anywhere while evaluating *expr*.

        `_expr_width` only reports a node's own self-determined RESULT
        width -- e.g. always 1 for a comparison, regardless of how wide its
        operands are. Scratch-array sizing needs the true PEAK width used
        internally anywhere in the tree (a comparison between a 234-bit
        concatenation and a 1-bit reduction still needs 234-bit-wide
        scratch space to hold the concatenation operand), so this recurses
        into every operand rather than stopping at each node's own
        self-determined width.
        """
        etype = type(expr)
        own = self._expr_width(expr)
        if etype is BinaryOp:
            return max(
                own,
                self._expr_width(expr.left),
                self._expr_width(expr.right),
                self._expr_max_internal_width(expr.left),
                self._expr_max_internal_width(expr.right),
            )
        if etype is UnaryOp:
            return max(own, self._expr_width(expr.operand), self._expr_max_internal_width(expr.operand))
        if etype is TernaryOp:
            return max(
                own,
                self._expr_max_internal_width(expr.condition),
                self._expr_max_internal_width(expr.true_expr),
                self._expr_max_internal_width(expr.false_expr),
            )
        if etype is Concatenation:
            return max([own, *(self._expr_max_internal_width(p) for p in expr.parts)])
        if etype is Replication:
            return max(own, self._expr_max_internal_width(expr.value))
        if etype in (RangeSelect, PartSelect, BitSelect):
            return max(own, self._expr_max_internal_width(expr.target))
        if etype is FunctionCall:
            return max([own, *(self._expr_max_internal_width(a) for a in expr.arguments)])
        return own

    def _emit_wide_lhs_write_new(
        self,
        dst_sid: int,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str] | None:
        """Emit a wide assignment via the new recursive scratch-space emitter.

        Returns None if this assignment is not yet handled by the new path —
        the caller falls through to the existing wide pattern matchers.
        """
        lhs_w = self._signal_widths[dst_sid]
        if lhs_w <= _WORD_BITS and not self._rhs_needs_wide_eval(rhs):
            return None  # narrow dst — handled by existing path

        n_words = max(1, (lhs_w + _WORD_BITS - 1) // _WORD_BITS)
        max_expr_w = self._expr_max_internal_width(rhs)
        expr_words = (max_expr_w + _WORD_BITS - 1) // _WORD_BITS
        n_words = max(n_words, expr_words)
        n_words = max(n_words, self._module_max_wide_words())
        self._dynamic_max_wide_words = max(self._dynamic_max_wide_words, n_words)
        self._reset_scratch()
        slot = self._alloc_scratch()

        # This recursive scratch emitter can itself recurse into the
        # NARROW emitter (e.g. a `FunctionCall` argument -- each
        # argument is always computed via `_emit_expr`/`_emit_mask_expr`
        # regardless of whether the call's own destination is wide, see
        # `_emit_user_func_call_expr`), which may in turn need to hoist
        # a wide sub-computation of its OWN into a named `cdef` temp
        # (`_emit_wide_reduction_to_value`/`_emit_wide_truthy_to_value`
        # in `_expr_emitter.py`) -- those both require an active
        # `_et_pending` list to append to, and silently return None
        # (falling back to the native-`long long`-only narrow formula,
        # wrong beyond 64 bits) whenever `_et_pending` is None. Every
        # OTHER top-level-statement compiler that can reach the narrow
        # emitter (the continuous-assign and blocking/nonblocking-
        # assignment fallback paths, both in `_process_compiler.py`/
        # `_stmt_emitters.py`) already opens its own fresh `_et_pending`
        # scope before calling into it -- this recursive wide emitter
        # never did, since it does not itself need `_et_pending` (it
        # writes its own multi-line output directly into `lines`
        # instead), leaving `_et_pending` at whatever a PRIOR statement
        # last left it as (`None` by default, from `__init__`, if this
        # is the first statement compiled in the module). Confirmed
        # against Icarus for `fn_sel1({2{((~|a6[62]) && {a7, a6[5]})}},
        # (^(-a5)))` with `a5` 65 bits: the destination (`y0`, 64 bits)
        # is narrow, but `(^(-a5))`'s 65-bit internal computation routes
        # the WHOLE statement through this wide path (via
        # `_rhs_needs_wide_eval`) to compute the function call itself --
        # the reduction argument's own attempt to hoist through
        # `_emit_wide_reduction_to_value` then silently failed and fell
        # back to the narrow formula, discarding `a5`'s 65th bit.
        old_et_pending = self._et_pending
        old_et_count = self._et_count
        old_et_node_vals = self._et_node_vals
        old_et_node_masks = self._et_node_masks
        self._et_pending = []
        self._et_count = 0
        self._et_node_vals = {}
        self._et_node_masks = {}
        lines = self._emit_wide_expr_to_scratch(rhs, slot, n_words, lhs_w, indent)
        et_pending = self._et_pending
        self._et_pending = old_et_pending
        self._et_count = old_et_count
        self._et_node_vals = old_et_node_vals
        self._et_node_masks = old_et_node_masks
        if lines is None:
            self._reset_scratch()
            return None

        self._needs_wide_helpers = True

        pad = "    " * indent
        lines = [f"{pad}{t}" for t in et_pending] + lines
        if is_nba:
            lines.append(f"{pad}wide_stage_signal(c, {dst_sid}, _sc{slot}_v, _sc{slot}_m, {n_words})")
        else:
            lines.append(f"{pad}wide_store_signal(c, {dst_sid}, _sc{slot}_v, _sc{slot}_m, {n_words})")

        self._reset_scratch()
        return lines
