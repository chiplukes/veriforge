"""``$readmemh`` / ``$readmemb`` file loading (IEEE 1364-2005 17.2.8), shared by
the reference executor and the VM interpreter (compiled runs these tasks
through the reference executor).

Both used to parse the file themselves and both:

- raised ``ValueError`` on an x/z digit (``2x``, ``zz``) and on ``/* */``
  comments, and took ``//`` only at the start of a line;
- ignored the optional start/end address arguments
  (``$readmemh(f, m, 2, 5)`` loaded from slot 0, and ``start > end`` -- a
  downward load -- didn't exist);
- took ``@address`` lines as slot numbers, wrong for a memory not starting at
  address 0 (``reg m [1:4]``; see `ascending.lower_memory_bases`).

Addresses here are *declared* addresses. A memory's slot ``k`` holds address
``base + k``: `lower_memory_bases` rebases ``m [1:4]`` to slots 0..3 and
passes ``base`` (1) as an internal fifth argument of the task call, which
no source code can write (the standard takes at most four). Without it the
base is 0. Loading defaults to starting at the lowest address and going up,
whatever the declared direction (1364-2005; Icarus agrees).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

#: Index of the internal address-base argument added by `lower_memory_bases`.
BASE_ARG = 4

_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _tokens(text: str) -> Iterator[str]:
    yield from _COMMENTS.sub(" ", text).split()


def _word(token: str, is_hex: bool, width: int) -> tuple[int, int]:
    """(value, x/z mask) of one data word; x/z (and ``?``) digits set mask
    bits (a z reads as x: the simulator has one unknown state)."""
    digits = token.replace("_", "")
    bits = 4 if is_hex else 1
    val = mask = 0
    for ch in digits:
        val <<= bits
        mask <<= bits
        if ch in "xXzZ?":
            mask |= (1 << bits) - 1
            continue
        digit = int(ch, 16 if is_hex else 2)  # ValueError for a bad digit
        val |= digit
    full = (1 << width) - 1
    # A short word is zero-extended, x digits included (Icarus: `x` into
    # 8 bits is `0x`, not `xx`).
    return val & full, mask & full


# cm:f7bb56
def load(
    text: str,
    *,
    is_hex: bool,
    width: int,
    depth: int,
    base: int = 0,
    start: int | None = None,
    end: int | None = None,
    store: Callable[[int, int, int], None],
) -> None:
    """Parse a readmem file's *text* and ``store(slot, value, mask)`` each
    word. *start*/*end* are the task's optional address arguments."""
    low, high = base, base + depth - 1
    first = low if start is None else start
    last = high if end is None else end  # (a start alone loads up to the top)
    step = 1 if last >= first else -1
    addr = first
    for token in _tokens(text):
        if token.startswith("@"):
            addr = int(token[1:].replace("_", ""), 16)
            continue
        if (addr - last) * step > 0 or not low <= addr <= high:
            # Past the requested range (Icarus warns "too many words") or
            # outside the memory: the rest of the run is dropped.
            addr += step
            continue
        val, mask = _word(token, is_hex, width)
        store(addr - base, val, mask)
        addr += step
