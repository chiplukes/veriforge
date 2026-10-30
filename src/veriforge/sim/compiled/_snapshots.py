"""Conservative dependencies for frozen scalar/wide signal reads."""

from __future__ import annotations

import io
import re
import tokenize

from veriforge._env import get_env


def snapshot_mode() -> str:
    mode = (get_env("COMPILE_SNAPSHOTS", "needed") or "needed").strip().lower()
    if mode not in {"all", "needed"}:
        raise ValueError("VERIFORGE_COMPILE_SNAPSHOTS must be 'all' or 'needed'")
    return mode


# Snapshot arguments and source SID position in the emitted helper signatures.
# New/unrecognized uses of sv/sm select the full-copy fallback automatically.
_CALLS = {
    "_sig_extract_word_val_sv": ((0, 1), 3),
    "_sig_extract_word_mask_sv": ((0,), 2),
    "_whole_stage_signal_sv": ((1, 2), 4),
    "_whole_stage_signal_s_sv": ((1, 2), 4),
    "_whole_stage_insert_signal_sv": ((1, 2), 5),
    "_whole_stage_insert_signal_slice_sv": ((1, 2), 5),
}


def _arguments(tokens, start):
    """Token-index ranges for one call, allowing nested expressions."""
    depth, first, arguments = 0, start + 1, []
    for index in range(start + 1, len(tokens)):
        text = tokens[index].string
        if text == ")" and depth == 0:
            arguments.append((first, index))
            return arguments
        if text == "," and depth == 0:
            arguments.append((first, index))
            first = index + 1
        elif text in {"(", "[", "{"}:
            depth += 1
        elif text in {")", "]", "}"}:
            depth -= 1
    return []


# cm:6d318e
class SnapshotReads:
    """Collect before process sharing, retaining full copies on uncertainty.

    Edge detection needs scalar slots independently of data reads. A wide edge
    alone needs only its mirrored low bit, not the wide backing storage.
    """

    def __init__(self, n_signals: int, edges: set[int]):
        self.n_signals = n_signals
        self.edges = edges
        self.data: set[int] = set()
        self.full = snapshot_mode() == "all"

    def record(self, lines: list[str]) -> None:
        if self.full:
            return
        try:
            tokens = [
                token
                for token in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline)
                if token.type not in {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT}
            ]
        except (tokenize.TokenError, IndentationError):
            self.full = True
            return
        uses = {i for i, token in enumerate(tokens) if token.type == tokenize.NAME and token.string in {"sv", "sm"}}
        covered = set()
        for index in uses:
            part = [token.string for token in tokens[index + 1 : index + 4]]
            if len(part) == 3 and part[0] == "[" and part[1].isdecimal() and part[2] == "]":
                self.data.add(int(part[1]))
                covered.add(index)
        for index, token in enumerate(tokens[:-1]):
            if token.type != tokenize.NAME or tokens[index + 1].string != "(":
                continue
            spec = _CALLS.get(token.string)
            if re.fullmatch(r"_wmem\d+_stage_insert_signal_slice_sv", token.string):
                spec = ((1, 2), 5)
            if spec is None:
                if token.string.endswith("_sv"):
                    self.full = True
                    return
                continue
            arguments = _arguments(tokens, index + 1)
            snapshot_args, sid_arg = spec
            if len(arguments) <= sid_arg:
                continue
            left, right = arguments[sid_arg]
            if right != left + 1 or not tokens[left].string.isdecimal():
                continue
            self.data.add(int(tokens[left].string))
            for argument in snapshot_args:
                left, right = arguments[argument]
                if right == left + 1 and left in uses:
                    covered.add(left)
        # Direct wide-array accesses or any unexpected snapshot ABI stay safe.
        self.full = uses != covered or any(token.string in {"wide_snap_val", "wide_snap_mask"} for token in tokens)
        self.full |= any(sid >= self.n_signals for sid in self.data)


def copy_spans(selected: set[int], total: int, *, full: bool) -> list[tuple[int, int]]:
    """Coalesce sparse copies; retain full memcpy for dense/fragmented sets."""
    if full or (total and len(selected) * 2 >= total):
        return [(0, total)] if total else []
    spans: list[tuple[int, int]] = []
    for index in sorted(selected):
        if spans and sum(spans[-1]) == index:
            start, count = spans[-1]
            spans[-1] = (start, count + 1)
        else:
            spans.append((index, 1))
    # Short, indirect memcpy pairs can cost more than copying the gaps. Budget
    # twelve extra slots per table run, calibrated against the fragmented
    # scaling control; truly sparse runs still avoid large unused regions.
    if len(spans) > 4 and len(selected) + 12 * len(spans) >= total:
        return [(0, total)]
    return spans
