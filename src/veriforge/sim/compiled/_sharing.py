"""Opt-in sharing of compatible scalar process implementations."""

from __future__ import annotations

import hashlib
import io
import re
import tokenize

from veriforge._env import get_env

_ADDRESS = re.compile(r"\b(?:c\.[A-Za-z_]\w*|sv|sm)\[(\d+)\]")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\(([^()\n]*)\)")
_SIGNATURE = re.compile(r"^cdef\s+(?:inline\s+)?(?:[\w*]+\s+)+([A-Za-z_]\w*)\(([^()\n]*)\)[^\n]*:", re.M)
_EXCLUDED = ("wide_", "_mem_", "c.mem", "_user_", "_out_", "random", "_vf_ids")
_MAX_GROUPS = 1024
_MAX_BODY = 8192


def sharing_enabled() -> bool:
    value = (get_env("COMPILE_SHARE_PROCESSES", "0") or "0").strip().lower()
    if value not in {"0", "1", "false", "true"}:
        raise ValueError("VERIFORGE_COMPILE_SHARE_PROCESSES must be 0 or 1")
    return value in {"1", "true"}


# cm:e7a921
class ProcessSharing:
    """Share bounded scalar bodies while retaining per-process wrappers/state.

    Unrecognized address patterns remain literal parts of the specialization
    key. Unsupported bodies use the original generator unchanged.
    """

    def __init__(self, widths: list[int], helpers: str, *, signed: list[bool], constants: dict[int, int]):
        self.widths = widths
        self.signed = signed
        self.constants = constants
        self.groups: dict[str, tuple[str, int]] = {}
        self.initializers: list[tuple[str, str, int]] = []
        self.sid_arguments = {}
        for match in _SIGNATURE.finditer(helpers):
            parameters = match[2].split(",")
            names = [re.findall(r"[A-Za-z_]\w*", parameter)[-1] for parameter in parameters if parameter.strip()]
            if names and names[0] == "c":
                self.sid_arguments[match[1]] = [
                    i for i, name in enumerate(names) if re.search(r"(?:^|_)sid(?:_|$)", name)
                ]

    def _canonical(self, body: str) -> tuple[str, list[int]] | None:
        if len(body) > _MAX_BODY or any(text in body for text in _EXCLUDED):
            return None
        offsets = [0]
        for line in body.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        comments = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(body).readline):
                if token.type == tokenize.STRING:
                    return None
                if token.type == tokenize.COMMENT:
                    comments.append(
                        (offsets[token.start[0] - 1] + token.start[1], offsets[token.end[0] - 1] + token.end[1])
                    )
        except (tokenize.TokenError, IndentationError):
            return None

        def protected(start, end):
            return any(start < right and end > left for left, right in comments)

        edits = []
        for match in _ADDRESS.finditer(body):
            edits.append((match.start(1), match.end(1), int(match[1])))
        for match in _CALL.finditer(body):
            arguments = list(re.finditer(r"[^,]+", match[2]))
            if not arguments or arguments[0][0].strip() != "c":
                continue
            for index in self.sid_arguments.get(match[1], []):
                if index >= len(arguments):
                    continue
                argument = arguments[index]
                text = argument[0].strip()
                if text.isdecimal():
                    start = match.start(2) + argument.start() + len(argument[0]) - len(argument[0].lstrip())
                    edits.append((start, start + len(text), int(text)))
        edits = sorted(set(edits))
        addresses: dict[int, int] = {}
        parts = []
        cursor = 0
        for start, end, value in edits:
            if protected(start, end):
                continue
            if start < cursor or value >= len(self.widths) or self.widths[value] > 64:
                return None
            slot = addresses.setdefault(value, len(addresses))
            parts.extend((body[cursor:start], f"_vf_ids[{slot}]"))
            cursor = end
        if not addresses:
            return None
        parts.append(body[cursor:])
        return "".join(parts), list(addresses)

    def emit(self, parts: list[str], prefix: str, index: int, use_sv: bool) -> str:
        original = "\n".join(parts)
        body = "\n".join(parts[1:])
        canonical = self._canonical(body)
        if canonical is None:
            return original
        body, addresses = canonical
        specialization = body + repr(
            tuple((self.widths[value], self.signed[value], self.constants.get(value)) for value in addresses)
        )
        key = hashlib.sha256(specialization.encode("utf-8")).hexdigest()
        previous = self.groups.get(key)
        if previous is None and len(self.groups) >= _MAX_GROUPS:
            return original
        prelude = []
        if previous is None:
            group = len(self.groups)
            self.groups[key] = (specialization, group)
            initializer = f"_vf_init_shared_{prefix}_{index}"
            self.initializers.append((initializer, prefix, index))
            prelude = [
                f"cdef void _vf_shared_impl_{group}(SimCtx *c, const int *_vf_ids, long long *sv, long long *sm) noexcept nogil:",
                body,
                f"cdef void (*_vf_shared_fn_{group})(SimCtx *, const int *, long long *, long long *) noexcept nogil",
                f"cdef public void {initializer}() noexcept nogil:",
                f"    global _vf_shared_fn_{group}",
                f"    _vf_shared_fn_{group} = _vf_shared_impl_{group}",
            ]
        else:
            if previous[0] != specialization:
                return original
            group = previous[1]
        name = f"VF_IDS_{prefix}_{index}"
        values = ", ".join(map(str, addresses))
        prelude.extend(
            [
                "cdef extern from *:",
                '    """',
                f"    static const int {name}[{len(addresses)}] = {{{values}}};",
                '    """',
                f"    const int {name}[{len(addresses)}]",
                parts[0],
                f"    _vf_shared_fn_{group}(c, {name}, {'sv, sm' if use_sv else 'NULL, NULL'})",
                "",
            ]
        )
        return "\n".join(prelude)
