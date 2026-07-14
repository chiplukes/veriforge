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
    _REDUCTION_OPS,
)
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

        phase = "stage" if is_nba else "assign"
        pad = "    " * indent
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
    ) -> list[str] | None:
        """Recursively evaluate *expr* into scratch slot *slot*.

        Emits Cython lines that write the result into ``_sc{slot}_v`` /
        ``_sc{slot}_m``.  Returns None if the expression type is not yet handled
        by the new emitter (caller falls back to existing pattern matchers).

        ``n_words``  — number of 64-bit words in each scratch array.
        ``dst_width`` — actual bit width of the result (for tail masking).
        """
        pad = "    " * indent
        et = type(expr)

        # ── Identifier ──────────────────────────────────────────────────────
        if et is Identifier:
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            if sid is not None:
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
            return lines

        # ── UnaryOp ─────────────────────────────────────────────────────────
        if et is UnaryOp:
            op = expr.op

            # Unary identity — evaluate operand directly into the destination slot
            if op == "+":
                return self._emit_wide_expr_to_scratch(expr.operand, slot, n_words, dst_width, indent)

            # Bitwise invert / negate — result has the same width as operand
            if op in {"~", "-"}:
                prim = "wide_not" if op == "~" else "wide_neg"
                op_slot = self._alloc_scratch()
                op_width = self._expr_width(expr.operand)
                lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, n_words, op_width, indent)
                if lines is None:
                    self._free_scratch(op_slot)
                    return None
                lines.append(
                    f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, {n_words}, {dst_width})"
                )
                self._free_scratch(op_slot)
                return lines

            # Reduction operators — 1-bit result in slot[0]; upper words zeroed
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
                lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, n_words, op_width, indent)
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
                for wi in range(1, n_words):
                    lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                    lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
                if op in {"~|", "~&", "~^", "^~"}:
                    lines.append(f"{pad}_sc{slot}_v[0] = (~_sc{slot}_v[0]) & (~_sc{slot}_m[0]) & 1ULL")
                self._free_scratch(op_slot)
                return lines

            # Logical NOT — equivalent to NOR reduction (~|): 1 if operand is all-zero
            if op == "!":
                op_slot = self._alloc_scratch()
                op_width = self._expr_width(expr.operand)
                op_n = (op_width + 63) // 64
                lines = self._emit_wide_expr_to_scratch(expr.operand, op_slot, op_n, op_width, indent)
                if lines is None:
                    self._free_scratch(op_slot)
                    return None
                lines.append(f"{pad}wide_reduce_or(_sc{slot}_v, _sc{slot}_m, _sc{op_slot}_v, _sc{op_slot}_m, {op_n})")
                lines.append(f"{pad}_sc{slot}_v[0] = (~_sc{slot}_v[0]) & (~_sc{slot}_m[0]) & 1ULL")
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
                lw = self._expr_width(expr.left)
                rw = self._expr_width(expr.right)
                llines = self._emit_wide_expr_to_scratch(expr.left, lslot, n_words, lw, indent)
                if llines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                rlines = self._emit_wide_expr_to_scratch(expr.right, rslot, n_words, rw, indent)
                if rlines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                lines = llines + rlines
                lines.append(
                    f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
                    f" _sc{lslot}_v, _sc{lslot}_m,"
                    f" _sc{rslot}_v, _sc{rslot}_m, {n_words}, {dst_width})"
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
                llines = self._emit_wide_expr_to_scratch(expr.left, lslot, n_src, lw, indent)
                if llines is None:
                    self._free_scratch(lslot)
                    return None
                amount_expr = f"<int>({self._emit_expr(expr.right, 32)})"
                lines = llines
                if op == ">>>":
                    lines.append(
                        f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{lslot}_v, _sc{lslot}_m,"
                        f" {amount_expr}, {n_src}, {lw}, {dst_width})"
                    )
                else:
                    lines.append(
                        f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
                        f" _sc{lslot}_v, _sc{lslot}_m,"
                        f" {amount_expr}, {n_src}, {dst_width})"
                    )
                self._free_scratch(lslot)
                return lines

            if op in self._WIDE_CMP_PRIMS:
                prim, swap = self._WIDE_CMP_PRIMS[op]
                # Detect $signed(a) <op> $signed(b) → use signed comparison primitive
                use_signed = (
                    op in ("<", "<=", ">", ">=")
                    and isinstance(expr.left, FunctionCall)
                    and expr.left.name.lower() == "$signed"
                    and len(expr.left.arguments) == 1
                    and isinstance(expr.right, FunctionCall)
                    and expr.right.name.lower() == "$signed"
                    and len(expr.right.arguments) == 1
                )
                left_expr = expr.left.arguments[0] if use_signed else expr.left
                right_expr = expr.right.arguments[0] if use_signed else expr.right
                lslot = self._alloc_scratch()
                rslot = self._alloc_scratch()
                lw = self._expr_width(left_expr)
                rw = self._expr_width(right_expr)
                n_operands = (max(lw, rw) + 63) // 64
                llines = self._emit_wide_expr_to_scratch(left_expr, lslot, n_words, lw, indent)
                if llines is None:
                    self._free_scratch(lslot, rslot)
                    return None
                rlines = self._emit_wide_expr_to_scratch(right_expr, rslot, n_words, rw, indent)
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
                        f" _sc{b_slot}_v, _sc{b_slot}_m, {n_operands}, {max(lw, rw)})"
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

            return None  # &&, ||, etc — not yet

        # ── TernaryOp ───────────────────────────────────────────────────────
        if et is TernaryOp:
            cond_v_expr = f"<unsigned long long>({self._emit_expr(expr.condition, 1)})"
            cond_m_expr = f"<unsigned long long>({self._emit_mask_expr(expr.condition, 1)})"
            tslot = self._alloc_scratch()
            fslot = self._alloc_scratch()
            tw = self._expr_width(expr.true_expr)
            fw = self._expr_width(expr.false_expr)
            tlines = self._emit_wide_expr_to_scratch(expr.true_expr, tslot, n_words, tw, indent)
            if tlines is None:
                self._free_scratch(tslot, fslot)
                return None
            flines = self._emit_wide_expr_to_scratch(expr.false_expr, fslot, n_words, fw, indent)
            if flines is None:
                self._free_scratch(tslot, fslot)
                return None
            lines = tlines + flines
            lines.append(
                f"{pad}wide_mux(_sc{slot}_v, _sc{slot}_m,"
                f" {cond_v_expr}, {cond_m_expr},"
                f" _sc{tslot}_v, _sc{tslot}_m,"
                f" _sc{fslot}_v, _sc{fslot}_m, {n_words}, {dst_width})"
            )
            self._free_scratch(tslot, fslot)
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
            lines = self._emit_wide_expr_to_scratch(expr.target, tslot, n_words, src_w, indent)
            if lines is None:
                self._free_scratch(tslot)
                return None
            lines.append(
                f"{pad}wide_slice_extract(_sc{slot}_v, _sc{slot}_m,"
                f" _sc{tslot}_v, _sc{tslot}_m, {lsb_expr}, {slice_w_expr}, {n_src}, {n_dst})"
            )
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
            return lines

        # ── Replication ──────────────────────────────────────────────────────
        if et is Replication:
            count = _const_int(expr.count, self._param_env)
            if count is None or count <= 0:
                return None
            elem_expr = self._normalize_replication_value(expr.value)
            elem_width = self._expr_width(elem_expr)
            pslot = self._alloc_scratch()
            lines = self._emit_wide_expr_to_scratch(elem_expr, pslot, n_words, elem_width, indent)
            if lines is None:
                self._free_scratch(pslot)
                return None
            lines.append(
                f"{pad}wide_replicate(_sc{slot}_v, _sc{slot}_m,"
                f" _sc{pslot}_v, _sc{pslot}_m, {count}, {elem_width}, {n_words}, {dst_width})"
            )
            self._free_scratch(pslot)
            return lines

        # ── Logical AND / OR ─────────────────────────────────────────────────
        # a && b  =  (|a) & (|b),   a || b  =  (|a) | (|b)
        # Result is 1-bit; both operands OR-reduced to booleans first.
        if et is BinaryOp and expr.op in {"&&", "||"}:
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
            if expr.op == "&&":
                lines.append(
                    f"{pad}_sc{slot}_v[0] = (_sc{bl_slot}_v[0] & _sc{br_slot}_v[0])"
                    f" & ~(_sc{bl_slot}_m[0] | _sc{br_slot}_m[0]) & 1ULL"
                )
            else:
                lines.append(
                    f"{pad}_sc{slot}_v[0] = (_sc{bl_slot}_v[0] | _sc{br_slot}_v[0])"
                    f" & ~(_sc{bl_slot}_m[0] | _sc{br_slot}_m[0]) & 1ULL"
                )
            lines.append(f"{pad}_sc{slot}_m[0] = _sc{bl_slot}_m[0] | _sc{br_slot}_m[0]")
            for wi in range(1, n_words):
                lines.append(f"{pad}_sc{slot}_v[{wi}] = 0")
                lines.append(f"{pad}_sc{slot}_m[{wi}] = 0")
            self._free_scratch(lslot, rslot, bl_slot, br_slot)
            return lines

        # ── FunctionCall ($signed/$unsigned are transparent in wide context) ──
        if et is FunctionCall:
            fname = expr.name.lower()
            if fname in {"$signed", "$unsigned"} and len(expr.arguments) == 1:
                return self._emit_wide_expr_to_scratch(
                    expr.arguments[0], slot, n_words, dst_width, indent
                )
            return None

        return None

    def _rhs_needs_wide_eval(self, rhs: Expression) -> bool:
        """True when a narrow LHS still requires wide-path evaluation.

        Covers cases like comparisons (==, <, >) and reductions where the
        operands are wide but the result is 1 bit.  Also covers shifts where
        the source operand is wide — the scalar path misses x-bit propagation
        across word boundaries.
        """
        if isinstance(rhs, BinaryOp):
            if rhs.op in self._WIDE_CMP_PRIMS:
                lw = self._expr_width(rhs.left)
                rw = self._expr_width(rhs.right)
                return max(lw, rw) > _WORD_BITS
            if rhs.op in {"<<", ">>", ">>>"}:
                if self._expr_width(rhs.left) > _WORD_BITS:
                    return True
                # Reduction of wide operand: (&wide) << N, (|wide) >> N, etc.
                if isinstance(rhs.left, UnaryOp) and rhs.left.op in _REDUCTION_OPS:
                    return self._expr_width(rhs.left.operand) > _WORD_BITS
        if isinstance(rhs, UnaryOp) and rhs.op in {"|", "&", "^", "~|", "~&", "~^", "^~", "!"}:
            return self._expr_width(rhs.operand) > _WORD_BITS
        if isinstance(rhs, BinaryOp) and rhs.op in {"&&", "||"}:
            return max(self._expr_width(rhs.left), self._expr_width(rhs.right)) > _WORD_BITS
        return False

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

        n_words = self._module_max_wide_words()
        self._reset_scratch()
        slot = self._alloc_scratch()

        lines = self._emit_wide_expr_to_scratch(rhs, slot, n_words, lhs_w, indent)
        if lines is None:
            self._reset_scratch()
            return None

        pad = "    " * indent
        if is_nba:
            lines.append(f"{pad}wide_stage_signal(c, {dst_sid}, _sc{slot}_v, _sc{slot}_m, {n_words})")
        else:
            lines.append(f"{pad}wide_store_signal(c, {dst_sid}, _sc{slot}_v, _sc{slot}_m, {n_words})")

        self._reset_scratch()
        return lines
