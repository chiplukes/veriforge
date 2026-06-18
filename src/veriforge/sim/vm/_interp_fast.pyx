# cython: boundscheck=False, wraparound=False, cdivision=True, language_level=3
# cython: initializedcheck=False, nonecheck=False
"""Fast Cython bytecode interpreter for the VM simulation engine.

Operates on flat C arrays — no Python objects in the hot loop.
All 4-state logic is done inline with (val, mask, width) triples.
"""

from libc.stdlib cimport malloc, free, realloc
from libc.stdlib cimport rand as c_rand
from libc.string cimport memcpy

# ── Opcode constants (must match opcodes.py Op enum values) ──────────
# We duplicate them as C #defines so the switch compiles to a jump table.

DEF OP_LOAD_SIG       = 1
DEF OP_LOAD_CONST     = 2
DEF OP_STORE_SIG      = 3
DEF OP_NBA_SIG        = 4
DEF OP_STORE_BIT      = 5
DEF OP_NBA_BIT        = 6
DEF OP_STORE_RANGE    = 7
DEF OP_NBA_RANGE      = 8
DEF OP_RESIZE         = 9

DEF OP_ADD            = 10
DEF OP_SUB            = 11
DEF OP_MUL            = 12
DEF OP_DIV            = 13
DEF OP_MOD            = 14
DEF OP_POW            = 15

DEF OP_BIT_AND        = 16
DEF OP_BIT_OR         = 17
DEF OP_BIT_XOR        = 18
DEF OP_BIT_XNOR       = 19
DEF OP_BIT_NOT        = 20
DEF OP_SHL            = 21
DEF OP_SHR            = 22
DEF OP_ASHL           = 23
DEF OP_ASHR           = 24

DEF OP_CMP_EQ         = 25
DEF OP_CMP_NE         = 26
DEF OP_CMP_LT         = 27
DEF OP_CMP_LE         = 28
DEF OP_CMP_GT         = 29
DEF OP_CMP_GE         = 30
DEF OP_CMP_CASE_EQ    = 31
DEF OP_CMP_CASE_NE    = 32

DEF OP_LOG_AND        = 33
DEF OP_LOG_OR         = 34
DEF OP_LOG_NOT        = 35

DEF OP_NEG            = 36
DEF OP_UPLUS          = 37

DEF OP_RED_AND        = 38
DEF OP_RED_OR         = 39
DEF OP_RED_XOR        = 40
DEF OP_RED_NAND       = 41
DEF OP_RED_NOR        = 42
DEF OP_RED_XNOR       = 43

DEF OP_BIT_SELECT     = 44
DEF OP_RANGE_SELECT   = 45
DEF OP_PART_SEL_UP    = 46
DEF OP_PART_SEL_DOWN  = 47
DEF OP_CONCAT         = 48
DEF OP_REPLICATE      = 49

DEF OP_JUMP           = 50
DEF OP_JUMP_IF_ZERO   = 51
DEF OP_JUMP_IF_NONZERO = 52
DEF OP_DUP            = 53
DEF OP_POP            = 54
DEF OP_NOP            = 55

DEF OP_SYS_DISPLAY    = 56
DEF OP_SYS_FINISH     = 57
DEF OP_SYS_TIME       = 58

DEF OP_PROC_END       = 59

DEF OP_FUNC_CLOG2     = 60
DEF OP_CMP_CASEX      = 61
DEF OP_CMP_CASEZ      = 62

DEF OP_CMP_SLT        = 63
DEF OP_CMP_SLE        = 64
DEF OP_CMP_SGT        = 65
DEF OP_CMP_SGE        = 66

DEF OP_LOAD_MEM       = 67
DEF OP_STORE_MEM      = 68
DEF OP_NBA_MEM        = 69

DEF OP_STORE_MEM_RANGE = 70
DEF OP_NBA_MEM_RANGE   = 71

DEF OP_FUNC_RANDOM    = 72

DEF OP_SIGN_EXT       = 73

DEF OP_SYS_READMEM    = 74

DEF OP_TERNARY        = 75
DEF OP_SYS_MONITOR    = 76

DEF OP_SDIV           = 82
DEF OP_SMOD           = 83

DEF STACK_MAX         = 256
DEF NBA_MAX           = 1024
DEF NBA_MEM_MAX       = 64
DEF DISP_BUF_CAP      = 4096    # flat long long slots for display output

DEF WIDE_WORDS        = 6       # max 64-bit words per wide value (384-bit max)
DEF WIDE_NBA_MAX      = 64      # max wide NBA entries per delta cycle
DEF WIDE_PART_NBA_MAX = 64      # max wide partial (bit/range) NBA entries per delta cycle

# ── Wide signal support ──────────────────────────────────────────────
#
# Signals/constants with width > 64 are stored as a flat array of
# unsigned long long words (little-endian: word 0 = bits [63:0]).
# WideCtx bundles the pool pointers passed into _execute_core.

cdef struct WideCtx:
    # Signal wide-word pool (one entry per wide-signal word)
    unsigned long long *sig_val      # flat: sig_offset[sid] .. sig_offset[sid]+words-1
    unsigned long long *sig_mask
    int               *sig_offset    # [sig_count]: byte offset into pool; -1 = narrow

    # Constant wide-word pool
    unsigned long long *const_val
    unsigned long long *const_mask
    int               *const_offset  # [const_count]: byte offset into pool; -1 = narrow

    # Wide NBA output buffer (full-signal replace: OP_NBA_SIG on wide signals)
    int               *nba_sids      # [WIDE_NBA_MAX]
    unsigned long long *nba_val      # [WIDE_NBA_MAX * WIDE_WORDS]
    unsigned long long *nba_mask     # [WIDE_NBA_MAX * WIDE_WORDS]
    int               *nba_count     # pointer: current count
    int                nba_cap

    # Wide partial NBA buffer (read-modify-write at apply: OP_NBA_BIT/RANGE on wide signals)
    int               *nba_part_sids  # [WIDE_PART_NBA_MAX]
    int               *nba_part_lsb   # [WIDE_PART_NBA_MAX] starting bit position
    int               *nba_part_n     # [WIDE_PART_NBA_MAX] bit count
    unsigned long long *nba_part_val  # [WIDE_PART_NBA_MAX * WIDE_WORDS] value at aligned position
    unsigned long long *nba_part_mask # [WIDE_PART_NBA_MAX * WIDE_WORDS]
    int               *nba_part_count # pointer: current count
    int                nba_part_cap

# ── Wide helper: extract up to 64 bits from a multi-word value ──────

cdef inline long long _wide_extract(
    const unsigned long long *pool,
    int words,
    int lsb,
    int result_w,
) noexcept nogil:
    """Extract result_w bits starting at lsb from a WIDE_WORDS-word value."""
    cdef int word_lo = lsb >> 6
    cdef int bit_lo  = lsb & 63
    cdef unsigned long long lo, hi
    if word_lo >= words:
        return 0
    lo = pool[word_lo] >> bit_lo
    if bit_lo > 0 and word_lo + 1 < words:
        hi = pool[word_lo + 1] << (64 - bit_lo)
        lo |= hi
    if result_w < 64:
        lo &= (1ULL << result_w) - 1
    return <long long>lo

# ── Wide helper: copy words from pool-at-offset to local wide slot ───

cdef inline void _wide_load(
    unsigned long long *dst_v,
    unsigned long long *dst_m,
    const unsigned long long *pv,
    const unsigned long long *pm,
    int off, int words,
) noexcept nogil:
    cdef int j
    for j in range(words):
        dst_v[j] = pv[off + j]
        dst_m[j] = pm[off + j]
    for j in range(words, WIDE_WORDS):
        dst_v[j] = 0
        dst_m[j] = 0

# ── Wide helper: compare two WIDE_WORDS-word values ─────────────────

cdef inline bint _wide_eq(
    const unsigned long long *av,
    const unsigned long long *am,
    const unsigned long long *bv,
    const unsigned long long *bm,
    int words,
) noexcept nogil:
    cdef int j
    for j in range(words):
        if av[j] != bv[j] or am[j] != bm[j]:
            return 0
    return 1

# ── Inline helpers ───────────────────────────────────────────────────

cdef inline long long mask_for_width(int w) noexcept nogil:
    if w >= 64:
        return <long long>(-1)   # all bits set
    return (1LL << w) - 1

cdef inline int popcount64(unsigned long long x) noexcept nogil:
    """Count set bits using Kernighan's method — O(popcount) iterations."""
    cdef int c = 0
    while x:
        x &= x - 1  # Clear lowest set bit
        c += 1
    return c

# ── Stack entry: val / mask / width ──────────────────────────────────

cdef struct SVal:
    long long val
    long long mask
    int       width

# ── NBA queue entry ──────────────────────────────────────────────────

cdef struct NBAEntry:
    int       sig_id
    long long val
    long long mask
    long long range_mask  # 0 = full-signal replace; non-zero = bit-range mask

cdef struct NBAMemEntry:
    int       mem_id
    int       addr
    long long val
    long long mask

# ── Result struct returned to Python ─────────────────────────────────

cdef struct ExecResult:
    int       status         # 0 = ok, 1 = finish, 2 = error
    int       nba_count
    int       dirty_count


# ── Delta-loop context struct ────────────────────────────────────────

cdef struct DeltaCtx:
    # Signal storage
    long long *sig_val
    long long *sig_mask
    int       *sig_width
    int        sig_count
    # Constant pool
    long long *const_val
    long long *const_mask
    int       *const_width
    int        const_count
    # Programs (flattened)
    int       *all_ops
    int       *all_a1
    int       *prog_offset
    int       *prog_length
    # Sensitivity CSR: sig → proc indices
    int       *sens_offset
    int       *sens_procs
    # Process type flags
    char      *proc_is_combo
    char      *proc_is_seq
    # Continuous assigns
    int       *cont_indices
    int        cont_count
    int       *cont_sens_offset
    int       *cont_sens_sigs
    # Edge info CSR: proc → (sig_id, edge_type)
    int       *edge_offset
    int       *edge_sigs
    int       *edge_types
    # Snapshot for edge detection
    long long *snap_val
    long long *snap_mask
    # Per-timestep state
    char      *seq_fired
    # Working buffers
    char      *is_changed
    char      *is_work        # current-pass CA input sigs (zeroed between passes)
    int       *changed_buf
    char      *trig_flag
    int       *triggered_buf
    NBAEntry  *nba_buf
    int        nba_cap
    int       *dirty_buf
    int        dirty_cap
    int        delta_limit
    long long  sim_time
    # Memory NBA buffer
    NBAMemEntry *nba_mem_buf
    int        nba_mem_cap
    # Display output buffer (flat long long array)
    long long *disp_buf
    int       *disp_pos       # pointer to current write position
    int        disp_cap
    # Memory arrays (flat storage)
    long long *mem_val
    long long *mem_mask
    int       *mem_elem_width  # element width for each mem_id
    int       *mem_depth       # depth for each mem_id
    int       *mem_base        # base offset into mem_val/mem_mask for each mem_id
    int        mem_count        # number of memories
    WideCtx    wctx             # wide signal pool pointers (zero-init = no wide signals)


# ── Wide arithmetic helpers (require GIL; called via `with gil:`) ────

cdef void _wide_mul_py(
    unsigned long long *dst_v, unsigned long long *dst_m,
    unsigned long long *a_v, int a_width, int a_is_wide, long long a_narrow,
    unsigned long long *b_v, int b_width, int b_is_wide, long long b_narrow,
) noexcept:
    cdef object a_py, b_py, result_py
    cdef int wi
    if a_is_wide:
        a_py = <object>0
        for wi in range(WIDE_WORDS):
            a_py = a_py | ((<object>a_v[wi]) << (wi * 64))
        a_py = a_py & (((<object>1) << a_width) - 1)
    else:
        a_py = (<object>(<unsigned long long>a_narrow)) & (((<object>1) << a_width) - 1)
    if b_is_wide:
        b_py = <object>0
        for wi in range(WIDE_WORDS):
            b_py = b_py | ((<object>b_v[wi]) << (wi * 64))
        b_py = b_py & (((<object>1) << b_width) - 1)
    else:
        b_py = (<object>(<unsigned long long>b_narrow)) & (((<object>1) << b_width) - 1)
    result_py = a_py * b_py
    for wi in range(WIDE_WORDS):
        dst_v[wi] = <unsigned long long>((result_py >> (wi * 64)) & <object>0xFFFFFFFFFFFFFFFF)
        dst_m[wi] = 0


cdef void _wide_div_py(
    unsigned long long *dst_v, unsigned long long *dst_m,
    unsigned long long *a_v, int a_width, int a_is_wide, long long a_narrow,
    unsigned long long *b_v, int b_width, int b_is_wide, long long b_narrow,
) noexcept:
    cdef object a_py, b_py, result_py
    cdef int wi
    if a_is_wide:
        a_py = <object>0
        for wi in range(WIDE_WORDS):
            a_py = a_py | ((<object>a_v[wi]) << (wi * 64))
        a_py = a_py & (((<object>1) << a_width) - 1)
    else:
        a_py = (<object>(<unsigned long long>a_narrow)) & (((<object>1) << a_width) - 1)
    if b_is_wide:
        b_py = <object>0
        for wi in range(WIDE_WORDS):
            b_py = b_py | ((<object>b_v[wi]) << (wi * 64))
        b_py = b_py & (((<object>1) << b_width) - 1)
    else:
        b_py = (<object>(<unsigned long long>b_narrow)) & (((<object>1) << b_width) - 1)
    result_py = a_py // b_py
    for wi in range(WIDE_WORDS):
        dst_v[wi] = <unsigned long long>((result_py >> (wi * 64)) & <object>0xFFFFFFFFFFFFFFFF)
        dst_m[wi] = 0


cdef void _wide_mod_py(
    unsigned long long *dst_v, unsigned long long *dst_m,
    unsigned long long *a_v, int a_width, int a_is_wide, long long a_narrow,
    unsigned long long *b_v, int b_width, int b_is_wide, long long b_narrow,
) noexcept:
    cdef object a_py, b_py, result_py
    cdef int wi
    if a_is_wide:
        a_py = <object>0
        for wi in range(WIDE_WORDS):
            a_py = a_py | ((<object>a_v[wi]) << (wi * 64))
        a_py = a_py & (((<object>1) << a_width) - 1)
    else:
        a_py = (<object>(<unsigned long long>a_narrow)) & (((<object>1) << a_width) - 1)
    if b_is_wide:
        b_py = <object>0
        for wi in range(WIDE_WORDS):
            b_py = b_py | ((<object>b_v[wi]) << (wi * 64))
        b_py = b_py & (((<object>1) << b_width) - 1)
    else:
        b_py = (<object>(<unsigned long long>b_narrow)) & (((<object>1) << b_width) - 1)
    result_py = a_py % b_py
    for wi in range(WIDE_WORDS):
        dst_v[wi] = <unsigned long long>((result_py >> (wi * 64)) & <object>0xFFFFFFFFFFFFFFFF)
        dst_m[wi] = 0


cdef int _wide_signed_cmp_py(
    unsigned long long *a_v, int a_width, int a_is_wide, long long a_narrow,
    unsigned long long *b_v, int b_width, int b_is_wide, long long b_narrow,
    int op_code,
) noexcept:
    cdef object a_py, b_py
    cdef int wi
    if a_is_wide:
        a_py = <object>0
        for wi in range(WIDE_WORDS):
            a_py = a_py | ((<object>a_v[wi]) << (wi * 64))
        a_py = a_py & (((<object>1) << a_width) - 1)
        if a_py >> (a_width - 1):
            a_py = a_py - ((<object>1) << a_width)
    else:
        a_py = <object>a_narrow
        if a_width > 0 and a_width < 64 and (a_narrow >> (a_width - 1)) & 1:
            a_py = a_narrow - (<long long>1 << a_width)
    if b_is_wide:
        b_py = <object>0
        for wi in range(WIDE_WORDS):
            b_py = b_py | ((<object>b_v[wi]) << (wi * 64))
        b_py = b_py & (((<object>1) << b_width) - 1)
        if b_py >> (b_width - 1):
            b_py = b_py - ((<object>1) << b_width)
    else:
        b_py = <object>b_narrow
        if b_width > 0 and b_width < 64 and (b_narrow >> (b_width - 1)) & 1:
            b_py = b_narrow - (<long long>1 << b_width)
    if op_code == 63:
        return 1 if a_py < b_py else 0
    elif op_code == 64:
        return 1 if a_py <= b_py else 0
    elif op_code == 65:
        return 1 if a_py > b_py else 0
    else:
        return 1 if a_py >= b_py else 0


cdef void _wm_mask_to_width(unsigned long long *wm_base, int w) noexcept nogil:
    """Clear bits in wm_base[] that lie above bit position w-1."""
    cdef int wsp = w >> 6
    cdef int bit_in_word = w & 63
    cdef int wi
    if bit_in_word > 0 and wsp < WIDE_WORDS:
        wm_base[wsp] = (<unsigned long long>1 << bit_in_word) - 1
        for wi in range(wsp + 1, WIDE_WORDS):
            wm_base[wi] = 0
    else:
        for wi in range(wsp, WIDE_WORDS):
            wm_base[wi] = 0


# ── The core execution function ──────────────────────────────────────

cdef int _execute_core(
    # Program
    const int *prog_ops,       # opcode array
    const int *prog_a1,        # arg1 array
    int        prog_len,
    # Signal storage (in/out)
    long long *sig_val,
    long long *sig_mask,
    const int *sig_width,
    int        sig_count,
    # Constant pool
    const long long *const_val,
    const long long *const_mask,
    const int       *const_width,
    int        const_count,
    # NBA output buffer
    NBAEntry  *nba_buf,
    int       *nba_count,      # in: max, out: actual
    # Dirty set output (signal IDs changed by blocking assigns)
    int       *dirty_buf,
    int       *dirty_count,
    # Simulation time
    long long  sim_time,
    # Memory arrays (optional, NULL if no memories)
    long long *mem_val,
    long long *mem_mask,
    const int *mem_elem_width,
    const int *mem_depth,
    const int *mem_base,
    int        mem_count,
    # Memory NBA buffer (optional, NULL to apply immediately)
    NBAMemEntry *nba_mem_buf,
    int       *nba_mem_count,     # in: max, out: actual (NULL if nba_mem_buf is NULL)
    # Display output buffer (optional, NULL to discard)
    # Layout per event: [fmt_id, n_args, is_monitor, v0, m0, w0, v1, m1, w1, ...]
    long long *disp_buf,
    int       *disp_pos,          # in/out: current write position
    int        disp_cap,          # capacity of disp_buf
    # Wide signal context (NULL-safe: pass NULL if design has no wide signals)
    WideCtx   *wctx,
) noexcept nogil:
    """Execute bytecode. Returns 0=ok, 1=finish, 2=error."""

    cdef SVal stack[STACK_MAX]
    cdef int sp = 0                     # stack pointer (next free slot)

    # Wide stack: parallel arrays indexed by stack slot (0..STACK_MAX-1).
    # wflag[i]=1 means stack[i] holds a wide value; the words are in
    # wv[i*WIDE_WORDS .. (i+1)*WIDE_WORDS-1] (and wm for mask).
    cdef unsigned long long wv[STACK_MAX * WIDE_WORDS]
    cdef unsigned long long wm[STACK_MAX * WIDE_WORDS]
    cdef int wflag[STACK_MAX]
    cdef int wi     # loop index for wide word loops
    cdef unsigned long long wtmp

    cdef int pc = 0
    cdef int op, arg1
    cdef long long wmask

    # Temporaries
    cdef SVal a, b, t
    cdef long long new_val, new_mask
    cdef int w, i, n
    cdef long long result_val, result_mask
    cdef int result_width
    cdef int mid, marker_sid
    cdef int nba_idx = 0
    cdef int dirty_idx = 0
    cdef int nba_max = nba_count[0]
    cdef int dirty_max = dirty_count[0]
    cdef int nba_mem_idx = 0
    cdef int nba_mem_max = 0
    if nba_mem_count != NULL:
        nba_mem_max = nba_mem_count[0]
    cdef int disp_idx = 0
    cdef int arg2
    if disp_pos != NULL:
        disp_idx = disp_pos[0]

    # Scratch buffers for wide concat accumulation (fixed allocation, not stack-relative)
    cdef unsigned long long concat_rv[WIDE_WORDS]
    cdef unsigned long long concat_rm[WIDE_WORDS]
    cdef int concat_rw    # accumulated bit width
    cdef int n_bits, bit_off, wrd_off, bit_in_word, bits_here  # local temps for shift math


    # Wide stack: all slots start as narrow
    cdef int wsp   # reusable wide-slot index
    for wsp in range(STACK_MAX):
        wflag[wsp] = 0

    cdef int wide_nba_idx = 0
    cdef int wide_nba_max = 0
    cdef int wide_part_nba_idx = 0
    cdef int wide_part_nba_max = 0
    cdef int a_wide, b_wide, a_any_x, b_any_x, b_is_zero
    cdef int has_x, red_parity, all_ones, any_one
    cdef unsigned long long av_w, am_w, bv_w, bm_w, agree_word
    if wctx != NULL:
        wide_nba_max = wctx.nba_cap
        wide_nba_idx = wctx.nba_count[0]
        wide_part_nba_max = wctx.nba_part_cap
        if wctx.nba_part_count != NULL:
            wide_part_nba_idx = wctx.nba_part_count[0]

    # Dirty tracking with a small bitset (supports up to sig_count signals)
    # We use dirty_buf as a flat list and just track count.

    while pc < prog_len:
        op = prog_ops[pc]
        arg1 = prog_a1[pc]
        pc += 1

        # ── Data movement ────────────────────────────────────────

        if op == OP_LOAD_SIG:
            stack[sp].width = sig_width[arg1]
            if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                # Wide signal: copy words to wide stack slot
                wflag[sp] = 1
                _wide_load(
                    &wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS],
                    wctx.sig_val, wctx.sig_mask,
                    wctx.sig_offset[arg1], WIDE_WORDS,
                )
                stack[sp].val  = 0
                stack[sp].mask = 0
            else:
                wflag[sp] = 0
                stack[sp].val  = sig_val[arg1]
                stack[sp].mask = sig_mask[arg1]
            sp += 1
            continue

        if op == OP_LOAD_CONST:
            stack[sp].width = const_width[arg1]
            if wctx != NULL and wctx.const_offset[arg1] >= 0:
                wflag[sp] = 1
                _wide_load(
                    &wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS],
                    wctx.const_val, wctx.const_mask,
                    wctx.const_offset[arg1], WIDE_WORDS,
                )
                stack[sp].val  = 0
                stack[sp].mask = 0
            else:
                wflag[sp] = 0
                stack[sp].val  = const_val[arg1]
                stack[sp].mask = const_mask[arg1]
            sp += 1
            continue

        if op == OP_STORE_SIG:
            sp -= 1
            if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                # Wide signal store
                wsp = wctx.sig_offset[arg1]
                if wflag[sp]:
                    # Wide value on stack — only mark dirty if value actually changed
                    bit_off = 0
                    for wi in range(WIDE_WORDS):
                        wtmp  = wv[sp * WIDE_WORDS + wi] & ~wm[sp * WIDE_WORDS + wi]
                        if wctx.sig_val[wsp + wi] != wtmp or wctx.sig_mask[wsp + wi] != wm[sp * WIDE_WORDS + wi]:
                            bit_off = 1
                        wctx.sig_val[wsp + wi]  = wtmp
                        wctx.sig_mask[wsp + wi] = wm[sp * WIDE_WORDS + wi]
                    if bit_off and dirty_idx < dirty_max:
                        dirty_buf[dirty_idx] = arg1
                        dirty_idx += 1
                else:
                    # Narrow value promoted to wide pool (zero upper words)
                    wmask = mask_for_width(sig_width[arg1])
                    wtmp  = <unsigned long long>(stack[sp].val & wmask & ~stack[sp].mask)
                    new_mask = stack[sp].mask & wmask
                    bit_off = (wctx.sig_val[wsp] != wtmp or wctx.sig_mask[wsp] != <unsigned long long>new_mask)
                    wctx.sig_val[wsp]  = wtmp
                    wctx.sig_mask[wsp] = <unsigned long long>new_mask
                    for wi in range(1, WIDE_WORDS):
                        if wctx.sig_val[wsp + wi] != 0 or wctx.sig_mask[wsp + wi] != 0:
                            bit_off = 1
                        wctx.sig_val[wsp + wi]  = 0
                        wctx.sig_mask[wsp + wi] = 0
                    if bit_off and dirty_idx < dirty_max:
                        dirty_buf[dirty_idx] = arg1
                        dirty_idx += 1
            else:
                a = stack[sp]
                w = sig_width[arg1]
                wmask = mask_for_width(w)
                new_val = a.val & wmask & ~a.mask
                new_mask = a.mask & wmask
                if sig_val[arg1] != new_val or sig_mask[arg1] != new_mask:
                    sig_val[arg1] = new_val
                    sig_mask[arg1] = new_mask
                    if dirty_idx < dirty_max:
                        dirty_buf[dirty_idx] = arg1
                        dirty_idx += 1
            continue

        if op == OP_NBA_SIG:
            sp -= 1
            if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                # Wide signal NBA
                if wide_nba_idx < wide_nba_max:
                    wctx.nba_sids[wide_nba_idx] = arg1
                    if wflag[sp]:
                        for wi in range(WIDE_WORDS):
                            wctx.nba_val[wide_nba_idx * WIDE_WORDS + wi]  = wv[sp * WIDE_WORDS + wi] & ~wm[sp * WIDE_WORDS + wi]
                            wctx.nba_mask[wide_nba_idx * WIDE_WORDS + wi] = wm[sp * WIDE_WORDS + wi]
                    else:
                        wmask = mask_for_width(sig_width[arg1])
                        wctx.nba_val[wide_nba_idx * WIDE_WORDS]  = stack[sp].val & wmask & ~stack[sp].mask
                        wctx.nba_mask[wide_nba_idx * WIDE_WORDS] = stack[sp].mask & wmask
                        for wi in range(1, WIDE_WORDS):
                            wctx.nba_val[wide_nba_idx * WIDE_WORDS + wi]  = 0
                            wctx.nba_mask[wide_nba_idx * WIDE_WORDS + wi] = 0
                    wide_nba_idx += 1
                    wctx.nba_count[0] = wide_nba_idx
                # If overflow, silently drop (same policy as narrow NBA overflow avoidance)
            else:
                a = stack[sp]
                w = sig_width[arg1]
                wmask = mask_for_width(w)
                if nba_idx < nba_max:
                    nba_buf[nba_idx].sig_id     = arg1
                    nba_buf[nba_idx].val        = a.val & wmask & ~a.mask
                    nba_buf[nba_idx].mask       = a.mask & wmask
                    nba_buf[nba_idx].range_mask = 0
                    nba_idx += 1
                else:
                    # NBA buffer overflow — signal data loss
                    nba_count[0] = nba_idx
                    dirty_count[0] = dirty_idx
                    if nba_mem_count != NULL:
                        nba_mem_count[0] = nba_mem_idx
                    if disp_pos != NULL:
                        disp_pos[0] = disp_idx
                    return 2
            continue

        if op == OP_RESIZE:
            # arg1 = new width
            if arg1 > 64:
                # Result is wide: promote narrow to wide or re-mask wide
                if wflag[sp - 1]:
                    # Already wide: mask to new width (clear bits above arg1)
                    wsp = (arg1 - 1) >> 6  # index of top word
                    if wsp < WIDE_WORDS:
                        wtmp = mask_for_width(arg1 & 63) if (arg1 & 63) != 0 else <unsigned long long>(-1)
                        wv[(sp - 1) * WIDE_WORDS + wsp] &= wtmp
                        wm[(sp - 1) * WIDE_WORDS + wsp] &= wtmp
                        for wi in range(wsp + 1, WIDE_WORDS):
                            wv[(sp - 1) * WIDE_WORDS + wi] = 0
                            wm[(sp - 1) * WIDE_WORDS + wi] = 0
                else:
                    # Narrow to wide: zero-extend
                    a = stack[sp - 1]
                    wflag[sp - 1] = 1
                    wv[(sp - 1) * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[(sp - 1) * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS):
                        wv[(sp - 1) * WIDE_WORDS + wi] = 0
                        wm[(sp - 1) * WIDE_WORDS + wi] = 0
                stack[sp - 1].width = arg1
            else:
                # Result is narrow
                if wflag[sp - 1]:
                    # Wide to narrow: extract low arg1 bits
                    wmask = mask_for_width(arg1)
                    stack[sp - 1].val  = <long long>(_wide_extract(&wv[(sp - 1) * WIDE_WORDS], WIDE_WORDS, 0, arg1))
                    stack[sp - 1].mask = <long long>(_wide_extract(&wm[(sp - 1) * WIDE_WORDS], WIDE_WORDS, 0, arg1))
                    stack[sp - 1].val  &= wmask & ~stack[sp - 1].mask
                    stack[sp - 1].mask &= wmask
                    wflag[sp - 1] = 0
                else:
                    a = stack[sp - 1]
                    wmask = mask_for_width(arg1)
                    stack[sp - 1].val   = a.val & wmask & ~a.mask
                    stack[sp - 1].mask  = a.mask & wmask
                stack[sp - 1].width = arg1
            continue

        if op == OP_SIGN_EXT:
            # Sign-extend TOS from a.width to arg1 bits
            a = stack[sp - 1]
            w = a.width
            if arg1 <= w:
                wmask = mask_for_width(arg1)
                stack[sp - 1].val   = a.val & wmask & ~a.mask
                stack[sp - 1].mask  = a.mask & wmask
                stack[sp - 1].width = arg1
            elif a.mask == 0:
                if w > 0 and (a.val >> (w - 1)) & 1:
                    # MSB is 1: fill bits [w, arg1-1] with 1
                    wmask = mask_for_width(arg1) ^ mask_for_width(w)
                    stack[sp - 1].val  = a.val | wmask
                else:
                    stack[sp - 1].val  = a.val  # MSB=0: upper bits remain 0
                stack[sp - 1].mask  = 0
                stack[sp - 1].width = arg1
            else:
                # Has X bits: extended bits become X (sign bit unknown)
                wmask = mask_for_width(arg1) ^ mask_for_width(w)
                stack[sp - 1].val   = a.val
                stack[sp - 1].mask  = a.mask | wmask
                stack[sp - 1].width = arg1
            continue

        if op == OP_STORE_BIT:
            sp -= 1
            b = stack[sp]      # index
            sp -= 1
            a = stack[sp]      # value (1-bit)
            if b.mask == 0:
                i = <int>b.val
                w = sig_width[arg1]
                if 0 <= i < w:
                    if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                        # Wide signal: update the appropriate word
                        wsp = wctx.sig_offset[arg1]
                        wi = i >> 6          # word index
                        n_bits = i & 63      # bit within word
                        if wi < WIDE_WORDS:
                            wtmp = wctx.sig_val[wsp + wi]           # save old val
                            result_mask = wctx.sig_mask[wsp + wi]   # save old mask
                            wctx.sig_val[wsp + wi]  = (wtmp & ~(1ULL << n_bits)) | ((<unsigned long long>(a.val & 1)) << n_bits)
                            wctx.sig_mask[wsp + wi] = <unsigned long long>result_mask & ~(1ULL << n_bits)
                            if wctx.sig_val[wsp + wi] != wtmp or wctx.sig_mask[wsp + wi] != <unsigned long long>result_mask:
                                if dirty_idx < dirty_max:
                                    dirty_buf[dirty_idx] = arg1
                                    dirty_idx += 1
                    else:
                        new_val = sig_val[arg1]
                        new_mask = sig_mask[arg1]
                        # Clear x/z for this bit
                        new_mask = new_mask & ~(1LL << i)
                        if a.val & 1:
                            new_val = new_val | (1LL << i)
                        else:
                            new_val = new_val & ~(1LL << i)
                        if sig_val[arg1] != new_val or sig_mask[arg1] != new_mask:
                            sig_val[arg1] = new_val
                            sig_mask[arg1] = new_mask
                            if dirty_idx < dirty_max:
                                dirty_buf[dirty_idx] = arg1
                                dirty_idx += 1
            continue

        if op == OP_NBA_BIT:
            sp -= 1
            b = stack[sp]      # index
            sp -= 1
            a = stack[sp]      # value
            if b.mask == 0:
                i = <int>b.val
                w = sig_width[arg1]
                if 0 <= i < w:
                    if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                        # Wide signal: queue partial NBA for read-modify-write at apply time
                        if wide_part_nba_idx < wide_part_nba_max and wctx.nba_part_count != NULL:
                            wsp = wide_part_nba_idx
                            wctx.nba_part_sids[wsp] = arg1
                            wctx.nba_part_lsb[wsp] = i
                            wctx.nba_part_n[wsp] = 1
                            wi = i >> 6
                            n_bits = i & 63
                            for bit_off in range(WIDE_WORDS):
                                wctx.nba_part_val[wsp * WIDE_WORDS + bit_off]  = 0
                                wctx.nba_part_mask[wsp * WIDE_WORDS + bit_off] = 0
                            wctx.nba_part_val[wsp * WIDE_WORDS + wi]  = (<unsigned long long>(a.val  & 1)) << n_bits
                            wctx.nba_part_mask[wsp * WIDE_WORDS + wi] = (<unsigned long long>(a.mask & 1)) << n_bits
                            wide_part_nba_idx += 1
                            wctx.nba_part_count[0] = wide_part_nba_idx
                    else:
                        new_val  = (a.val & 1) << i     # partial: bit i only
                        new_mask = (a.mask & 1) << i
                        if nba_idx < nba_max:
                            nba_buf[nba_idx].sig_id     = arg1
                            nba_buf[nba_idx].val        = new_val
                            nba_buf[nba_idx].mask       = new_mask
                            nba_buf[nba_idx].range_mask = 1LL << i
                            nba_idx += 1
                        else:
                            nba_count[0] = nba_idx
                            dirty_count[0] = dirty_idx
                            if nba_mem_count != NULL:
                                nba_mem_count[0] = nba_mem_idx
                            if disp_pos != NULL:
                                disp_pos[0] = disp_idx
                            return 2
            continue

        if op == OP_STORE_RANGE:
            sp -= 1
            b = stack[sp]      # lsb
            sp -= 1
            t = stack[sp]      # msb
            sp -= 1
            a = stack[sp]      # value
            a_wide = wflag[sp]
            if t.mask == 0 and b.mask == 0:
                w = sig_width[arg1]
                i = <int>b.val  # lsb
                n = <int>(t.val - b.val + 1)  # width of range
                if n > 0:
                    if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                        # Wide signal: update word-by-word
                        wsp = wctx.sig_offset[arg1]
                        bit_off = 0  # tracks whether any word changed
                        for wi in range(WIDE_WORDS):
                            wrd_off = wi * 64
                            if i + n <= wrd_off:
                                break
                            if i >= wrd_off + 64:
                                continue
                            # n_bits: signed offset of range start from word start
                            # positive → range starts inside this word at bit n_bits
                            # negative → range started in an earlier word; src_skip = -n_bits
                            n_bits = i - wrd_off
                            bit_in_word = n_bits if n_bits >= 0 else 0
                            n_bits = 0 if n_bits >= 0 else -n_bits   # reused as src_skip
                            bits_here = min(n - n_bits, 64 - bit_in_word)
                            if bits_here <= 0:
                                continue
                            wmask = mask_for_width(bits_here)
                            if a_wide:
                                new_val  = <long long>_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, n_bits, bits_here)
                                new_mask = <long long>_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, n_bits, bits_here)
                            else:
                                new_val  = (a.val  >> n_bits) & wmask
                                new_mask = (a.mask >> n_bits) & wmask
                            wtmp = wctx.sig_val[wsp + wi]
                            wctx.sig_val[wsp + wi] = (
                                (wtmp & ~(<unsigned long long>wmask << bit_in_word))
                                | ((<unsigned long long>new_val & <unsigned long long>wmask) << bit_in_word)
                            )
                            if wctx.sig_val[wsp + wi] != wtmp:
                                bit_off = 1
                            wtmp = wctx.sig_mask[wsp + wi]
                            wctx.sig_mask[wsp + wi] = (
                                (wtmp & ~(<unsigned long long>wmask << bit_in_word))
                                | ((<unsigned long long>new_mask & <unsigned long long>wmask) << bit_in_word)
                            )
                            if wctx.sig_mask[wsp + wi] != wtmp:
                                bit_off = 1
                        if bit_off != 0 and dirty_idx < dirty_max:
                            dirty_buf[dirty_idx] = arg1
                            dirty_idx += 1
                    else:
                        wmask = mask_for_width(n)
                        if a_wide:
                            new_val  = <long long>wv[sp * WIDE_WORDS]
                            new_mask = <long long>wm[sp * WIDE_WORDS]
                        else:
                            new_val  = a.val
                            new_mask = a.mask
                        new_val  = (sig_val[arg1]  & ~(wmask << i)) | ((new_val  & wmask) << i)
                        new_mask = (sig_mask[arg1] & ~(wmask << i)) | ((new_mask & wmask) << i)
                        if sig_val[arg1] != new_val or sig_mask[arg1] != new_mask:
                            sig_val[arg1]  = new_val
                            sig_mask[arg1] = new_mask
                            if dirty_idx < dirty_max:
                                dirty_buf[dirty_idx] = arg1
                                dirty_idx += 1
            continue

        if op == OP_NBA_RANGE:
            sp -= 1
            b = stack[sp]      # lsb
            sp -= 1
            t = stack[sp]      # msb
            sp -= 1
            a = stack[sp]      # value
            a_wide = wflag[sp]
            if t.mask == 0 and b.mask == 0:
                i = <int>b.val         # lsb
                n = <int>(t.val - b.val + 1)  # width of range
                if n > 0:
                    if wctx != NULL and wctx.sig_offset[arg1] >= 0:
                        # Wide signal: queue partial NBA for read-modify-write at apply time
                        if wide_part_nba_idx < wide_part_nba_max and wctx.nba_part_count != NULL:
                            wsp = wide_part_nba_idx
                            wctx.nba_part_sids[wsp] = arg1
                            wctx.nba_part_lsb[wsp] = i
                            wctx.nba_part_n[wsp] = n
                            for wi in range(WIDE_WORDS):
                                wctx.nba_part_val[wsp * WIDE_WORDS + wi]  = 0
                                wctx.nba_part_mask[wsp * WIDE_WORDS + wi] = 0
                            for wi in range(WIDE_WORDS):
                                wrd_off = wi * 64
                                if i + n <= wrd_off:
                                    break
                                if i >= wrd_off + 64:
                                    continue
                                n_bits = i - wrd_off
                                bit_in_word = n_bits if n_bits >= 0 else 0
                                n_bits = 0 if n_bits >= 0 else -n_bits
                                bits_here = min(n - n_bits, 64 - bit_in_word)
                                if bits_here <= 0:
                                    continue
                                wmask = mask_for_width(bits_here)
                                if a_wide:
                                    new_val  = <long long>_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, n_bits, bits_here)
                                    new_mask = <long long>_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, n_bits, bits_here)
                                else:
                                    new_val  = (a.val  >> n_bits) & wmask
                                    new_mask = (a.mask >> n_bits) & wmask
                                wctx.nba_part_val[wsp * WIDE_WORDS + wi]  = (<unsigned long long>new_val & <unsigned long long>wmask) << bit_in_word
                                wctx.nba_part_mask[wsp * WIDE_WORDS + wi] = (<unsigned long long>new_mask & <unsigned long long>wmask) << bit_in_word
                            wide_part_nba_idx += 1
                            wctx.nba_part_count[0] = wide_part_nba_idx
                    else:
                        wmask = mask_for_width(n)
                        if a_wide:
                            new_val  = <long long>wv[sp * WIDE_WORDS]
                            new_mask = <long long>wm[sp * WIDE_WORDS]
                        else:
                            new_val  = a.val
                            new_mask = a.mask
                        new_val  = (new_val  & wmask) << i    # partial: range bits only
                        new_mask = (new_mask & wmask) << i
                        if nba_idx < nba_max:
                            nba_buf[nba_idx].sig_id     = arg1
                            nba_buf[nba_idx].val        = new_val
                            nba_buf[nba_idx].mask       = new_mask
                            nba_buf[nba_idx].range_mask = wmask << i
                            nba_idx += 1
                        else:
                            nba_count[0] = nba_idx
                            dirty_count[0] = dirty_idx
                            if nba_mem_count != NULL:
                                nba_mem_count[0] = nba_mem_idx
                            if disp_pos != NULL:
                                disp_pos[0] = disp_idx
                            return 2
            continue

        # ── Arithmetic ───────────────────────────────────────────

        if op == OP_ADD:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                # Check for X bits: narrow mask fields OR wide mask words
                wsp = 0  # reuse as has_x flag
                if a.mask or b.mask:
                    wsp = 1
                else:
                    if wflag[sp]:
                        for wi in range(WIDE_WORDS):
                            if wm[sp * WIDE_WORDS + wi]:
                                wsp = 1; break
                    if wsp == 0 and wflag[sp + 1]:
                        for wi in range(WIDE_WORDS):
                            if wm[(sp + 1) * WIDE_WORDS + wi]:
                                wsp = 1; break
                if wsp:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    if not wflag[sp + 1]:
                        wv[(sp + 1) * WIDE_WORDS] = <unsigned long long>b.val
                        for wi in range(1, WIDE_WORDS):
                            wv[(sp + 1) * WIDE_WORDS + wi] = 0
                    for wi in range(WIDE_WORDS):
                        concat_rv[wi] = wv[sp * WIDE_WORDS + wi]
                    wtmp = 0
                    for wi in range(WIDE_WORDS):
                        concat_rm[wi] = concat_rv[wi] + wv[(sp + 1) * WIDE_WORDS + wi]
                        wv[sp * WIDE_WORDS + wi] = concat_rm[wi] + wtmp
                        wtmp = (<unsigned long long>(concat_rm[wi] < concat_rv[wi]) |
                                <unsigned long long>(wv[sp * WIDE_WORDS + wi] < concat_rm[wi]))
                    # Mask result to w bits
                    wsp = w >> 6; bit_in_word = w & 63
                    if bit_in_word > 0 and wsp < WIDE_WORDS:
                        wv[sp * WIDE_WORDS + wsp] &= (<unsigned long long>1 << bit_in_word) - 1
                        for wi in range(wsp + 1, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    else:
                        for wi in range(wsp, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    for wi in range(WIDE_WORDS):
                        wm[sp * WIDE_WORDS + wi] = 0
                    wflag[sp] = 1; stack[sp].val = <long long>wv[sp * WIDE_WORDS]; stack[sp].mask = 0
                stack[sp].width = w
            else:
                if a.mask or b.mask:
                    stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
                else:
                    stack[sp].val = (a.val + b.val) & wmask
                    stack[sp].mask = 0
                    stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_SUB:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                # Check for X bits: narrow mask fields OR wide mask words
                wsp = 0  # reuse as has_x flag
                if a.mask or b.mask:
                    wsp = 1
                else:
                    if wflag[sp]:
                        for wi in range(WIDE_WORDS):
                            if wm[sp * WIDE_WORDS + wi]:
                                wsp = 1; break
                    if wsp == 0 and wflag[sp + 1]:
                        for wi in range(WIDE_WORDS):
                            if wm[(sp + 1) * WIDE_WORDS + wi]:
                                wsp = 1; break
                if wsp:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    if not wflag[sp + 1]:
                        wv[(sp + 1) * WIDE_WORDS] = <unsigned long long>b.val
                        for wi in range(1, WIDE_WORDS):
                            wv[(sp + 1) * WIDE_WORDS + wi] = 0
                    for wi in range(WIDE_WORDS):
                        concat_rv[wi] = wv[sp * WIDE_WORDS + wi]
                    wtmp = 0  # borrow
                    for wi in range(WIDE_WORDS):
                        concat_rm[wi] = concat_rv[wi] - wv[(sp + 1) * WIDE_WORDS + wi]
                        wv[sp * WIDE_WORDS + wi] = concat_rm[wi] - wtmp
                        wtmp = (<unsigned long long>(concat_rv[wi] < wv[(sp + 1) * WIDE_WORDS + wi]) |
                                <unsigned long long>(concat_rm[wi] < wtmp))
                    # Mask result to w bits
                    wsp = w >> 6; bit_in_word = w & 63
                    if bit_in_word > 0 and wsp < WIDE_WORDS:
                        wv[sp * WIDE_WORDS + wsp] &= (<unsigned long long>1 << bit_in_word) - 1
                        for wi in range(wsp + 1, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    else:
                        for wi in range(wsp, WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                    for wi in range(WIDE_WORDS):
                        wm[sp * WIDE_WORDS + wi] = 0
                    wflag[sp] = 1; stack[sp].val = <long long>wv[sp * WIDE_WORDS]; stack[sp].mask = 0
                stack[sp].width = w
            else:
                if a.mask or b.mask:
                    stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
                else:
                    stack[sp].val = (a.val - b.val) & wmask
                    stack[sp].mask = 0
                    stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_MUL:
            sp -= 1
            b = stack[sp]
            b_wide = wflag[sp]
            sp -= 1
            a = stack[sp]
            a_wide = wflag[sp]
            w = a.width + b.width
            wmask = mask_for_width(w)
            a_any_x = 1 if a.mask else 0
            b_any_x = 1 if b.mask else 0
            if not a_any_x and a_wide:
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1; break
            if not b_any_x and b_wide:
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1; break
            if a_any_x or b_any_x:
                if w > 64:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = <unsigned long long>(-1)
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
            elif a_wide or b_wide or w > 64:
                with gil:
                    _wide_mul_py(
                        &wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS],
                        &wv[sp * WIDE_WORDS], a.width, a_wide, a.val,
                        &wv[(sp+1) * WIDE_WORDS], b.width, b_wide, b.val,
                    )
                if w > 64:
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = <long long>wv[sp * WIDE_WORDS]
                stack[sp].mask = 0; stack[sp].width = w
            else:
                stack[sp].val = (a.val * b.val) & wmask
                stack[sp].mask = 0
                stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_DIV:
            sp -= 1
            b = stack[sp]
            b_wide = wflag[sp]
            sp -= 1
            a = stack[sp]
            a_wide = wflag[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            a_any_x = 1 if a.mask else 0
            b_any_x = 1 if b.mask else 0
            if not a_any_x and a_wide:
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1; break
            if not b_any_x and b_wide:
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1; break
            b_is_zero = 0
            if b_wide:
                b_is_zero = 1
                for wi in range(WIDE_WORDS):
                    if wv[(sp+1) * WIDE_WORDS + wi]:
                        b_is_zero = 0; break
            elif b.val == 0:
                b_is_zero = 1
            if a_any_x or b_any_x or b_is_zero:
                if w > 64:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = <unsigned long long>(-1)
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
            elif a_wide or b_wide or w > 64:
                with gil:
                    _wide_div_py(
                        &wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS],
                        &wv[sp * WIDE_WORDS], a.width, a_wide, a.val,
                        &wv[(sp+1) * WIDE_WORDS], b.width, b_wide, b.val,
                    )
                if w > 64:
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = <long long>wv[sp * WIDE_WORDS]
                stack[sp].mask = 0; stack[sp].width = w
            else:
                stack[sp].val = (a.val // b.val) & wmask
                stack[sp].mask = 0
                stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_MOD:
            sp -= 1
            b = stack[sp]
            b_wide = wflag[sp]
            sp -= 1
            a = stack[sp]
            a_wide = wflag[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            a_any_x = 1 if a.mask else 0
            b_any_x = 1 if b.mask else 0
            if not a_any_x and a_wide:
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1; break
            if not b_any_x and b_wide:
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1; break
            b_is_zero = 0
            if b_wide:
                b_is_zero = 1
                for wi in range(WIDE_WORDS):
                    if wv[(sp+1) * WIDE_WORDS + wi]:
                        b_is_zero = 0; break
            elif b.val == 0:
                b_is_zero = 1
            if a_any_x or b_any_x or b_is_zero:
                if w > 64:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = <unsigned long long>(-1)
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
            elif a_wide or b_wide or w > 64:
                with gil:
                    _wide_mod_py(
                        &wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS],
                        &wv[sp * WIDE_WORDS], a.width, a_wide, a.val,
                        &wv[(sp+1) * WIDE_WORDS], b.width, b_wide, b.val,
                    )
                if w > 64:
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = <long long>wv[sp * WIDE_WORDS]
                stack[sp].mask = 0; stack[sp].width = w
            else:
                stack[sp].val = (a.val % b.val) & wmask
                stack[sp].mask = 0
                stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_SDIV or op == OP_SMOD:
            sp -= 1
            b = stack[sp]
            b_wide = wflag[sp]
            sp -= 1
            a = stack[sp]
            a_wide = wflag[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            a_any_x = 1 if a.mask else 0
            b_any_x = 1 if b.mask else 0
            if not a_any_x and a_wide:
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1; break
            if not b_any_x and b_wide:
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1; break
            b_is_zero = 0
            if b_wide:
                b_is_zero = 1
                for wi in range(WIDE_WORDS):
                    if wv[(sp+1) * WIDE_WORDS + wi]:
                        b_is_zero = 0; break
            elif b.val == 0:
                b_is_zero = 1
            if a_any_x or b_any_x or b_is_zero:
                if w > 64:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = <unsigned long long>(-1)
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    wflag[sp] = 1
                else:
                    wflag[sp] = 0
                stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
            else:
                # Convert to signed values (2's-complement)
                new_val = a.val
                if a.width > 0 and a.width < 64 and (a.val >> (a.width - 1)) & 1:
                    new_val = a.val - (<long long>1 << a.width)
                new_mask = b.val
                if b.width > 0 and b.width < 64 and (b.val >> (b.width - 1)) & 1:
                    new_mask = b.val - (<long long>1 << b.width)
                if op == OP_SDIV:
                    stack[sp].val = <long long>(new_val / new_mask) & wmask
                else:
                    stack[sp].val = (new_val - (<long long>(new_val / new_mask)) * new_mask) & wmask
                stack[sp].mask = 0
                stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        if op == OP_POW:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width if a.width > b.width else b.width
            wmask = mask_for_width(w)
            if a.mask or b.mask:
                stack[sp].val = 0; stack[sp].mask = wmask; stack[sp].width = w
            else:
                # Simple integer power, truncated to width
                result_val = 1
                n = <int>b.val
                for i in range(n):
                    result_val = result_val * a.val
                stack[sp].val = result_val & wmask
                stack[sp].mask = 0
                stack[sp].width = w
            sp += 1
            continue

        # ── Bitwise ──────────────────────────────────────────────

        if op == OP_BIT_AND:
            sp -= 2
            a = stack[sp]
            b = stack[sp + 1]
            w = a.width if a.width > b.width else b.width
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                # Wide path: promote narrow operands then do word-by-word
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS):
                        wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                for wi in range(WIDE_WORDS):
                    wtmp = wv[sp * WIDE_WORDS + wi]
                    wv[sp * WIDE_WORDS + wi] = wtmp & wv[(sp + 1) * WIDE_WORDS + wi] & ~(
                        (wm[sp * WIDE_WORDS + wi] | wm[(sp + 1) * WIDE_WORDS + wi])
                        & ~(~wtmp & ~wm[sp * WIDE_WORDS + wi])
                        & ~(~wv[(sp + 1) * WIDE_WORDS + wi] & ~wm[(sp + 1) * WIDE_WORDS + wi])
                    )
                    wm[sp * WIDE_WORDS + wi] = (
                        (wm[sp * WIDE_WORDS + wi] | wm[(sp + 1) * WIDE_WORDS + wi])
                        & ~(~wtmp & ~wm[sp * WIDE_WORDS + wi])
                        & ~(~wv[(sp + 1) * WIDE_WORDS + wi] & ~wm[(sp + 1) * WIDE_WORDS + wi])
                    )
                wflag[sp] = 1
                stack[sp].val = 0; stack[sp].mask = 0
            else:
                wmask = mask_for_width(w)
                if a.mask == 0 and b.mask == 0:
                    stack[sp].val = a.val & b.val
                    stack[sp].mask = 0
                else:
                    new_mask = (a.mask | b.mask) & ~(~a.val & ~a.mask) & ~(~b.val & ~b.mask) & wmask
                    stack[sp].val = a.val & b.val & ~new_mask
                    stack[sp].mask = new_mask
                wflag[sp] = 0
            stack[sp].width = w
            sp += 1
            continue

        if op == OP_BIT_OR:
            sp -= 2
            a = stack[sp]
            b = stack[sp + 1]
            w = a.width if a.width > b.width else b.width
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                for wi in range(WIDE_WORDS):
                    # x | 1 = 1: known-1 bits kill the mask
                    new_val = (wv[sp * WIDE_WORDS + wi] & ~wm[sp * WIDE_WORDS + wi]) | (wv[(sp + 1) * WIDE_WORDS + wi] & ~wm[(sp + 1) * WIDE_WORDS + wi])
                    wm[sp * WIDE_WORDS + wi] = (wm[sp * WIDE_WORDS + wi] | wm[(sp + 1) * WIDE_WORDS + wi]) & ~new_val
                    wv[sp * WIDE_WORDS + wi] = (wv[sp * WIDE_WORDS + wi] | wv[(sp + 1) * WIDE_WORDS + wi] | new_val) & ~wm[sp * WIDE_WORDS + wi]
                wflag[sp] = 1
                stack[sp].val = 0; stack[sp].mask = 0
            else:
                wmask = mask_for_width(w)
                if a.mask == 0 and b.mask == 0:
                    stack[sp].val = a.val | b.val
                    stack[sp].mask = 0
                else:
                    new_val = (a.val & ~a.mask) | (b.val & ~b.mask)
                    new_mask = (a.mask | b.mask) & ~new_val & wmask
                    stack[sp].val = (a.val | b.val | new_val) & ~new_mask
                    stack[sp].mask = new_mask
                wflag[sp] = 0
            stack[sp].width = w
            sp += 1
            continue

        if op == OP_BIT_XOR:
            sp -= 2
            a = stack[sp]
            b = stack[sp + 1]
            w = a.width if a.width > b.width else b.width
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                for wi in range(WIDE_WORDS):
                    wm[sp * WIDE_WORDS + wi] = wm[sp * WIDE_WORDS + wi] | wm[(sp + 1) * WIDE_WORDS + wi]
                    wv[sp * WIDE_WORDS + wi] = (wv[sp * WIDE_WORDS + wi] ^ wv[(sp + 1) * WIDE_WORDS + wi]) & ~wm[sp * WIDE_WORDS + wi]
                wflag[sp] = 1
                stack[sp].val = 0; stack[sp].mask = 0
            else:
                wmask = mask_for_width(w)
                if a.mask == 0 and b.mask == 0:
                    stack[sp].val = a.val ^ b.val
                    stack[sp].mask = 0
                else:
                    new_mask = (a.mask | b.mask) & wmask
                    stack[sp].val = (a.val ^ b.val) & ~new_mask
                    stack[sp].mask = new_mask
                wflag[sp] = 0
            stack[sp].width = w
            sp += 1
            continue

        if op == OP_BIT_XNOR:
            sp -= 2
            a = stack[sp]
            b = stack[sp + 1]
            w = a.width if a.width > b.width else b.width
            if w > 64 or wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                for wi in range(WIDE_WORDS):
                    wm[sp * WIDE_WORDS + wi] = wm[sp * WIDE_WORDS + wi] | wm[(sp + 1) * WIDE_WORDS + wi]
                    wv[sp * WIDE_WORDS + wi] = ~(wv[sp * WIDE_WORDS + wi] ^ wv[(sp + 1) * WIDE_WORDS + wi]) & ~wm[sp * WIDE_WORDS + wi]
                # Mask top word to avoid garbage in unused bits
                wsp = (w - 1) >> 6
                if wsp < WIDE_WORDS:
                    n = w & 63
                    if n > 0:
                        wtmp = (1ULL << n) - 1
                        wv[sp * WIDE_WORDS + wsp] &= wtmp
                        wm[sp * WIDE_WORDS + wsp] &= wtmp
                wflag[sp] = 1
                stack[sp].val = 0; stack[sp].mask = 0
            else:
                wmask = mask_for_width(w)
                if a.mask == 0 and b.mask == 0:
                    stack[sp].val = ~(a.val ^ b.val) & wmask
                    stack[sp].mask = 0
                else:
                    new_mask = (a.mask | b.mask) & wmask
                    stack[sp].val = ~(a.val ^ b.val) & wmask & ~new_mask
                    stack[sp].mask = new_mask
                wflag[sp] = 0
            stack[sp].width = w
            sp += 1
            continue

        if op == OP_BIT_NOT:
            w = stack[sp - 1].width
            if w > 64 or wflag[sp - 1]:
                if not wflag[sp - 1]:
                    a = stack[sp - 1]
                    wv[(sp - 1) * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[(sp - 1) * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp - 1) * WIDE_WORDS + wi] = 0; wm[(sp - 1) * WIDE_WORDS + wi] = 0
                    wflag[sp - 1] = 1
                for wi in range(WIDE_WORDS):
                    wv[(sp - 1) * WIDE_WORDS + wi] = ~wv[(sp - 1) * WIDE_WORDS + wi] & ~wm[(sp - 1) * WIDE_WORDS + wi]
                # Mask top word
                wsp = (w - 1) >> 6
                if wsp < WIDE_WORDS:
                    n = w & 63
                    if n > 0:
                        wtmp = (1ULL << n) - 1
                        wv[(sp - 1) * WIDE_WORDS + wsp] &= wtmp
                        wm[(sp - 1) * WIDE_WORDS + wsp] &= wtmp
                stack[sp - 1].val = 0; stack[sp - 1].mask = 0
            else:
                a = stack[sp - 1]
                wmask = mask_for_width(a.width)
                if a.mask:
                    stack[sp - 1].val = ~a.val & ~a.mask & wmask
                    stack[sp - 1].mask = a.mask
                else:
                    stack[sp - 1].val = ~a.val & wmask
                    stack[sp - 1].mask = 0
            continue

        if op == OP_SHL:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width
            if w > 64 or wflag[sp]:
                if b.mask:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS] = <unsigned long long>a.mask
                        for wi in range(1, WIDE_WORDS): wm[sp * WIDE_WORDS + wi] = 0
                    n = <int>b.val
                    wrd_off = n >> 6; bit_off = n & 63
                    for wi in range(WIDE_WORDS - 1, -1, -1):
                        if wi - wrd_off < 0:
                            concat_rv[wi] = 0; concat_rm[wi] = 0
                        elif bit_off == 0:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi - wrd_off]
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi - wrd_off]
                        else:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi - wrd_off] << bit_off
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi - wrd_off] << bit_off
                            if wi - wrd_off - 1 >= 0:
                                concat_rv[wi] |= wv[sp * WIDE_WORDS + wi - wrd_off - 1] >> (64 - bit_off)
                                concat_rm[wi] |= wm[sp * WIDE_WORDS + wi - wrd_off - 1] >> (64 - bit_off)
                    # Mask top word
                    wsp = (w - 1) >> 6
                    if wsp < WIDE_WORDS:
                        bit_in_word = w & 63
                        if bit_in_word > 0:
                            wtmp = (1ULL << bit_in_word) - 1
                            concat_rv[wsp] &= wtmp; concat_rm[wsp] &= wtmp
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                        wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0; stack[sp].width = w
            else:
                wmask = mask_for_width(w)
                if b.mask:
                    stack[sp].val = 0; stack[sp].mask = wmask
                elif a.mask:
                    stack[sp].val = (a.val << <int>b.val) & wmask
                    stack[sp].mask = (a.mask << <int>b.val) & wmask
                else:
                    stack[sp].val = (a.val << <int>b.val) & wmask
                    stack[sp].mask = 0
                stack[sp].width = w; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_SHR:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width
            if w > 64 or wflag[sp]:
                if b.mask:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS] = <unsigned long long>a.mask
                        for wi in range(1, WIDE_WORDS): wm[sp * WIDE_WORDS + wi] = 0
                    n = <int>b.val
                    wrd_off = n >> 6; bit_off = n & 63
                    for wi in range(WIDE_WORDS):
                        if wi + wrd_off >= WIDE_WORDS:
                            concat_rv[wi] = 0; concat_rm[wi] = 0
                        elif bit_off == 0:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi + wrd_off]
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi + wrd_off]
                        else:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi + wrd_off] >> bit_off
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi + wrd_off] >> bit_off
                            if wi + wrd_off + 1 < WIDE_WORDS:
                                concat_rv[wi] |= wv[sp * WIDE_WORDS + wi + wrd_off + 1] << (64 - bit_off)
                                concat_rm[wi] |= wm[sp * WIDE_WORDS + wi + wrd_off + 1] << (64 - bit_off)
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                        wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0; stack[sp].width = w
            else:
                if b.mask:
                    wmask = mask_for_width(w)
                    stack[sp].val = 0; stack[sp].mask = wmask
                elif a.mask:
                    stack[sp].val = a.val >> <int>b.val
                    stack[sp].mask = a.mask >> <int>b.val
                else:
                    stack[sp].val = a.val >> <int>b.val
                    stack[sp].mask = 0
                stack[sp].width = w; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_ASHL:
            # Same as logical shift left
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width
            if w > 64 or wflag[sp]:
                if b.mask:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS] = <unsigned long long>a.mask
                        for wi in range(1, WIDE_WORDS): wm[sp * WIDE_WORDS + wi] = 0
                    n = <int>b.val
                    wrd_off = n >> 6; bit_off = n & 63
                    for wi in range(WIDE_WORDS - 1, -1, -1):
                        if wi - wrd_off < 0:
                            concat_rv[wi] = 0; concat_rm[wi] = 0
                        elif bit_off == 0:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi - wrd_off]
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi - wrd_off]
                        else:
                            concat_rv[wi] = wv[sp * WIDE_WORDS + wi - wrd_off] << bit_off
                            concat_rm[wi] = wm[sp * WIDE_WORDS + wi - wrd_off] << bit_off
                            if wi - wrd_off - 1 >= 0:
                                concat_rv[wi] |= wv[sp * WIDE_WORDS + wi - wrd_off - 1] >> (64 - bit_off)
                                concat_rm[wi] |= wm[sp * WIDE_WORDS + wi - wrd_off - 1] >> (64 - bit_off)
                    # Mask top word
                    wsp = (w - 1) >> 6
                    if wsp < WIDE_WORDS:
                        bit_in_word = w & 63
                        if bit_in_word > 0:
                            wtmp = (1ULL << bit_in_word) - 1
                            concat_rv[wsp] &= wtmp; concat_rm[wsp] &= wtmp
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                        wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0; stack[sp].width = w
            else:
                wmask = mask_for_width(w)
                if b.mask:
                    stack[sp].val = 0; stack[sp].mask = wmask
                elif a.mask:
                    stack[sp].val = (a.val << <int>b.val) & wmask
                    stack[sp].mask = (a.mask << <int>b.val) & wmask
                else:
                    stack[sp].val = (a.val << <int>b.val) & wmask
                    stack[sp].mask = 0
                stack[sp].width = w; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_ASHR:
            sp -= 1
            b = stack[sp]
            sp -= 1
            a = stack[sp]
            w = a.width
            if w > 64 or wflag[sp]:
                if b.mask:
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                else:
                    if not wflag[sp]:
                        wv[sp * WIDE_WORDS] = <unsigned long long>a.val
                        for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS] = <unsigned long long>a.mask
                        for wi in range(1, WIDE_WORDS): wm[sp * WIDE_WORDS + wi] = 0
                    # If left operand has any X bits, result is all-X
                    has_x = 0
                    for wi in range(WIDE_WORDS):
                        if wm[sp * WIDE_WORDS + wi]: has_x = 1; break
                    if has_x:
                        for wi in range(WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = 0
                            wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                        _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
                    else:
                        n = <int>b.val
                        # Find sign bit: bit (w-1), which is bit (w-1)&63 of word (w-1)>>6
                        wsp = (w - 1) >> 6
                        bit_in_word = (w - 1) & 63
                        if wsp < WIDE_WORDS:
                            wtmp = (wv[sp * WIDE_WORDS + wsp] >> bit_in_word) & 1ULL  # sign bit
                        else:
                            wtmp = 0
                        # Logical right-shift first (word-by-word)
                        wrd_off = n >> 6; bit_off = n & 63
                        for wi in range(WIDE_WORDS):
                            if wi + wrd_off >= WIDE_WORDS:
                                concat_rv[wi] = 0; concat_rm[wi] = 0
                            elif bit_off == 0:
                                concat_rv[wi] = wv[sp * WIDE_WORDS + wi + wrd_off]
                                concat_rm[wi] = wm[sp * WIDE_WORDS + wi + wrd_off]
                            else:
                                concat_rv[wi] = wv[sp * WIDE_WORDS + wi + wrd_off] >> bit_off
                                concat_rm[wi] = wm[sp * WIDE_WORDS + wi + wrd_off] >> bit_off
                                if wi + wrd_off + 1 < WIDE_WORDS:
                                    concat_rv[wi] |= wv[sp * WIDE_WORDS + wi + wrd_off + 1] << (64 - bit_off)
                                    concat_rm[wi] |= wm[sp * WIDE_WORDS + wi + wrd_off + 1] << (64 - bit_off)
                        if wtmp:
                            # Fill vacated high bits with 1s (sign-fill)
                            # Fill bits [w-n .. w-1] with 1s (n bits at the top of the w-bit field)
                            if n >= w:
                                # Shift >= width: entire result is all-1s
                                for wi in range(WIDE_WORDS):
                                    concat_rv[wi] = 0xFFFFFFFFFFFFFFFF
                            else:
                                # Fill top n bits of the w-bit field
                                # The fill starts at bit position (w - n) within the w-bit result
                                n_bits = (w - n) >> 6   # fill_start_word
                                bits_here = (w - n) & 63  # fill_start_bit
                                for wi in range(WIDE_WORDS):
                                    if wi > n_bits:
                                        if wi * 64 < w:
                                            concat_rv[wi] = 0xFFFFFFFFFFFFFFFF
                                    elif wi == n_bits:
                                        # Fill bits [bits_here .. 63] with 1s
                                        concat_rv[wi] |= ~((1ULL << bits_here) - 1)
                            # Mask the top word to w bits
                            wsp = (w - 1) >> 6
                            if wsp < WIDE_WORDS:
                                bit_in_word = w & 63
                                if bit_in_word > 0:
                                    wtmp = (1ULL << bit_in_word) - 1
                                    concat_rv[wsp] &= wtmp; concat_rm[wsp] &= wtmp
                        for wi in range(WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                            wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0; stack[sp].width = w
            else:
                wmask = mask_for_width(w)
                if b.mask or a.mask:
                    stack[sp].val = 0; stack[sp].mask = wmask
                else:
                    n = <int>b.val
                    if w > 0 and (a.val >> (w - 1)) & 1:
                        result_val = a.val - (1LL << w)
                        result_val = result_val >> n
                        stack[sp].val = result_val & wmask
                    else:
                        stack[sp].val = (a.val >> n) & wmask
                    stack[sp].mask = 0
                stack[sp].width = w; wflag[sp] = 0
            sp += 1
            continue

        # ── Comparison ───────────────────────────────────────────

        if op == OP_CMP_EQ:
            sp -= 2
            a = stack[sp]; b = stack[sp + 1]
            if wflag[sp] or wflag[sp + 1]:
                # Wide comparison: promote narrow first, then word-by-word
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                # Check for any X bits
                result_val = 1; new_mask = 0
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi] or wm[(sp + 1) * WIDE_WORDS + wi]:
                        new_mask = 1; break
                    if wv[sp * WIDE_WORDS + wi] != wv[(sp + 1) * WIDE_WORDS + wi]:
                        result_val = 0; break
                if new_mask:
                    stack[sp].val = 0; stack[sp].mask = 1
                else:
                    stack[sp].val = result_val; stack[sp].mask = 0
                wflag[sp] = 0
            else:
                if a.mask or b.mask:
                    stack[sp].val = 0; stack[sp].mask = 1
                else:
                    stack[sp].val = 1 if a.val == b.val else 0
                    stack[sp].mask = 0
            stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_NE:
            sp -= 2
            a = stack[sp]; b = stack[sp + 1]
            if wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                    wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask)
                    wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                result_val = 0; new_mask = 0
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi] or wm[(sp + 1) * WIDE_WORDS + wi]:
                        new_mask = 1; break
                    if wv[sp * WIDE_WORDS + wi] != wv[(sp + 1) * WIDE_WORDS + wi]:
                        result_val = 1; break
                if new_mask:
                    stack[sp].val = 0; stack[sp].mask = 1
                else:
                    stack[sp].val = result_val; stack[sp].mask = 0
                wflag[sp] = 0
            else:
                if a.mask or b.mask:
                    stack[sp].val = 0; stack[sp].mask = 1
                else:
                    stack[sp].val = 1 if a.val != b.val else 0
                    stack[sp].mask = 0
            stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_LT:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            if a.mask or b.mask:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1
            else:
                stack[sp].val = 1 if a.val < b.val else 0
                stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_LE:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            if a.mask or b.mask:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1
            else:
                stack[sp].val = 1 if a.val <= b.val else 0
                stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_GT:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            if a.mask or b.mask:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1
            else:
                stack[sp].val = 1 if a.val > b.val else 0
                stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_GE:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            if a.mask or b.mask:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1
            else:
                stack[sp].val = 1 if a.val >= b.val else 0
                stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_CASE_EQ:
            sp -= 2
            a = stack[sp]; b = stack[sp + 1]
            if wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask); wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask); wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                stack[sp].val = 1 if _wide_eq(&wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS], &wv[(sp + 1) * WIDE_WORDS], &wm[(sp + 1) * WIDE_WORDS], WIDE_WORDS) else 0
                wflag[sp] = 0
            else:
                stack[sp].val = 1 if (a.val == b.val and a.mask == b.mask) else 0
            stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_CASE_NE:
            sp -= 2
            a = stack[sp]; b = stack[sp + 1]
            if wflag[sp] or wflag[sp + 1]:
                if not wflag[sp]:
                    wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask); wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                    for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                if not wflag[sp + 1]:
                    wv[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.val & ~b.mask); wm[(sp + 1) * WIDE_WORDS]  = <unsigned long long>(b.mask)
                    for wi in range(1, WIDE_WORDS): wv[(sp + 1) * WIDE_WORDS + wi] = 0; wm[(sp + 1) * WIDE_WORDS + wi] = 0
                stack[sp].val = 0 if _wide_eq(&wv[sp * WIDE_WORDS], &wm[sp * WIDE_WORDS], &wv[(sp + 1) * WIDE_WORDS], &wm[(sp + 1) * WIDE_WORDS], WIDE_WORDS) else 1
                wflag[sp] = 0
            else:
                stack[sp].val = 1 if (a.val != b.val or a.mask != b.mask) else 0
            stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        # ── Logical ──────────────────────────────────────────────

        if op == OP_LOG_AND:
            sp -= 1; b = stack[sp]
            b_wide = wflag[sp]; wflag[sp] = 0
            sp -= 1; a = stack[sp]
            a_wide = wflag[sp]; wflag[sp] = 0
            if b_wide:
                b_any_x = 0; any_one = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1
                    if wv[(sp + 1) * WIDE_WORDS + wi] & ~wm[(sp + 1) * WIDE_WORDS + wi]: any_one = 1
                if any_one:
                    b.val = 1; b.mask = 0
                elif b_any_x:
                    b.val = 0; b.mask = 1
                else:
                    b.val = 0; b.mask = 0
            if a_wide:
                a_any_x = 0; any_one = 0
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1
                    if wv[sp * WIDE_WORDS + wi] & ~wm[sp * WIDE_WORDS + wi]: any_one = 1
                if any_one:
                    a.val = 1; a.mask = 0
                elif a_any_x:
                    a.val = 0; a.mask = 1
                else:
                    a.val = 0; a.mask = 0
            if a.mask or b.mask:
                if a.mask == 0 and a.val == 0:
                    stack[sp].val = 0; stack[sp].mask = 0
                elif b.mask == 0 and b.val == 0:
                    stack[sp].val = 0; stack[sp].mask = 0
                else:
                    stack[sp].val = 0; stack[sp].mask = 1
            else:
                stack[sp].val = 1 if (a.val != 0 and b.val != 0) else 0
                stack[sp].mask = 0
            stack[sp].width = 1
            sp += 1; continue

        if op == OP_LOG_OR:
            sp -= 1; b = stack[sp]
            b_wide = wflag[sp]; wflag[sp] = 0
            sp -= 1; a = stack[sp]
            a_wide = wflag[sp]; wflag[sp] = 0
            if b_wide:
                b_any_x = 0; any_one = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: b_any_x = 1
                    if wv[(sp + 1) * WIDE_WORDS + wi] & ~wm[(sp + 1) * WIDE_WORDS + wi]: any_one = 1
                if any_one:
                    b.val = 1; b.mask = 0
                elif b_any_x:
                    b.val = 0; b.mask = 1
                else:
                    b.val = 0; b.mask = 0
            if a_wide:
                a_any_x = 0; any_one = 0
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: a_any_x = 1
                    if wv[sp * WIDE_WORDS + wi] & ~wm[sp * WIDE_WORDS + wi]: any_one = 1
                if any_one:
                    a.val = 1; a.mask = 0
                elif a_any_x:
                    a.val = 0; a.mask = 1
                else:
                    a.val = 0; a.mask = 0
            if a.mask or b.mask:
                if a.mask == 0 and a.val != 0:
                    stack[sp].val = 1; stack[sp].mask = 0
                elif b.mask == 0 and b.val != 0:
                    stack[sp].val = 1; stack[sp].mask = 0
                else:
                    stack[sp].val = 0; stack[sp].mask = 1
            else:
                stack[sp].val = 1 if (a.val != 0 or b.val != 0) else 0
                stack[sp].mask = 0
            stack[sp].width = 1
            sp += 1; continue

        if op == OP_LOG_NOT:
            a = stack[sp - 1]
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    any_one = 0
                    for wi in range(WIDE_WORDS):
                        if wv[(sp - 1) * WIDE_WORDS + wi]: any_one = 1; break
                    stack[sp - 1].val = 0 if any_one else 1
                    stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 1 if a.val == 0 else 0
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        # ── Unary ────────────────────────────────────────────────

        if op == OP_NEG:
            a = stack[sp - 1]
            w = a.width
            wmask = mask_for_width(w)
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    for wi in range(WIDE_WORDS):
                        wv[(sp - 1) * WIDE_WORDS + wi] = 0
                        wm[(sp - 1) * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
                    # Mask wm to w bits so upper words don't leak X into downstream ops
                    wsp = w >> 6; bit_in_word = w & 63
                    if bit_in_word > 0 and wsp < WIDE_WORDS:
                        wm[(sp - 1) * WIDE_WORDS + wsp] = (<unsigned long long>1 << bit_in_word) - 1
                        for wi in range(wsp + 1, WIDE_WORDS):
                            wm[(sp - 1) * WIDE_WORDS + wi] = 0
                    else:
                        for wi in range(wsp, WIDE_WORDS):
                            wm[(sp - 1) * WIDE_WORDS + wi] = 0
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 0
                else:
                    # Two's complement: ~v + 1 word-by-word
                    wtmp = 1
                    for wi in range(WIDE_WORDS):
                        concat_rv[wi] = ~wv[(sp - 1) * WIDE_WORDS + wi]
                        wv[(sp - 1) * WIDE_WORDS + wi] = concat_rv[wi] + wtmp
                        wtmp = <unsigned long long>(wv[(sp - 1) * WIDE_WORDS + wi] < concat_rv[wi])
                    # Mask to w bits
                    wsp = w >> 6; bit_in_word = w & 63
                    if bit_in_word > 0 and wsp < WIDE_WORDS:
                        wv[(sp - 1) * WIDE_WORDS + wsp] &= (<unsigned long long>1 << bit_in_word) - 1
                        for wi in range(wsp + 1, WIDE_WORDS):
                            wv[(sp - 1) * WIDE_WORDS + wi] = 0
                    else:
                        for wi in range(wsp, WIDE_WORDS):
                            wv[(sp - 1) * WIDE_WORDS + wi] = 0
                    for wi in range(WIDE_WORDS):
                        wm[(sp - 1) * WIDE_WORDS + wi] = 0
                    stack[sp - 1].val = <long long>wv[(sp - 1) * WIDE_WORDS]
                    stack[sp - 1].mask = 0
                if w <= 64:
                    wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = wmask
                wflag[sp - 1] = 0
            else:
                stack[sp - 1].val = (-a.val) & wmask
                stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            continue

        if op == OP_UPLUS:
            continue

        # ── Reduction ────────────────────────────────────────────

        if op == OP_RED_AND:
            a = stack[sp - 1]
            wmask = mask_for_width(a.width)
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    all_ones = 1
                    wsp = a.width >> 6; bit_in_word = a.width & 63
                    for wi in range(wsp):
                        if wv[(sp - 1) * WIDE_WORDS + wi] != <unsigned long long>0xFFFFFFFFFFFFFFFF:
                            all_ones = 0; break
                    if all_ones and bit_in_word > 0 and wsp < WIDE_WORDS:
                        if (wv[(sp - 1) * WIDE_WORDS + wsp] & ((<unsigned long long>1 << bit_in_word) - 1)) != ((<unsigned long long>1 << bit_in_word) - 1):
                            all_ones = 0
                    stack[sp - 1].val = all_ones; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 1 if (a.val & wmask) == wmask else 0
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        if op == OP_RED_OR:
            a = stack[sp - 1]
            if wflag[sp - 1]:
                any_one = 0
                for wi in range(WIDE_WORDS):
                    if wv[(sp - 1) * WIDE_WORDS + wi] & ~wm[(sp - 1) * WIDE_WORDS + wi]:
                        any_one = 1; break
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if any_one:
                    stack[sp - 1].val = 1; stack[sp - 1].mask = 0
                elif has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                if a.val & ~a.mask:
                    stack[sp - 1].val = 1; stack[sp - 1].mask = 0
                else:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 1 if a.val != 0 else 0
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        if op == OP_RED_XOR:
            a = stack[sp - 1]
            wmask = mask_for_width(a.width)
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    red_parity = 0
                    for wi in range(WIDE_WORDS):
                        red_parity ^= popcount64(wv[(sp - 1) * WIDE_WORDS + wi]) & 1
                    stack[sp - 1].val = red_parity & 1; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = popcount64(<unsigned long long>(a.val & wmask)) & 1
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        if op == OP_RED_NAND:
            a = stack[sp - 1]
            wmask = mask_for_width(a.width)
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    all_ones = 1
                    wsp = a.width >> 6; bit_in_word = a.width & 63
                    for wi in range(wsp):
                        if wv[(sp - 1) * WIDE_WORDS + wi] != <unsigned long long>0xFFFFFFFFFFFFFFFF:
                            all_ones = 0; break
                    if all_ones and bit_in_word > 0 and wsp < WIDE_WORDS:
                        if (wv[(sp - 1) * WIDE_WORDS + wsp] & ((<unsigned long long>1 << bit_in_word) - 1)) != ((<unsigned long long>1 << bit_in_word) - 1):
                            all_ones = 0
                    stack[sp - 1].val = 0 if all_ones else 1; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 0 if (a.val & wmask) == wmask else 1
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        if op == OP_RED_NOR:
            a = stack[sp - 1]
            if wflag[sp - 1]:
                any_one = 0
                for wi in range(WIDE_WORDS):
                    if wv[(sp - 1) * WIDE_WORDS + wi] & ~wm[(sp - 1) * WIDE_WORDS + wi]:
                        any_one = 1; break
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if any_one:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 0
                elif has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    stack[sp - 1].val = 1; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                if a.val & ~a.mask:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 0
                else:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 0 if a.val != 0 else 1
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        if op == OP_RED_XNOR:
            a = stack[sp - 1]
            wmask = mask_for_width(a.width)
            if wflag[sp - 1]:
                has_x = 0
                for wi in range(WIDE_WORDS):
                    if wm[(sp - 1) * WIDE_WORDS + wi]: has_x = 1; break
                if has_x:
                    stack[sp - 1].val = 0; stack[sp - 1].mask = 1
                else:
                    red_parity = 0
                    for wi in range(WIDE_WORDS):
                        red_parity ^= popcount64(wv[(sp - 1) * WIDE_WORDS + wi]) & 1
                    stack[sp - 1].val = 1 if (red_parity & 1) == 0 else 0; stack[sp - 1].mask = 0
                wflag[sp - 1] = 0
            elif a.mask:
                stack[sp - 1].val = 0; stack[sp - 1].mask = 1
            else:
                stack[sp - 1].val = 1 if (popcount64(<unsigned long long>(a.val & wmask)) & 1) == 0 else 0
                stack[sp - 1].mask = 0
            stack[sp - 1].width = 1
            continue

        # ── Special expression ops ───────────────────────────────

        if op == OP_BIT_SELECT:
            sp -= 2
            b = stack[sp + 1]   # index (top, always narrow)
            a = stack[sp]       # target
            if b.mask == 0:
                i = <int>b.val
                if 0 <= i < a.width:
                    if wflag[sp]:
                        stack[sp].val = <long long>(_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i, 1))
                        stack[sp].mask = <long long>(_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i, 1))
                    else:
                        stack[sp].val = (a.val >> i) & 1
                        stack[sp].mask = (a.mask >> i) & 1
                else:
                    stack[sp].val = 0; stack[sp].mask = 1
            else:
                stack[sp].val = 0; stack[sp].mask = 1
            stack[sp].width = 1
            wflag[sp] = 0
            sp += 1
            continue

        if op == OP_RANGE_SELECT:
            sp -= 3
            b = stack[sp + 2]   # lsb (top)
            t = stack[sp + 1]   # msb
            a = stack[sp]       # target
            if t.mask == 0 and b.mask == 0:
                i = <int>b.val   # lsb
                w = <int>(t.val - b.val + 1)
                if w > 0:
                    if wflag[sp] or w > 64:
                        # Wide source or wide result
                        if not wflag[sp]:
                            # promote narrow source
                            wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                            wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                            for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                        # Extract 'w' bits starting at 'i' into a new wide slot
                        # For simplicity, use _wide_extract per word of result
                        if w > 64:
                            for wi in range(WIDE_WORDS):
                                wv[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (i + wi * 64 + 64) <= w else w - wi * 64)
                                wm[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (i + wi * 64 + 64) <= w else w - wi * 64)
                            wflag[sp] = 1
                            stack[sp].val = 0; stack[sp].mask = 0
                        else:
                            wmask = mask_for_width(w)
                            stack[sp].val  = <long long>(_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            stack[sp].mask = <long long>(_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            wflag[sp] = 0
                        stack[sp].width = w
                    else:
                        wmask = mask_for_width(w)
                        stack[sp].val = (a.val >> i) & wmask
                        stack[sp].mask = (a.mask >> i) & wmask
                        stack[sp].width = w
                        wflag[sp] = 0
                else:
                    stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            else:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_PART_SEL_UP:
            sp -= 3
            t = stack[sp + 2]   # width (top)
            b = stack[sp + 1]   # base
            a = stack[sp]       # target
            if b.mask == 0 and t.mask == 0:
                w = <int>t.val
                i = <int>b.val
                if w > 0:
                    if wflag[sp] or w > 64:
                        if not wflag[sp]:
                            wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                            wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                            for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                        if w > 64:
                            for wi in range(WIDE_WORDS):
                                wv[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (wi * 64 + 64) <= w else w - wi * 64)
                                wm[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (wi * 64 + 64) <= w else w - wi * 64)
                            wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0
                        else:
                            wmask = mask_for_width(w)
                            stack[sp].val  = <long long>(_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            stack[sp].mask = <long long>(_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            wflag[sp] = 0
                        stack[sp].width = w
                    else:
                        wmask = mask_for_width(w)
                        stack[sp].val = (a.val >> i) & wmask
                        stack[sp].mask = (a.mask >> i) & wmask
                        stack[sp].width = w; wflag[sp] = 0
                else:
                    stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            else:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_PART_SEL_DOWN:
            sp -= 3
            t = stack[sp + 2]   # width (top)
            b = stack[sp + 1]   # base
            a = stack[sp]       # target
            if b.mask == 0 and t.mask == 0:
                w = <int>t.val
                i = <int>(b.val - w + 1)
                if w > 0 and i >= 0:
                    if wflag[sp] or w > 64:
                        if not wflag[sp]:
                            wv[sp * WIDE_WORDS]      = <unsigned long long>(a.val & ~a.mask)
                            wm[sp * WIDE_WORDS]      = <unsigned long long>(a.mask)
                            for wi in range(1, WIDE_WORDS): wv[sp * WIDE_WORDS + wi] = 0; wm[sp * WIDE_WORDS + wi] = 0
                        if w > 64:
                            for wi in range(WIDE_WORDS):
                                wv[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (wi * 64 + 64) <= w else w - wi * 64)
                                wm[sp * WIDE_WORDS + wi] = <unsigned long long>_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i + wi * 64, 64 if (wi * 64 + 64) <= w else w - wi * 64)
                            wflag[sp] = 1; stack[sp].val = 0; stack[sp].mask = 0
                        else:
                            wmask = mask_for_width(w)
                            stack[sp].val  = <long long>(_wide_extract(&wv[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            stack[sp].mask = <long long>(_wide_extract(&wm[sp * WIDE_WORDS], WIDE_WORDS, i, w)) & wmask
                            wflag[sp] = 0
                        stack[sp].width = w
                    else:
                        wmask = mask_for_width(w)
                        stack[sp].val = (a.val >> i) & wmask
                        stack[sp].mask = (a.mask >> i) & wmask
                        stack[sp].width = w; wflag[sp] = 0
                else:
                    stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            else:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1; wflag[sp] = 0
            sp += 1
            continue

        if op == OP_CONCAT:
            n = arg1    # number of parts; TOS = LSB part, bottom = MSB part
            concat_rw = 0
            for wi in range(WIDE_WORDS):
                concat_rv[wi] = 0; concat_rm[wi] = 0
            i = 0
            while i < n:
                sp -= 1
                a = stack[sp]
                w = a.width
                # Insert part into concat_rv/rm at bit offset concat_rw
                wrd_off = concat_rw >> 6
                bit_off = concat_rw & 63
                if wflag[sp]:
                    # Wide part: insert word by word
                    for wi in range(WIDE_WORDS):
                        if wrd_off + wi < WIDE_WORDS:
                            if bit_off == 0:
                                concat_rv[wrd_off + wi] |= wv[sp * WIDE_WORDS + wi]
                                concat_rm[wrd_off + wi] |= wm[sp * WIDE_WORDS + wi]
                            else:
                                concat_rv[wrd_off + wi]     |= wv[sp * WIDE_WORDS + wi] << bit_off
                                concat_rm[wrd_off + wi]     |= wm[sp * WIDE_WORDS + wi] << bit_off
                                if wrd_off + wi + 1 < WIDE_WORDS:
                                    concat_rv[wrd_off + wi + 1] |= wv[sp * WIDE_WORDS + wi] >> (64 - bit_off)
                                    concat_rm[wrd_off + wi + 1] |= wm[sp * WIDE_WORDS + wi] >> (64 - bit_off)
                else:
                    if wrd_off < WIDE_WORDS:
                        if bit_off == 0:
                            concat_rv[wrd_off] |= <unsigned long long>(a.val & ~a.mask)
                            concat_rm[wrd_off] |= <unsigned long long>(a.mask)
                        else:
                            concat_rv[wrd_off] |= (<unsigned long long>(a.val & ~a.mask)) << bit_off
                            concat_rm[wrd_off] |= (<unsigned long long>(a.mask)) << bit_off
                            if wrd_off + 1 < WIDE_WORDS:
                                concat_rv[wrd_off + 1] |= (<unsigned long long>(a.val & ~a.mask)) >> (64 - bit_off)
                                concat_rm[wrd_off + 1] |= (<unsigned long long>(a.mask)) >> (64 - bit_off)
                concat_rw += w
                i += 1
            # sp now = original_sp - n; write result here
            if concat_rw > 64:
                for wi in range(WIDE_WORDS):
                    wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                    wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                wflag[sp] = 1
                stack[sp].val = 0; stack[sp].mask = 0
            else:
                wflag[sp] = 0
                wmask = mask_for_width(concat_rw)
                stack[sp].val  = <long long>(concat_rv[0]) & wmask
                stack[sp].mask = <long long>(concat_rm[0]) & wmask
            stack[sp].width = concat_rw
            sp += 1
            continue

        if op == OP_REPLICATE:
            sp -= 1
            a = stack[sp]     # value to replicate; sp+1 after next decrement
            sp -= 1
            b = stack[sp]     # count
            if b.mask == 0:
                n = <int>b.val
                result_width = a.width * n
                if result_width > 64:
                    # Wide result: assemble in concat scratch buffers, then copy to wv/wm.
                    # Mirrors OP_CONCAT insert logic for both narrow and wide source values.
                    for wi in range(WIDE_WORDS):
                        concat_rv[wi] = 0; concat_rm[wi] = 0
                    for i in range(n):
                        wrd_off = (i * a.width) >> 6
                        bit_off = (i * a.width) & 63
                        if wflag[sp + 1]:
                            # Wide source (a.width > 64): insert word-by-word from wv
                            for wi in range(WIDE_WORDS):
                                if wrd_off + wi < WIDE_WORDS:
                                    if bit_off == 0:
                                        concat_rv[wrd_off + wi] |= wv[(sp + 1) * WIDE_WORDS + wi]
                                        concat_rm[wrd_off + wi] |= wm[(sp + 1) * WIDE_WORDS + wi]
                                    else:
                                        concat_rv[wrd_off + wi]     |= wv[(sp + 1) * WIDE_WORDS + wi] << bit_off
                                        concat_rm[wrd_off + wi]     |= wm[(sp + 1) * WIDE_WORDS + wi] << bit_off
                                        if wrd_off + wi + 1 < WIDE_WORDS:
                                            concat_rv[wrd_off + wi + 1] |= wv[(sp + 1) * WIDE_WORDS + wi] >> (64 - bit_off)
                                            concat_rm[wrd_off + wi + 1] |= wm[(sp + 1) * WIDE_WORDS + wi] >> (64 - bit_off)
                        else:
                            # Narrow source: insert from a.val/a.mask
                            if wrd_off < WIDE_WORDS:
                                if bit_off == 0:
                                    concat_rv[wrd_off] |= <unsigned long long>(a.val & ~a.mask)
                                    concat_rm[wrd_off] |= <unsigned long long>(a.mask)
                                else:
                                    concat_rv[wrd_off] |= (<unsigned long long>(a.val & ~a.mask)) << bit_off
                                    concat_rm[wrd_off] |= (<unsigned long long>(a.mask)) << bit_off
                                    if wrd_off + 1 < WIDE_WORDS:
                                        concat_rv[wrd_off + 1] |= (<unsigned long long>(a.val & ~a.mask)) >> (64 - bit_off)
                                        concat_rm[wrd_off + 1] |= (<unsigned long long>(a.mask)) >> (64 - bit_off)
                    for wi in range(WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = concat_rv[wi]
                        wm[sp * WIDE_WORDS + wi] = concat_rm[wi]
                    wflag[sp] = 1
                    stack[sp].val = 0; stack[sp].mask = 0
                else:
                    result_val = 0
                    result_mask = 0
                    for i in range(n):
                        result_val = (result_val << a.width) | a.val
                        result_mask = (result_mask << a.width) | a.mask
                    wmask = mask_for_width(result_width)
                    stack[sp].val = result_val & wmask
                    stack[sp].mask = result_mask & wmask
                    wflag[sp] = 0
            else:
                result_width = a.width
                stack[sp].val = 0
                stack[sp].mask = mask_for_width(a.width)
                wflag[sp] = 0
            stack[sp].width = result_width
            sp += 1
            continue

        # ── Control flow ─────────────────────────────────────────

        if op == OP_JUMP:
            pc = arg1
            continue

        if op == OP_JUMP_IF_ZERO:
            sp -= 1
            a = stack[sp]
            if a.mask or a.val == 0:
                pc = arg1
            continue

        if op == OP_JUMP_IF_NONZERO:
            sp -= 1
            a = stack[sp]
            if a.mask == 0 and a.val != 0:
                pc = arg1
            continue

        if op == OP_DUP:
            stack[sp] = stack[sp - 1]
            wflag[sp] = wflag[sp - 1]
            if wflag[sp - 1]:
                for wi in range(WIDE_WORDS):
                    wv[sp * WIDE_WORDS + wi] = wv[(sp - 1) * WIDE_WORDS + wi]
                    wm[sp * WIDE_WORDS + wi] = wm[(sp - 1) * WIDE_WORDS + wi]
            sp += 1
            continue

        if op == OP_POP:
            if sp > 0:
                sp -= 1
            continue

        if op == OP_NOP:
            continue

        # ── System tasks (need Python callback) ──────────────────

        if op == OP_SYS_DISPLAY:
            # arg1 packs: n_args in low 16 bits, fmt_id in high 16 bits
            n = arg1 & 0xFFFF
            arg2 = arg1 >> 16
            if disp_buf != NULL and disp_idx + 3 + 3 * n <= disp_cap:
                disp_buf[disp_idx] = arg2   # fmt_id
                disp_buf[disp_idx + 1] = n  # n_args
                disp_buf[disp_idx + 2] = 0  # is_monitor=0
                i = n - 1
                while i >= 0:
                    sp -= 1
                    disp_buf[disp_idx + 3 + i * 3]     = stack[sp].val
                    disp_buf[disp_idx + 3 + i * 3 + 1] = stack[sp].mask
                    disp_buf[disp_idx + 3 + i * 3 + 2] = stack[sp].width
                    i -= 1
                disp_idx += 3 + 3 * n
            elif disp_buf != NULL:
                # Display buffer overflow
                nba_count[0] = nba_idx
                dirty_count[0] = dirty_idx
                if nba_mem_count != NULL:
                    nba_mem_count[0] = nba_mem_idx
                if disp_pos != NULL:
                    disp_pos[0] = disp_idx
                return 2
            else:
                # No buffer (standalone wrapper) — just pop and discard
                i = 0
                while i < n:
                    sp -= 1
                    i += 1
            continue

        if op == OP_SYS_FINISH:
            nba_count[0] = nba_idx
            dirty_count[0] = dirty_idx
            if nba_mem_count != NULL:
                nba_mem_count[0] = nba_mem_idx
            if disp_pos != NULL:
                disp_pos[0] = disp_idx
            return 1  # finish

        if op == OP_SYS_TIME:
            stack[sp].val = sim_time
            stack[sp].mask = 0
            stack[sp].width = 64
            sp += 1
            continue

        if op == OP_PROC_END:
            nba_count[0] = nba_idx
            dirty_count[0] = dirty_idx
            if nba_mem_count != NULL:
                nba_mem_count[0] = nba_mem_idx
            if disp_pos != NULL:
                disp_pos[0] = disp_idx
            return 0

        if op == OP_FUNC_CLOG2:
            a = stack[sp - 1]
            if a.mask:
                stack[sp - 1].val = 0
                stack[sp - 1].mask = mask_for_width(32)
                stack[sp - 1].width = 32
            else:
                n = <int>a.val
                if n <= 1:
                    result_val = 0
                else:
                    result_val = 0
                    n = n - 1
                    while n > 0:
                        result_val += 1
                        n >>= 1
                stack[sp - 1].val = result_val
                stack[sp - 1].mask = 0
                stack[sp - 1].width = 32
            continue

        if op == OP_CMP_CASEX:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            # casex: x/z bits in either operand are don't-care
            new_mask = a.mask | b.mask
            stack[sp].val = 1 if ((a.val ^ b.val) & ~new_mask) == 0 else 0
            stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_CMP_CASEZ:
            sp -= 1; b = stack[sp]
            sp -= 1; a = stack[sp]
            # casez: same as casex in our 4-state model
            new_mask = a.mask | b.mask
            stack[sp].val = 1 if ((a.val ^ b.val) & ~new_mask) == 0 else 0
            stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        # ── Signed comparisons ────────────────────────────────────

        if op == OP_CMP_SLT or op == OP_CMP_SLE or op == OP_CMP_SGT or op == OP_CMP_SGE:
            sp -= 1; b = stack[sp]
            b_wide = wflag[sp]; wflag[sp] = 0
            sp -= 1; a = stack[sp]
            a_wide = wflag[sp]; wflag[sp] = 0
            has_x = 0
            if a.mask or b.mask:
                has_x = 1
            elif a_wide:
                for wi in range(WIDE_WORDS):
                    if wm[sp * WIDE_WORDS + wi]: has_x = 1; break
            if not has_x and b_wide:
                for wi in range(WIDE_WORDS):
                    if wm[(sp + 1) * WIDE_WORDS + wi]: has_x = 1; break
            if has_x:
                stack[sp].val = 0; stack[sp].mask = 1; stack[sp].width = 1
            elif a_wide or b_wide:
                with gil:
                    result_val = _wide_signed_cmp_py(
                        &wv[sp * WIDE_WORDS], a.width, a_wide, a.val,
                        &wv[(sp + 1) * WIDE_WORDS], b.width, b_wide, b.val,
                        op,
                    )
                stack[sp].val = result_val; stack[sp].mask = 0; stack[sp].width = 1
            else:
                # Sign-extend to 64-bit signed long long for comparison.
                # new_val = signed(a), new_mask = signed(b)  (reusing scratch vars)
                new_val = a.val
                if a.width > 0 and a.width < 64 and (a.val >> (a.width - 1)) & 1:
                    new_val = a.val - (<long long>1 << a.width)
                new_mask = b.val
                if b.width > 0 and b.width < 64 and (b.val >> (b.width - 1)) & 1:
                    new_mask = b.val - (<long long>1 << b.width)
                if op == OP_CMP_SLT:
                    result_val = 1 if new_val < new_mask else 0
                elif op == OP_CMP_SLE:
                    result_val = 1 if new_val <= new_mask else 0
                elif op == OP_CMP_SGT:
                    result_val = 1 if new_val > new_mask else 0
                else:
                    result_val = 1 if new_val >= new_mask else 0
                stack[sp].val = result_val; stack[sp].mask = 0; stack[sp].width = 1
            sp += 1; continue

        if op == OP_FUNC_RANDOM:
            # Push a pseudo-random 32-bit value (no Python interaction)
            stack[sp].val = <long long>(c_rand() & 0x7FFFFFFF) | (<long long>(c_rand() & 0x1) << 31)
            stack[sp].mask = 0
            stack[sp].width = 32
            sp += 1
            continue

        if op == OP_SYS_READMEM:
            # File I/O handled by Python interpreter path; skip in C fast path
            continue

        # ── Ternary merge ────────────────────────────────────────

        if op == OP_TERNARY:
            # Stack: [cond, true_val, false_val] → [result]
            sp -= 1
            b = stack[sp]       # false_val
            b_wide = wflag[sp]
            sp -= 1
            a = stack[sp]       # true_val
            a_wide = wflag[sp]
            sp -= 1
            t = stack[sp]       # cond
            if t.mask == 0:
                # Condition fully defined — select one operand
                if t.val != 0:
                    stack[sp] = a
                    wflag[sp] = a_wide
                    if a_wide:
                        for wi in range(WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = wv[(sp + 1) * WIDE_WORDS + wi]
                            wm[sp * WIDE_WORDS + wi] = wm[(sp + 1) * WIDE_WORDS + wi]
                else:
                    stack[sp] = b
                    wflag[sp] = b_wide
                    if b_wide:
                        for wi in range(WIDE_WORDS):
                            wv[sp * WIDE_WORDS + wi] = wv[(sp + 2) * WIDE_WORDS + wi]
                            wm[sp * WIDE_WORDS + wi] = wm[(sp + 2) * WIDE_WORDS + wi]
            elif a_wide or b_wide:
                # Condition has x/z with wide operands: merge bit by bit
                w = a.width if a.width > b.width else b.width
                wflag[sp] = 1
                for wi in range(WIDE_WORDS):
                    av_w = wv[(sp + 1) * WIDE_WORDS + wi] if a_wide else (<unsigned long long>a.val if wi == 0 else 0)
                    am_w = wm[(sp + 1) * WIDE_WORDS + wi] if a_wide else (<unsigned long long>a.mask if wi == 0 else 0)
                    bv_w = wv[(sp + 2) * WIDE_WORDS + wi] if b_wide else (<unsigned long long>b.val if wi == 0 else 0)
                    bm_w = wm[(sp + 2) * WIDE_WORDS + wi] if b_wide else (<unsigned long long>b.mask if wi == 0 else 0)
                    agree_word = ~(av_w ^ bv_w) & ~(am_w ^ bm_w)
                    wm[sp * WIDE_WORDS + wi] = ~agree_word
                    wv[sp * WIDE_WORDS + wi] = av_w & bv_w & agree_word
                # Mask result to w bits
                wsp = w >> 6; bit_in_word = w & 63
                if bit_in_word > 0 and wsp < WIDE_WORDS:
                    wm[sp * WIDE_WORDS + wsp] &= (<unsigned long long>1 << bit_in_word) - 1
                    wv[sp * WIDE_WORDS + wsp] &= (<unsigned long long>1 << bit_in_word) - 1
                    for wi in range(wsp + 1, WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0
                else:
                    for wi in range(wsp, WIDE_WORDS):
                        wv[sp * WIDE_WORDS + wi] = 0
                        wm[sp * WIDE_WORDS + wi] = 0
                stack[sp].val = <long long>wv[sp * WIDE_WORDS]
                stack[sp].mask = <long long>wm[sp * WIDE_WORDS]
                stack[sp].width = w
            else:
                # Condition has x/z with narrow operands: merge bit by bit
                w = a.width if a.width > b.width else b.width
                wmask = mask_for_width(w)
                new_val = ~(a.val ^ b.val) & ~(a.mask ^ b.mask) & wmask
                new_mask = ~new_val & wmask
                stack[sp].val = a.val & b.val & ~new_mask & wmask
                stack[sp].mask = new_mask
                stack[sp].width = w
                wflag[sp] = 0
            sp += 1
            continue

        # ── Monitor (buffer output like display) ─────────────────

        if op == OP_SYS_MONITOR:
            n = arg1 & 0xFFFF
            arg2 = arg1 >> 16
            if disp_buf != NULL and disp_idx + 3 + 3 * n <= disp_cap:
                disp_buf[disp_idx] = arg2   # fmt_id
                disp_buf[disp_idx + 1] = n  # n_args
                disp_buf[disp_idx + 2] = 1  # is_monitor=1
                i = n - 1
                while i >= 0:
                    sp -= 1
                    disp_buf[disp_idx + 3 + i * 3]     = stack[sp].val
                    disp_buf[disp_idx + 3 + i * 3 + 1] = stack[sp].mask
                    disp_buf[disp_idx + 3 + i * 3 + 2] = stack[sp].width
                    i -= 1
                disp_idx += 3 + 3 * n
            elif disp_buf != NULL:
                # Display buffer overflow
                nba_count[0] = nba_idx
                dirty_count[0] = dirty_idx
                if nba_mem_count != NULL:
                    nba_mem_count[0] = nba_mem_idx
                if disp_pos != NULL:
                    disp_pos[0] = disp_idx
                return 2
            else:
                i = 0
                while i < n:
                    sp -= 1
                    i += 1
            continue

        # ── Memory array operations ──────────────────────────────

        if op == OP_LOAD_MEM:
            # arg1 = mem_id, TOS = index → push mem[index]
            sp -= 1
            b = stack[sp]     # index
            if mem_val != NULL and b.mask == 0 and arg1 < mem_count:
                i = <int>b.val
                if 0 <= i < mem_depth[arg1]:
                    n = mem_base[arg1] + i
                    stack[sp].val = mem_val[n]
                    stack[sp].mask = mem_mask[n]
                    stack[sp].width = mem_elem_width[arg1]
                else:
                    w = mem_elem_width[arg1]
                    stack[sp].val = 0; stack[sp].mask = mask_for_width(w); stack[sp].width = w
            else:
                w = mem_elem_width[arg1] if (mem_val != NULL and arg1 < mem_count) else 1
                stack[sp].val = 0; stack[sp].mask = mask_for_width(w); stack[sp].width = w
            wflag[sp] = 0  # memory elements are always narrow
            sp += 1
            continue

        if op == OP_STORE_MEM:
            # arg1 = mem_id (low 16) | (marker_sid << 16)
            sp -= 1
            b = stack[sp]     # index
            sp -= 1
            a = stack[sp]     # value
            mid = arg1 & 0xFFFF
            marker_sid = arg1 >> 16
            if mem_val != NULL and b.mask == 0 and mid < mem_count:
                i = <int>b.val
                if 0 <= i < mem_depth[mid]:
                    n = mem_base[mid] + i
                    w = mem_elem_width[mid]
                    wmask = mask_for_width(w)
                    new_val = a.val & wmask & ~a.mask
                    new_mask = a.mask & wmask
                    if mem_val[n] != new_val or mem_mask[n] != new_mask:
                        mem_val[n] = new_val
                        mem_mask[n] = new_mask
                        # Mark memory marker signal dirty for combo re-eval
                        if dirty_idx < dirty_max:
                            dirty_buf[dirty_idx] = marker_sid
                            dirty_idx += 1
            continue

        if op == OP_NBA_MEM:
            # arg1 = mem_id (low 16) | (marker_sid << 16)
            sp -= 1
            b = stack[sp]     # index
            sp -= 1
            a = stack[sp]     # value
            mid = arg1 & 0xFFFF
            if mem_val != NULL and b.mask == 0 and mid < mem_count:
                i = <int>b.val
                if 0 <= i < mem_depth[mid]:
                    w = mem_elem_width[mid]
                    wmask = mask_for_width(w)
                    if nba_mem_buf != NULL and nba_mem_idx < nba_mem_max:
                        # Queue the memory NBA for later application
                        nba_mem_buf[nba_mem_idx].mem_id = arg1  # encode marker_sid in upper bits
                        nba_mem_buf[nba_mem_idx].addr = i
                        nba_mem_buf[nba_mem_idx].val = a.val & wmask & ~a.mask
                        nba_mem_buf[nba_mem_idx].mask = a.mask & wmask
                        nba_mem_idx += 1
                    elif nba_mem_buf != NULL:
                        # NBA memory buffer overflow — signal data loss
                        nba_count[0] = nba_idx
                        dirty_count[0] = dirty_idx
                        if nba_mem_count != NULL:
                            nba_mem_count[0] = nba_mem_idx
                        if disp_pos != NULL:
                            disp_pos[0] = disp_idx
                        return 2
                    else:
                        # No NBA buffer: apply immediately (standalone wrapper path)
                        n = mem_base[mid] + i
                        mem_val[n] = a.val & wmask & ~a.mask
                        mem_mask[n] = a.mask & wmask
            continue

        if op == OP_STORE_MEM_RANGE:
            # Blocking partial memory write.
            # arg1 = mem_id (low 16) | (marker_sid << 16)
            # stack: [val, idx, msb, lsb] — lsb on top
            sp -= 1; t = stack[sp]   # lsb
            sp -= 1; b = stack[sp]   # msb
            sp -= 1; a = stack[sp]   # idx (array index)
            sp -= 1                   # val is now at stack[sp]
            mid = arg1 & 0xFFFF
            marker_sid = arg1 >> 16
            if mem_val != NULL and t.mask == 0 and b.mask == 0 and a.mask == 0 and mid < mem_count:
                i = <int>a.val
                if 0 <= i < mem_depth[mid]:
                    n = mem_base[mid] + i
                    w = mem_elem_width[mid]
                    wmask = mask_for_width(w)
                    # Bit-range mask for bits [msb:lsb]
                    new_val = mask_for_width(<int>b.val + 1) ^ mask_for_width(<int>t.val)
                    new_val = new_val & wmask
                    # Shifted val/mask into range position
                    result_val = (stack[sp].val << <int>t.val) & new_val & ~(stack[sp].mask << <int>t.val)
                    result_mask = (stack[sp].mask << <int>t.val) & new_val
                    # Merge: keep bits outside range from current element
                    result_val = (mem_val[n] & ~new_val) | result_val
                    result_mask = (mem_mask[n] & ~new_val) | result_mask
                    if mem_val[n] != result_val or mem_mask[n] != result_mask:
                        mem_val[n] = result_val
                        mem_mask[n] = result_mask
                        if dirty_idx < dirty_max:
                            dirty_buf[dirty_idx] = marker_sid
                            dirty_idx += 1
            continue

        if op == OP_NBA_MEM_RANGE:
            # Non-blocking partial memory write.
            # arg1 = mem_id (low 16) | (marker_sid << 16)
            # stack: [val, idx, msb, lsb] — lsb on top
            # The C delta loop lacks range-NBA infrastructure, so apply immediately
            # (same as blocking) — valid for cases where NBA ordering within one
            # active region does not matter, which is the common case.
            sp -= 1; t = stack[sp]   # lsb
            sp -= 1; b = stack[sp]   # msb
            sp -= 1; a = stack[sp]   # idx
            sp -= 1                   # val at stack[sp]
            mid = arg1 & 0xFFFF
            marker_sid = arg1 >> 16
            if mem_val != NULL and t.mask == 0 and b.mask == 0 and a.mask == 0 and mid < mem_count:
                i = <int>a.val
                if 0 <= i < mem_depth[mid]:
                    n = mem_base[mid] + i
                    w = mem_elem_width[mid]
                    wmask = mask_for_width(w)
                    new_val = mask_for_width(<int>b.val + 1) ^ mask_for_width(<int>t.val)
                    new_val = new_val & wmask
                    result_val = (stack[sp].val << <int>t.val) & new_val & ~(stack[sp].mask << <int>t.val)
                    result_mask = (stack[sp].mask << <int>t.val) & new_val
                    result_val = (mem_val[n] & ~new_val) | result_val
                    result_mask = (mem_mask[n] & ~new_val) | result_mask
                    if mem_val[n] != result_val or mem_mask[n] != result_mask:
                        mem_val[n] = result_val
                        mem_mask[n] = result_mask
                        if dirty_idx < dirty_max:
                            dirty_buf[dirty_idx] = marker_sid
                            dirty_idx += 1
            continue

    # Fell off end of program
    nba_count[0] = nba_idx
    dirty_count[0] = dirty_idx
    if nba_mem_count != NULL:
        nba_mem_count[0] = nba_mem_idx
    if disp_pos != NULL:
        disp_pos[0] = disp_idx
    return 0


# ── Continuous-assign multi-pass propagation ─────────────────────────

cdef int _propagate_cont_assigns(DeltaCtx *dc, int *p_changed_count) noexcept nogil:
    """Propagate continuous assigns to convergence using a worklist.

    Iterates until no new signals are dirtied.  Uses dc.is_work as the
    "current-pass input" bitset and dc.is_changed for cumulative dedup.

    Invariant: dc.is_work is zero on entry and restored to zero on return.

    Returns 0 = converged, 1 = $finish, 2 = buffer overflow.
    *p_changed_count is updated with the final changed count.
    """
    cdef int changed_count = p_changed_count[0]
    cdef int ca_new_start, ca_iter
    cdef int j, c, pid, sid, off, plen, status, hit
    cdef int dirty_room, nba_room
    cdef int work_lo = 0, work_hi = changed_count

    # Initialise is_work from the full current changed set
    for j in range(changed_count):
        dc.is_work[dc.changed_buf[j]] = 1

    for ca_iter in range(dc.delta_limit):
        ca_new_start = changed_count

        # Clear is_changed for the current work batch BEFORE firing CAs so that
        # a signal already in is_work can be re-queued if a later CA in this same
        # iteration updates it again (multi-path / diamond propagation).
        for j in range(work_lo, work_hi):
            dc.is_changed[dc.changed_buf[j]] = 0

        for c in range(dc.cont_count):
            pid = dc.cont_indices[c]
            hit = 0
            for j in range(dc.cont_sens_offset[c], dc.cont_sens_offset[c + 1]):
                if dc.is_work[dc.cont_sens_sigs[j]]:
                    hit = 1
                    break
            if hit:
                off = dc.prog_offset[pid]
                plen = dc.prog_length[pid]
                dirty_room = dc.dirty_cap
                nba_room = 0
                status = _execute_core(
                    &dc.all_ops[off], &dc.all_a1[off], plen,
                    dc.sig_val, dc.sig_mask, dc.sig_width, dc.sig_count,
                    dc.const_val, dc.const_mask, dc.const_width, dc.const_count,
                    dc.nba_buf, &nba_room,
                    dc.dirty_buf, &dirty_room,
                    dc.sim_time,
                    dc.mem_val, dc.mem_mask, dc.mem_elem_width,
                    dc.mem_depth, dc.mem_base, dc.mem_count,
                    NULL, NULL,
                    dc.disp_buf, dc.disp_pos, dc.disp_cap,
                    &dc.wctx if dc.wctx.sig_offset != NULL else NULL,
                )
                for j in range(dirty_room):
                    sid = dc.dirty_buf[j]
                    if not dc.is_changed[sid]:
                        dc.is_changed[sid] = 1
                        dc.changed_buf[changed_count] = sid
                        changed_count += 1
                if status != 0:
                    # Restore is_work to zero before returning
                    for j in range(work_lo, work_hi):
                        dc.is_work[dc.changed_buf[j]] = 0
                    for j in range(ca_new_start, changed_count):
                        dc.is_work[dc.changed_buf[j]] = 0
                    p_changed_count[0] = 0
                    return status

        if changed_count == ca_new_start:
            break  # converged — no new signals dirtied this pass

        # Advance is_work to the new batch only.
        for j in range(work_lo, work_hi):
            dc.is_work[dc.changed_buf[j]] = 0
        for j in range(ca_new_start, changed_count):
            dc.is_work[dc.changed_buf[j]] = 1
        work_lo = ca_new_start
        work_hi = changed_count

    # Cleanup: clear the last active is_work batch
    for j in range(work_lo, work_hi):
        dc.is_work[dc.changed_buf[j]] = 0

    p_changed_count[0] = changed_count
    return 0


# ── Full delta-cycle loop in C ───────────────────────────────────────

cdef int _run_delta_loop_core(DeltaCtx *dc, int *p_changed_count) noexcept nogil:
    """Run the complete delta cycle loop without touching Python.

    Phase 0 — run dirty continuous assigns on the initial changed set
              (propagates event-driven signal changes through assign nets).
    Phase 1 — iterate: collect triggered → execute → apply NBAs →
              run dirty continuous assigns → repeat until convergence.

    Returns  0 = converged,  1 = $finish,  -1 = delta limit exceeded.
    The caller supplies (and owns) all buffers through *dc*.
    """
    cdef int delta = 0
    cdef int changed_count = p_changed_count[0]
    cdef int triggered_count, total_nba_count, total_nba_mem_count
    cdef int i, j, c, sid, esid, pid, off, plen, status
    cdef int nba_room, dirty_room, nba_mem_room
    cdef int hit, etype
    cdef long long v, m, rm, new_v, new_m
    cdef int old_bit, old_x, new_bit, new_x
    cdef int n_flat
    cdef int mid, marker_sid
    cdef int p_lsb, p_n, p_wrd_off, p_bit_in_word, p_bits_here
    cdef unsigned long long p_wmask, p_old_v, p_old_m, p_new_v, p_new_m

    # ── Phase 0: propagate event changes through continuous assigns ──
    # Multi-pass worklist: propagates CA chains regardless of cont_indices order.
    # Matches vm_scheduler._propagate_continuous semantics.
    if changed_count > 0 and dc.cont_count > 0:
        status = _propagate_cont_assigns(dc, &changed_count)
        if status != 0:
            p_changed_count[0] = 0
            return status

    # ── Phase 1: delta cycle iterations ──────────────────────────
    while True:
        delta += 1
        if delta > dc.delta_limit:
            p_changed_count[0] = changed_count
            return -1

        # ── Collect triggered procs ──
        triggered_count = 0
        for c in range(changed_count):
            sid = dc.changed_buf[c]
            for j in range(dc.sens_offset[sid], dc.sens_offset[sid + 1]):
                pid = dc.sens_procs[j]
                if dc.proc_is_combo[pid] and not dc.trig_flag[pid]:
                    dc.trig_flag[pid] = 1
                    dc.triggered_buf[triggered_count] = pid
                    triggered_count += 1
                elif dc.proc_is_seq[pid] and not dc.seq_fired[pid] and not dc.trig_flag[pid]:
                    # Check edge conditions for this sequential proc
                    hit = 0
                    for i in range(dc.edge_offset[pid], dc.edge_offset[pid + 1]):
                        etype = dc.edge_types[i]
                        esid = dc.edge_sigs[i]
                        old_bit = <int>(dc.snap_val[esid] & 1)
                        old_x = 1 if (dc.snap_mask[esid] & 1) else 0
                        new_bit = <int>(dc.sig_val[esid] & 1)
                        new_x = 1 if (dc.sig_mask[esid] & 1) else 0
                        if etype == 0:   # posedge
                            if not new_x and new_bit == 1 and (old_x or old_bit == 0):
                                hit = 1
                                break
                        else:            # negedge
                            if not new_x and new_bit == 0 and (old_x or old_bit == 1):
                                hit = 1
                                break
                    if hit:
                        dc.seq_fired[pid] = 1
                        dc.trig_flag[pid] = 1
                        dc.triggered_buf[triggered_count] = pid
                        triggered_count += 1

        if triggered_count == 0:
            break

        # Clear triggered flags (reusable next delta)
        for i in range(triggered_count):
            dc.trig_flag[dc.triggered_buf[i]] = 0

        # Clear old changed set
        for i in range(changed_count):
            dc.is_changed[dc.changed_buf[i]] = 0
        changed_count = 0

        # ── Execute all triggered procs ──
        total_nba_count = 0
        total_nba_mem_count = 0
        for i in range(triggered_count):
            pid = dc.triggered_buf[i]
            off = dc.prog_offset[pid]
            plen = dc.prog_length[pid]

            # dirty_buf is reused per-proc; nba_buf accumulates
            nba_room = dc.nba_cap - total_nba_count
            if nba_room < 0:
                nba_room = 0
            dirty_room = dc.dirty_cap
            nba_mem_room = dc.nba_mem_cap - total_nba_mem_count
            if nba_mem_room < 0:
                nba_mem_room = 0

            status = _execute_core(
                &dc.all_ops[off], &dc.all_a1[off], plen,
                dc.sig_val, dc.sig_mask, dc.sig_width, dc.sig_count,
                dc.const_val, dc.const_mask, dc.const_width, dc.const_count,
                &dc.nba_buf[total_nba_count], &nba_room,
                dc.dirty_buf, &dirty_room,
                dc.sim_time,
                dc.mem_val, dc.mem_mask, dc.mem_elem_width,
                dc.mem_depth, dc.mem_base, dc.mem_count,
                &dc.nba_mem_buf[total_nba_mem_count] if dc.nba_mem_buf != NULL else NULL,
                &nba_mem_room if dc.nba_mem_buf != NULL else NULL,
                dc.disp_buf, dc.disp_pos, dc.disp_cap,
                &dc.wctx if dc.wctx.sig_offset != NULL else NULL,
            )
            total_nba_count += nba_room
            if dc.nba_mem_buf != NULL:
                total_nba_mem_count += nba_mem_room

            # Merge blocking-assign dirty into changed
            for j in range(dirty_room):
                sid = dc.dirty_buf[j]
                if not dc.is_changed[sid]:
                    dc.is_changed[sid] = 1
                    dc.changed_buf[changed_count] = sid
                    changed_count += 1

            if status == 1:
                p_changed_count[0] = 0
                return 1
            if status == 2:
                p_changed_count[0] = 0
                return 2

        # ── Apply NBAs ──
        for i in range(total_nba_count):
            sid = dc.nba_buf[i].sig_id
            v   = dc.nba_buf[i].val
            m   = dc.nba_buf[i].mask
            rm  = dc.nba_buf[i].range_mask
            if rm == 0:
                new_v = v
                new_m = m
            else:
                new_v = (dc.sig_val[sid] & ~rm) | (v & rm)
                new_m = (dc.sig_mask[sid] & ~rm) | (m & rm)
            if dc.sig_val[sid] != new_v or dc.sig_mask[sid] != new_m:
                dc.sig_val[sid] = new_v
                dc.sig_mask[sid] = new_m
                if not dc.is_changed[sid]:
                    dc.is_changed[sid] = 1
                    dc.changed_buf[changed_count] = sid
                    changed_count += 1

        # ── Apply wide signal NBAs (full-signal replace) ──
        if dc.wctx.sig_offset != NULL and dc.wctx.nba_count != NULL:
            for i in range(dc.wctx.nba_count[0]):
                sid = dc.wctx.nba_sids[i]
                off = dc.wctx.sig_offset[sid]
                for j in range(WIDE_WORDS):
                    new_v = <long long>dc.wctx.nba_val[i * WIDE_WORDS + j]
                    new_m = <long long>dc.wctx.nba_mask[i * WIDE_WORDS + j]
                    if <long long>dc.wctx.sig_val[off + j] != new_v or <long long>dc.wctx.sig_mask[off + j] != new_m:
                        dc.wctx.sig_val[off + j]  = <unsigned long long>new_v
                        dc.wctx.sig_mask[off + j] = <unsigned long long>new_m
                        if not dc.is_changed[sid]:
                            dc.is_changed[sid] = 1
                            dc.changed_buf[changed_count] = sid
                            changed_count += 1
            dc.wctx.nba_count[0] = 0

        # ── Apply wide partial NBAs (read-modify-write: NBA_BIT/NBA_RANGE on wide signals) ──
        if dc.wctx.sig_offset != NULL and dc.wctx.nba_part_count != NULL:
            for i in range(dc.wctx.nba_part_count[0]):
                sid = dc.wctx.nba_part_sids[i]
                off = dc.wctx.sig_offset[sid]
                p_lsb = dc.wctx.nba_part_lsb[i]
                p_n   = dc.wctx.nba_part_n[i]
                for j in range(WIDE_WORDS):
                    p_wrd_off = j * 64
                    if p_lsb + p_n <= p_wrd_off:
                        break
                    if p_lsb >= p_wrd_off + 64:
                        continue
                    p_bit_in_word = p_lsb - p_wrd_off
                    if p_bit_in_word < 0:
                        p_bit_in_word = 0
                    p_bits_here = p_lsb + p_n - p_wrd_off - p_bit_in_word
                    if p_bits_here > 64 - p_bit_in_word:
                        p_bits_here = 64 - p_bit_in_word
                    if p_bits_here <= 0:
                        continue
                    p_wmask = <unsigned long long>mask_for_width(p_bits_here)
                    p_wmask = p_wmask << p_bit_in_word
                    p_old_v = dc.wctx.sig_val[off + j]
                    p_old_m = dc.wctx.sig_mask[off + j]
                    p_new_v = (p_old_v & ~p_wmask) | (dc.wctx.nba_part_val[i * WIDE_WORDS + j]  & p_wmask)
                    p_new_m = (p_old_m & ~p_wmask) | (dc.wctx.nba_part_mask[i * WIDE_WORDS + j] & p_wmask)
                    if p_old_v != p_new_v or p_old_m != p_new_m:
                        dc.wctx.sig_val[off + j]  = p_new_v
                        dc.wctx.sig_mask[off + j] = p_new_m
                        if not dc.is_changed[sid]:
                            dc.is_changed[sid] = 1
                            dc.changed_buf[changed_count] = sid
                            changed_count += 1
            dc.wctx.nba_part_count[0] = 0

        # ── Apply memory NBAs ──
        if dc.nba_mem_buf != NULL:
            for i in range(total_nba_mem_count):
                # mem_id field encodes marker_sid in upper 16 bits
                mid = dc.nba_mem_buf[i].mem_id & 0xFFFF
                marker_sid = dc.nba_mem_buf[i].mem_id >> 16
                n_flat = dc.mem_base[mid] + dc.nba_mem_buf[i].addr
                dc.mem_val[n_flat] = dc.nba_mem_buf[i].val
                dc.mem_mask[n_flat] = dc.nba_mem_buf[i].mask
                # Mark memory marker signal as changed for combo re-eval
                if marker_sid < dc.sig_count and not dc.is_changed[marker_sid]:
                    dc.is_changed[marker_sid] = 1
                    dc.changed_buf[changed_count] = marker_sid
                    changed_count += 1

        # ── Run dirty continuous assigns (multi-pass worklist) ──
        if changed_count > 0 and dc.cont_count > 0:
            status = _propagate_cont_assigns(dc, &changed_count)
            if status != 0:
                p_changed_count[0] = 0
                return status

    # Clean up flags
    for i in range(changed_count):
        dc.is_changed[dc.changed_buf[i]] = 0
    p_changed_count[0] = 0
    return 0


# ── Python-visible wrapper ───────────────────────────────────────────

class CyStopSimulation(Exception):
    """Raised when $finish is executed in the fast interpreter."""
    pass


def cy_execute(
    list program,
    list sig_val_list,
    list sig_mask_list,
    list sig_width_list,
    list const_val_list,
    list const_mask_list,
    list const_width_list,
    long long sim_time,
):
    """Execute bytecode using the fast C interpreter.

    Args:
        program: list of (op, arg1, arg2) tuples.
        sig_val_list: mutable list of signal values (int).
        sig_mask_list: mutable list of signal masks (int).
        sig_width_list: list of signal widths (int).
        const_val_list: list of constant values (int).
        const_mask_list: list of constant masks (int).
        const_width_list: list of constant widths (int).
        sim_time: current simulation time.

    Returns:
        (status, nba_list, dirty_set) where:
            status: 0=ok, 1=finish
            nba_list: list of (sig_id, val, mask) tuples
            dirty_set: set of signal IDs changed by blocking assigns

    Raises:
        CyStopSimulation: if $finish is encountered.
    """
    cdef int prog_len = len(program)
    cdef int sig_count = len(sig_val_list)
    cdef int const_count = len(const_val_list)

    # Allocate C arrays
    cdef int *prog_ops = <int *>malloc(prog_len * sizeof(int))
    cdef int *prog_a1  = <int *>malloc(prog_len * sizeof(int))
    cdef long long *sig_val  = <long long *>malloc(sig_count * sizeof(long long))
    cdef long long *sig_mask = <long long *>malloc(sig_count * sizeof(long long))
    cdef int       *sig_w    = <int *>malloc(sig_count * sizeof(int))
    cdef long long *c_val    = <long long *>malloc(const_count * sizeof(long long))
    cdef long long *c_mask   = <long long *>malloc(const_count * sizeof(long long))
    cdef int       *c_w      = <int *>malloc(const_count * sizeof(int))
    cdef NBAEntry  *nba_buf  = <NBAEntry *>malloc(NBA_MAX * sizeof(NBAEntry))
    cdef int       *dirty_buf = <int *>malloc(sig_count * sizeof(int))
    cdef int        nba_count = NBA_MAX
    cdef int        dirty_count = sig_count
    cdef int        status
    cdef int        i

    if (prog_ops == NULL or prog_a1 == NULL or sig_val == NULL or
        sig_mask == NULL or sig_w == NULL or c_val == NULL or
        c_mask == NULL or c_w == NULL or nba_buf == NULL or dirty_buf == NULL):
        # Free whatever we allocated
        free(prog_ops); free(prog_a1); free(sig_val); free(sig_mask)
        free(sig_w); free(c_val); free(c_mask); free(c_w)
        free(nba_buf); free(dirty_buf)
        raise MemoryError("Failed to allocate interpreter buffers")

    try:
        # Copy program
        for i in range(prog_len):
            t = program[i]
            prog_ops[i] = <int>t[0]
            prog_a1[i]  = <int>t[1]

        # Copy signal state
        for i in range(sig_count):
            sig_val[i]  = <long long>sig_val_list[i]
            sig_mask[i] = <long long>sig_mask_list[i]
            sig_w[i]    = <int>sig_width_list[i]

        # Copy constants
        for i in range(const_count):
            c_val[i]  = <long long>const_val_list[i]
            c_mask[i] = <long long>const_mask_list[i]
            c_w[i]    = <int>const_width_list[i]

        # Execute
        status = _execute_core(
            prog_ops, prog_a1, prog_len,
            sig_val, sig_mask, sig_w, sig_count,
            c_val, c_mask, c_w, const_count,
            nba_buf, &nba_count,
            dirty_buf, &dirty_count,
            sim_time,
            NULL, NULL, NULL, NULL, NULL, 0,
            NULL, NULL,
            NULL, NULL, 0,
            NULL,
        )

        # Copy signal state back
        for i in range(sig_count):
            sig_val_list[i]  = sig_val[i]
            sig_mask_list[i] = sig_mask[i]

        # Build NBA list — merge partial-range entries in order
        nba_merged = {}
        for i in range(nba_count):
            sid_ = nba_buf[i].sig_id
            v_   = nba_buf[i].val
            m_   = nba_buf[i].mask
            rm_  = nba_buf[i].range_mask
            if rm_ == 0:
                nba_merged[sid_] = (v_, m_)
            else:
                cur_v, cur_m = nba_merged.get(sid_, (sig_val[sid_], sig_mask[sid_]))
                nba_merged[sid_] = ((cur_v & ~rm_) | (v_ & rm_), (cur_m & ~rm_) | (m_ & rm_))
        nba_list = [(sid_, v_, m_) for sid_, (v_, m_) in nba_merged.items()]

        # Build dirty set
        dirty_set = set()
        for i in range(dirty_count):
            dirty_set.add(dirty_buf[i])

        if status == 1:
            raise CyStopSimulation()
        if status == 2:
            raise RuntimeError(
                "VM Cython interpreter: buffer overflow (NBA/display capacity exceeded). "
                "Increase NBA_MAX/DISP_BUF_CAP and rebuild."
            )

        return (status, nba_list, dirty_set)

    finally:
        free(prog_ops)
        free(prog_a1)
        free(sig_val)
        free(sig_mask)
        free(sig_w)
        free(c_val)
        free(c_mask)
        free(c_w)
        free(nba_buf)
        free(dirty_buf)


def cy_execute_batch(
    list programs,
    list sig_val_list,
    list sig_mask_list,
    list sig_width_list,
    list const_val_list,
    list const_mask_list,
    list const_width_list,
    long long sim_time,
):
    """Execute multiple programs in sequence, collecting all NBAs and dirty signals.

    This avoids the overhead of Python→C→Python round-trips per process.
    All programs share the same signal state (mutations are visible to later programs).

    Returns:
        (nba_list, dirty_set) accumulated from all programs.

    Raises:
        CyStopSimulation: if any program executes $finish.
    """
    cdef int sig_count = len(sig_val_list)
    cdef int const_count = len(const_val_list)
    cdef int n_progs = len(programs)

    # Allocate signal and const arrays once
    cdef long long *sig_val  = <long long *>malloc(sig_count * sizeof(long long))
    cdef long long *sig_mask = <long long *>malloc(sig_count * sizeof(long long))
    cdef int       *sig_w    = <int *>malloc(sig_count * sizeof(int))
    cdef long long *c_val    = <long long *>malloc(const_count * sizeof(long long))
    cdef long long *c_mask   = <long long *>malloc(const_count * sizeof(long long))
    cdef int       *c_w      = <int *>malloc(const_count * sizeof(int))
    cdef NBAEntry  *nba_buf  = <NBAEntry *>malloc(NBA_MAX * sizeof(NBAEntry))
    cdef int       *dirty_buf = <int *>malloc(sig_count * sizeof(int))
    cdef int       *prog_ops = NULL
    cdef int       *prog_a1  = NULL
    cdef int        nba_count, dirty_count, status
    cdef int        prog_len, prog_alloc = 0
    cdef int        i, j

    if (sig_val == NULL or sig_mask == NULL or sig_w == NULL or
        c_val == NULL or c_mask == NULL or c_w == NULL or
        nba_buf == NULL or dirty_buf == NULL):
        free(sig_val); free(sig_mask); free(sig_w)
        free(c_val); free(c_mask); free(c_w)
        free(nba_buf); free(dirty_buf)
        raise MemoryError("Failed to allocate interpreter buffers")

    all_nba = []
    all_dirty = set()

    try:
        # Copy signal state in
        for i in range(sig_count):
            sig_val[i]  = <long long>sig_val_list[i]
            sig_mask[i] = <long long>sig_mask_list[i]
            sig_w[i]    = <int>sig_width_list[i]

        # Copy constants
        for i in range(const_count):
            c_val[i]  = <long long>const_val_list[i]
            c_mask[i] = <long long>const_mask_list[i]
            c_w[i]    = <int>const_width_list[i]

        # Execute each program
        for j in range(n_progs):
            prog = programs[j]
            prog_len = len(prog)

            # (Re)allocate program arrays if needed
            if prog_len > prog_alloc:
                free(prog_ops)
                free(prog_a1)
                prog_ops = <int *>malloc(prog_len * sizeof(int))
                prog_a1  = <int *>malloc(prog_len * sizeof(int))
                prog_alloc = prog_len
                if prog_ops == NULL or prog_a1 == NULL:
                    raise MemoryError("Failed to allocate program buffers")

            # Copy program
            for i in range(prog_len):
                t = prog[i]
                prog_ops[i] = <int>t[0]
                prog_a1[i]  = <int>t[1]

            nba_count = NBA_MAX
            dirty_count = sig_count

            status = _execute_core(
                prog_ops, prog_a1, prog_len,
                sig_val, sig_mask, sig_w, sig_count,
                c_val, c_mask, c_w, const_count,
                nba_buf, &nba_count,
                dirty_buf, &dirty_count,
                sim_time,
                NULL, NULL, NULL, NULL, NULL, 0,
                NULL, NULL,
                NULL, NULL, 0,
                NULL,
            )

            # Accumulate NBAs — merge partial-range entries in order
            for i in range(nba_count):
                sid_ = nba_buf[i].sig_id
                v_   = nba_buf[i].val
                m_   = nba_buf[i].mask
                rm_  = nba_buf[i].range_mask
                if rm_ == 0:
                    all_nba.append((sid_, v_, m_))
                else:
                    # Search backward for existing entry for same signal and merge
                    merged = False
                    for k in range(len(all_nba) - 1, -1, -1):
                        if all_nba[k][0] == sid_:
                            cur_v, cur_m = all_nba[k][1], all_nba[k][2]
                            all_nba[k] = (sid_,
                                          (cur_v & ~rm_) | (v_ & rm_),
                                          (cur_m & ~rm_) | (m_ & rm_))
                            merged = True
                            break
                    if not merged:
                        # No prior entry: merge with current sig_val
                        cur_v = sig_val[sid_]
                        cur_m = sig_mask[sid_]
                        all_nba.append((sid_,
                                        (cur_v & ~rm_) | (v_ & rm_),
                                        (cur_m & ~rm_) | (m_ & rm_)))

            # Accumulate dirty
            for i in range(dirty_count):
                all_dirty.add(dirty_buf[i])

            if status == 1:
                # Copy signal state back before raising
                for i in range(sig_count):
                    sig_val_list[i]  = sig_val[i]
                    sig_mask_list[i] = sig_mask[i]
                raise CyStopSimulation()
            if status == 2:
                for i in range(sig_count):
                    sig_val_list[i]  = sig_val[i]
                    sig_mask_list[i] = sig_mask[i]
                raise RuntimeError(
                    "VM Cython interpreter: buffer overflow (NBA/display capacity exceeded). "
                    "Increase NBA_MAX/DISP_BUF_CAP and rebuild."
                )

        # Copy signal state back
        for i in range(sig_count):
            sig_val_list[i]  = sig_val[i]
            sig_mask_list[i] = sig_mask[i]

        return (all_nba, all_dirty)

    finally:
        free(prog_ops)
        free(prog_a1)
        free(sig_val)
        free(sig_mask)
        free(sig_w)
        free(c_val)
        free(c_mask)
        free(c_w)
        free(nba_buf)
        free(dirty_buf)


# ══════════════════════════════════════════════════════════════════════
# Persistent context — zero-allocation hot loop
# ══════════════════════════════════════════════════════════════════════

DEF MAX_PROCS       = 128     # max compiled processes
DEF MAX_PROG_INSTRS = 16384   # total across all programs

cdef class CyContext:
    """Persistent C-native simulation context.

    Owns all signal/const/program data in C arrays allocated once at
    setup.  The ``execute_procs`` method runs programs *in-place* — no
    Python-list copies, no malloc/free per call.
    """

    # ── C-level fields ───────────────────────────────────────────
    cdef long long *sig_val
    cdef long long *sig_mask
    cdef int       *sig_width
    cdef int        sig_count

    cdef long long *const_val
    cdef long long *const_mask
    cdef int       *const_width
    cdef int        const_count

    # Flattened programs: all instructions concatenated
    cdef int       *all_ops       # opcode array
    cdef int       *all_a1        # arg1 array
    # Per-program offset/length into all_ops / all_a1
    cdef int       *prog_offset   # [proc_idx] → start index in all_ops
    cdef int       *prog_length   # [proc_idx] → instruction count
    cdef int        n_procs

    # NBA output buffer
    cdef NBAEntry  *nba_buf
    cdef int        nba_cap
    cdef int        nba_count

    # Memory NBA output buffer
    cdef NBAMemEntry *nba_mem_buf
    cdef int        nba_mem_cap

    # Display output buffer (flat long long array)
    cdef long long *disp_buf
    cdef int        disp_pos       # current write position
    cdef int        disp_cap

    # Dirty output buffer (signal IDs changed by blocking assigns)
    cdef int       *dirty_buf
    cdef int        dirty_cap
    cdef int        dirty_count

    # Simulation time
    cdef long long  sim_time

    # ── Delta-loop data (populated by setup_processes) ───────
    cdef char      *proc_is_combo
    cdef char      *proc_is_seq
    cdef int       *sens_offset       # [sig_count + 1] CSR offsets
    cdef int       *sens_procs        # [total_entries] proc indices
    cdef int       *cont_indices      # [cont_count]
    cdef int        cont_count
    cdef int       *cont_sens_offset  # [cont_count + 1]
    cdef int       *cont_sens_sigs    # [total_entries]
    cdef int       *edge_offset       # [n_procs + 1]
    cdef int       *edge_sigs         # [total_entries]
    cdef int       *edge_types        # [total_entries]
    cdef long long *snap_val          # [sig_count]
    cdef long long *snap_mask         # [sig_count]
    cdef char      *seq_fired         # [n_procs]
    cdef char      *is_changed        # [sig_count]
    cdef char      *is_work           # [sig_count] current-pass CA input sigs
    cdef int       *changed_buf       # [sig_count]
    cdef char      *trig_flag         # [n_procs]
    cdef int       *triggered_buf     # [n_procs]
    cdef public bint _procs_setup

    # Memory arrays
    cdef long long *mem_val
    cdef long long *mem_mask
    cdef int       *mem_elem_width
    cdef int       *mem_depth
    cdef int       *mem_base
    cdef int        mem_count
    cdef int        mem_total_cells   # total flat cells
    cdef bint       _mem_allocated

    # Wide signal support (optional; NULL = no wide signals)
    cdef unsigned long long *wide_sig_val     # flat pool: sig_words words per wide signal
    cdef unsigned long long *wide_sig_mask
    cdef int               *wide_sig_offset   # [sig_count]: pool word offset; -1 = narrow
    cdef unsigned long long *wide_const_val
    cdef unsigned long long *wide_const_mask
    cdef int               *wide_const_offset # [const_count]: pool word offset; -1 = narrow
    cdef unsigned long long *wide_nba_val     # [wide_nba_cap * WIDE_WORDS]
    cdef unsigned long long *wide_nba_mask
    cdef int               *wide_nba_sids     # [wide_nba_cap]
    cdef int                wide_nba_count
    cdef int                wide_nba_cap
    # Wide partial NBA buffer (NBA_BIT / NBA_RANGE on wide signals)
    cdef int               *wide_part_nba_sids  # [WIDE_PART_NBA_MAX]
    cdef int               *wide_part_nba_lsb   # [WIDE_PART_NBA_MAX]
    cdef int               *wide_part_nba_n     # [WIDE_PART_NBA_MAX]
    cdef unsigned long long *wide_part_nba_val  # [WIDE_PART_NBA_MAX * WIDE_WORDS]
    cdef unsigned long long *wide_part_nba_mask
    cdef int                wide_part_nba_count
    cdef WideCtx            wctx_c            # always valid; NULL-pool fields = no wide signals
    cdef bint               _wide_allocated

    # Whether memory is owned (for dealloc)
    cdef bint       _allocated

    def __cinit__(self):
        self.sig_val = NULL
        self.sig_mask = NULL
        self.sig_width = NULL
        self.const_val = NULL
        self.const_mask = NULL
        self.const_width = NULL
        self.all_ops = NULL
        self.all_a1 = NULL
        self.prog_offset = NULL
        self.prog_length = NULL
        self.nba_buf = NULL
        self.nba_mem_buf = NULL
        self.disp_buf = NULL
        self.disp_pos = 0
        self.dirty_buf = NULL
        self.proc_is_combo = NULL
        self.proc_is_seq = NULL
        self.sens_offset = NULL
        self.sens_procs = NULL
        self.cont_indices = NULL
        self.cont_count = 0
        self.cont_sens_offset = NULL
        self.cont_sens_sigs = NULL
        self.edge_offset = NULL
        self.edge_sigs = NULL
        self.edge_types = NULL
        self.snap_val = NULL
        self.snap_mask = NULL
        self.seq_fired = NULL
        self.is_changed = NULL
        self.is_work = NULL
        self.changed_buf = NULL
        self.trig_flag = NULL
        self.triggered_buf = NULL
        self._procs_setup = False
        self._allocated = False
        self.mem_val = NULL
        self.mem_mask = NULL
        self.mem_elem_width = NULL
        self.mem_depth = NULL
        self.mem_base = NULL
        self.mem_count = 0
        self.mem_total_cells = 0
        self._mem_allocated = False
        self.wide_sig_val = NULL
        self.wide_sig_mask = NULL
        self.wide_sig_offset = NULL
        self.wide_const_val = NULL
        self.wide_const_mask = NULL
        self.wide_const_offset = NULL
        self.wide_nba_val = NULL
        self.wide_nba_mask = NULL
        self.wide_nba_sids = NULL
        self.wide_nba_count = 0
        self.wide_nba_cap = 0
        self.wide_part_nba_sids = NULL
        self.wide_part_nba_lsb = NULL
        self.wide_part_nba_n = NULL
        self.wide_part_nba_val = NULL
        self.wide_part_nba_mask = NULL
        self.wide_part_nba_count = 0
        self.wctx_c.sig_val = NULL
        self.wctx_c.sig_mask = NULL
        self.wctx_c.sig_offset = NULL
        self.wctx_c.const_val = NULL
        self.wctx_c.const_mask = NULL
        self.wctx_c.const_offset = NULL
        self.wctx_c.nba_sids = NULL
        self.wctx_c.nba_val = NULL
        self.wctx_c.nba_mask = NULL
        self.wctx_c.nba_count = NULL
        self.wctx_c.nba_cap = 0
        self.wctx_c.nba_part_sids = NULL
        self.wctx_c.nba_part_lsb = NULL
        self.wctx_c.nba_part_n = NULL
        self.wctx_c.nba_part_val = NULL
        self.wctx_c.nba_part_mask = NULL
        self.wctx_c.nba_part_count = NULL
        self.wctx_c.nba_part_cap = 0
        self._wide_allocated = False

    def __dealloc__(self):
        if self._allocated:
            free(self.sig_val)
            free(self.sig_mask)
            free(self.sig_width)
            free(self.const_val)
            free(self.const_mask)
            free(self.const_width)
            free(self.all_ops)
            free(self.all_a1)
            free(self.prog_offset)
            free(self.prog_length)
            free(self.nba_buf)
            free(self.nba_mem_buf)
            free(self.disp_buf)
            free(self.dirty_buf)
        if self._procs_setup:
            free(self.proc_is_combo)
            free(self.proc_is_seq)
            free(self.sens_offset)
            free(self.sens_procs)
            free(self.cont_indices)
            free(self.cont_sens_offset)
            free(self.cont_sens_sigs)
            free(self.edge_offset)
            free(self.edge_sigs)
            free(self.edge_types)
            free(self.snap_val)
            free(self.snap_mask)
            free(self.seq_fired)
            free(self.is_changed)
            free(self.is_work)
            free(self.changed_buf)
            free(self.trig_flag)
            free(self.triggered_buf)
        if self._mem_allocated:
            free(self.mem_val)
            free(self.mem_mask)
            free(self.mem_elem_width)
            free(self.mem_depth)
            free(self.mem_base)
        if self._wide_allocated:
            free(self.wide_sig_val)
            free(self.wide_sig_mask)
            free(self.wide_sig_offset)
            free(self.wide_const_val)
            free(self.wide_const_mask)
            free(self.wide_const_offset)
            free(self.wide_nba_val)
            free(self.wide_nba_mask)
            free(self.wide_nba_sids)
            free(self.wide_part_nba_sids)
            free(self.wide_part_nba_lsb)
            free(self.wide_part_nba_n)
            free(self.wide_part_nba_val)
            free(self.wide_part_nba_mask)

    def setup(
        self,
        list sig_val_list,
        list sig_mask_list,
        list sig_width_list,
        list const_val_list,
        list const_mask_list,
        list const_width_list,
        list programs,                 # list of list[(op, arg1, arg2)]
    ):
        """Allocate C arrays and copy initial data.  Called once."""
        cdef int i, j, offset
        cdef int total_instrs

        self.sig_count = len(sig_val_list)
        self.const_count = len(const_val_list)
        self.n_procs = len(programs)
        self.sim_time = 0

        # ── Signal arrays ──
        self.sig_val   = <long long *>malloc(self.sig_count * sizeof(long long))
        self.sig_mask  = <long long *>malloc(self.sig_count * sizeof(long long))
        self.sig_width = <int *>malloc(self.sig_count * sizeof(int))
        for i in range(self.sig_count):
            self.sig_val[i]   = <long long>sig_val_list[i]
            self.sig_mask[i]  = <long long>sig_mask_list[i]
            self.sig_width[i] = <int>sig_width_list[i]

        # ── Constant pools ──
        self.const_val   = <long long *>malloc(self.const_count * sizeof(long long))
        self.const_mask  = <long long *>malloc(self.const_count * sizeof(long long))
        self.const_width = <int *>malloc(self.const_count * sizeof(int))
        for i in range(self.const_count):
            self.const_val[i]   = <long long>const_val_list[i]
            self.const_mask[i]  = <long long>const_mask_list[i]
            self.const_width[i] = <int>const_width_list[i]

        # ── Flatten all programs into one contiguous block ──
        total_instrs = 0
        for prog in programs:
            total_instrs += len(prog)

        self.all_ops    = <int *>malloc(total_instrs * sizeof(int))
        self.all_a1     = <int *>malloc(total_instrs * sizeof(int))
        self.prog_offset = <int *>malloc(self.n_procs * sizeof(int))
        self.prog_length = <int *>malloc(self.n_procs * sizeof(int))

        offset = 0
        for j in range(self.n_procs):
            prog = programs[j]
            plen = len(prog)
            self.prog_offset[j] = offset
            self.prog_length[j] = plen
            for i in range(plen):
                t = prog[i]
                self.all_ops[offset + i] = <int>t[0]
                self.all_a1[offset + i]  = <int>t[1]
            offset += plen

        # ── Output buffers ──
        self.nba_cap = NBA_MAX
        self.nba_buf = <NBAEntry *>malloc(self.nba_cap * sizeof(NBAEntry))
        self.nba_count = 0

        self.nba_mem_cap = NBA_MEM_MAX
        self.nba_mem_buf = <NBAMemEntry *>malloc(self.nba_mem_cap * sizeof(NBAMemEntry))

        self.disp_cap = DISP_BUF_CAP
        self.disp_buf = <long long *>malloc(self.disp_cap * sizeof(long long))
        self.disp_pos = 0

        self.dirty_cap = self.sig_count
        self.dirty_buf = <int *>malloc(self.dirty_cap * sizeof(int))
        self.dirty_count = 0

        self._allocated = True

    # ── Memory array setup ───────────────────────────────────────

    def setup_memory(self, list mem_val_list, list mem_mask_list, list mem_info_list):
        """Set up memory arrays (called once after setup).

        Args:
            mem_val_list:  flat list[int] of all memory element values.
            mem_mask_list: flat list[int] of all memory element masks.
            mem_info_list: list of (elem_width, depth, base_addr) per memory.
        """
        cdef int i, total_cells, n_mems

        total_cells = len(mem_val_list)
        n_mems = len(mem_info_list)

        if n_mems == 0:
            return

        self.mem_count = n_mems
        self.mem_total_cells = total_cells

        self.mem_val = <long long *>malloc(total_cells * sizeof(long long))
        self.mem_mask = <long long *>malloc(total_cells * sizeof(long long))
        self.mem_elem_width = <int *>malloc(n_mems * sizeof(int))
        self.mem_depth = <int *>malloc(n_mems * sizeof(int))
        self.mem_base = <int *>malloc(n_mems * sizeof(int))

        for i in range(total_cells):
            self.mem_val[i]  = <long long><unsigned long long>mem_val_list[i]
            self.mem_mask[i] = <long long><unsigned long long>mem_mask_list[i]

        for i in range(n_mems):
            info = mem_info_list[i]
            self.mem_elem_width[i] = <int>info[0]
            self.mem_depth[i] = <int>info[1]
            self.mem_base[i] = <int>info[2]

        self._mem_allocated = True

    def read_mem(self, int mem_id, int addr):
        """Read a memory element from Python. Returns (val, mask)."""
        if self._mem_allocated and mem_id < self.mem_count:
            if 0 <= addr < self.mem_depth[mem_id]:
                flat = self.mem_base[mem_id] + addr
                return (self.mem_val[flat], self.mem_mask[flat])
        return (0, 0)

    def write_mem(self, int mem_id, int addr, long long val, long long mask):
        """Write a memory element from Python."""
        if self._mem_allocated and mem_id < self.mem_count:
            if 0 <= addr < self.mem_depth[mem_id]:
                flat = self.mem_base[mem_id] + addr
                self.mem_val[flat] = val
                self.mem_mask[flat] = mask

    def sync_mem_to_lists(self, list val_list, list mask_list):
        """Copy C memory arrays out to Python lists."""
        cdef int i
        for i in range(self.mem_total_cells):
            val_list[i] = self.mem_val[i]
            mask_list[i] = self.mem_mask[i]

    def sync_mem_from_lists(self, list val_list, list mask_list):
        """Copy Python lists into C memory arrays."""
        cdef int i
        for i in range(self.mem_total_cells):
            self.mem_val[i] = <long long>(<unsigned long long>val_list[i])
            self.mem_mask[i] = <long long>(<unsigned long long>mask_list[i])

    # ── Wide signal setup ────────────────────────────────────────

    def setup_wide(
        self,
        list sig_offsets,    # [sig_count] int: pool word offset; -1 = narrow
        list sig_val_words,  # flat list[int] of unsigned 64-bit words (sig pool)
        list sig_mask_words,
        list const_offsets,  # [const_count] int: pool word offset; -1 = narrow
        list const_val_words,
        list const_mask_words,
    ):
        """Set up wide signal pool.  Called once after setup(), before any execution."""
        cdef int i
        cdef int n_sig_words = len(sig_val_words)
        cdef int n_const_words = len(const_val_words)

        # Allocate per-signal offsets
        self.wide_sig_offset = <int *>malloc(self.sig_count * sizeof(int))
        self.wide_const_offset = <int *>malloc(self.const_count * sizeof(int))
        for i in range(self.sig_count):
            self.wide_sig_offset[i] = <int>sig_offsets[i]
        for i in range(self.const_count):
            self.wide_const_offset[i] = <int>const_offsets[i]

        # Allocate and populate signal word pool
        if n_sig_words > 0:
            self.wide_sig_val  = <unsigned long long *>malloc(n_sig_words * sizeof(unsigned long long))
            self.wide_sig_mask = <unsigned long long *>malloc(n_sig_words * sizeof(unsigned long long))
            for i in range(n_sig_words):
                self.wide_sig_val[i]  = <unsigned long long>sig_val_words[i]
                self.wide_sig_mask[i] = <unsigned long long>sig_mask_words[i]

        # Allocate and populate constant word pool
        if n_const_words > 0:
            self.wide_const_val  = <unsigned long long *>malloc(n_const_words * sizeof(unsigned long long))
            self.wide_const_mask = <unsigned long long *>malloc(n_const_words * sizeof(unsigned long long))
            for i in range(n_const_words):
                self.wide_const_val[i]  = <unsigned long long>const_val_words[i]
                self.wide_const_mask[i] = <unsigned long long>const_mask_words[i]

        # Wide NBA buffer (full-signal replace)
        self.wide_nba_cap = WIDE_NBA_MAX
        self.wide_nba_sids = <int *>malloc(WIDE_NBA_MAX * sizeof(int))
        self.wide_nba_val  = <unsigned long long *>malloc(WIDE_NBA_MAX * WIDE_WORDS * sizeof(unsigned long long))
        self.wide_nba_mask = <unsigned long long *>malloc(WIDE_NBA_MAX * WIDE_WORDS * sizeof(unsigned long long))
        self.wide_nba_count = 0

        # Wide partial NBA buffer (read-modify-write for NBA_BIT / NBA_RANGE)
        self.wide_part_nba_sids = <int *>malloc(WIDE_PART_NBA_MAX * sizeof(int))
        self.wide_part_nba_lsb  = <int *>malloc(WIDE_PART_NBA_MAX * sizeof(int))
        self.wide_part_nba_n    = <int *>malloc(WIDE_PART_NBA_MAX * sizeof(int))
        self.wide_part_nba_val  = <unsigned long long *>malloc(WIDE_PART_NBA_MAX * WIDE_WORDS * sizeof(unsigned long long))
        self.wide_part_nba_mask = <unsigned long long *>malloc(WIDE_PART_NBA_MAX * WIDE_WORDS * sizeof(unsigned long long))
        self.wide_part_nba_count = 0

        # Build the WideCtx struct
        self.wctx_c.sig_val    = self.wide_sig_val
        self.wctx_c.sig_mask   = self.wide_sig_mask
        self.wctx_c.sig_offset = self.wide_sig_offset
        self.wctx_c.const_val    = self.wide_const_val
        self.wctx_c.const_mask   = self.wide_const_mask
        self.wctx_c.const_offset = self.wide_const_offset
        self.wctx_c.nba_sids  = self.wide_nba_sids
        self.wctx_c.nba_val   = self.wide_nba_val
        self.wctx_c.nba_mask  = self.wide_nba_mask
        self.wctx_c.nba_count = &self.wide_nba_count
        self.wctx_c.nba_cap   = self.wide_nba_cap
        self.wctx_c.nba_part_sids  = self.wide_part_nba_sids
        self.wctx_c.nba_part_lsb   = self.wide_part_nba_lsb
        self.wctx_c.nba_part_n     = self.wide_part_nba_n
        self.wctx_c.nba_part_val   = self.wide_part_nba_val
        self.wctx_c.nba_part_mask  = self.wide_part_nba_mask
        self.wctx_c.nba_part_count = &self.wide_part_nba_count
        self.wctx_c.nba_part_cap   = WIDE_PART_NBA_MAX

        self._wide_allocated = True

    def get_wide_signal(self, int sid):
        """Read a wide signal back to Python as (list_of_words_val, list_of_words_mask)."""
        cdef int off, wi
        if not self._wide_allocated or self.wide_sig_offset[sid] < 0:
            v = self.sig_val[sid]; m = self.sig_mask[sid]
            return ([v], [m])
        off = self.wide_sig_offset[sid]
        vl = []; ml = []
        for wi in range(WIDE_WORDS):
            vl.append(self.wide_sig_val[off + wi])
            ml.append(self.wide_sig_mask[off + wi])
        return (vl, ml)

    def write_wide_signal(self, int sid, list val_words, list mask_words):
        """Write a wide signal from Python."""
        cdef int off, wi
        if not self._wide_allocated or self.wide_sig_offset[sid] < 0:
            self.sig_val[sid]  = <long long>val_words[0]
            self.sig_mask[sid] = <long long>mask_words[0]
            return
        off = self.wide_sig_offset[sid]
        for wi in range(WIDE_WORDS):
            self.wide_sig_val[off + wi]  = <unsigned long long>val_words[wi]
            self.wide_sig_mask[off + wi] = <unsigned long long>mask_words[wi]

    def apply_wide_nbas(self):
        """Apply queued wide NBAs to wide signal pool. Returns set of dirty signal IDs."""
        cdef int i, sid, off, wi
        cdef set changed = set()
        if not self._wide_allocated:
            return changed
        for i in range(self.wide_nba_count):
            sid = self.wide_nba_sids[i]
            off = self.wide_sig_offset[sid]
            for wi in range(WIDE_WORDS):
                new_v = self.wide_nba_val[i * WIDE_WORDS + wi]
                new_m = self.wide_nba_mask[i * WIDE_WORDS + wi]
                if self.wide_sig_val[off + wi] != new_v or self.wide_sig_mask[off + wi] != new_m:
                    self.wide_sig_val[off + wi]  = new_v
                    self.wide_sig_mask[off + wi] = new_m
                    changed.add(sid)
        self.wide_nba_count = 0
        return changed

    # ── Core execute: run a list of proc indices ─────────────────

    def execute_procs(self, list proc_indices):
        """Execute programs identified by index.  Accumulates NBAs/dirty.

        Returns (nba_list, dirty_set).
        Raises CyStopSimulation on $finish.
        """
        cdef int j, idx, status
        cdef int off, plen
        cdef int nba_room, dirty_room

        self.nba_count = 0
        self.dirty_count = 0

        for j in range(len(proc_indices)):
            idx = <int>proc_indices[j]

            off  = self.prog_offset[idx]
            plen = self.prog_length[idx]

            nba_room = self.nba_cap - self.nba_count
            dirty_room = self.dirty_cap - self.dirty_count
            if nba_room < 1:
                nba_room = 0
            if dirty_room < 1:
                dirty_room = 0

            self.wide_nba_count = 0  # reset per-proc wide NBA count
            status = _execute_core(
                &self.all_ops[off], &self.all_a1[off], plen,
                self.sig_val, self.sig_mask, self.sig_width, self.sig_count,
                self.const_val, self.const_mask, self.const_width, self.const_count,
                &self.nba_buf[self.nba_count], &nba_room,
                &self.dirty_buf[self.dirty_count], &dirty_room,
                self.sim_time,
                self.mem_val, self.mem_mask, self.mem_elem_width,
                self.mem_depth, self.mem_base, self.mem_count,
                NULL, NULL,
                self.disp_buf, &self.disp_pos, self.disp_cap,
                &self.wctx_c if self._wide_allocated else NULL,
            )

            self.nba_count += nba_room
            self.dirty_count += dirty_room

            if status == 1:
                self._sync_out()
                raise CyStopSimulation()
            if status == 2:
                self._sync_out()
                raise RuntimeError(
                    "VM Cython interpreter: buffer overflow (NBA/display capacity exceeded). "
                    "Increase NBA_MAX/DISP_BUF_CAP and rebuild."
                )

        self._sync_out()
        return self._collect_results()

    cdef void _sync_out(self):
        """Write C signal arrays back to nothing — signals stay in C."""
        pass   # no-op: signals live in our C arrays permanently

    def _collect_results(self):
        """Build Python nba_list / dirty_set from C buffers."""
        cdef int i
        cdef long long rm_
        nba_merged = {}
        for i in range(self.nba_count):
            sid_ = self.nba_buf[i].sig_id
            v_   = self.nba_buf[i].val
            m_   = self.nba_buf[i].mask
            rm_  = self.nba_buf[i].range_mask
            if rm_ == 0:
                nba_merged[sid_] = (v_, m_)
            else:
                cur_v, cur_m = nba_merged.get(sid_, (self.sig_val[sid_], self.sig_mask[sid_]))
                nba_merged[sid_] = ((cur_v & ~rm_) | (v_ & rm_), (cur_m & ~rm_) | (m_ & rm_))
        nba_list = [(sid_, v_, m_) for sid_, (v_, m_) in nba_merged.items()]
        dirty_set = set()
        for i in range(self.dirty_count):
            dirty_set.add(self.dirty_buf[i])
        return (nba_list, dirty_set)

    # ── apply_nbas:  NBA → signal storage (in C) ────────────────

    def apply_nbas(self):
        """Apply queued NBAs to signal storage.  Returns dirty set."""
        cdef int i, sid
        cdef long long v, m, rm, new_v, new_m
        cdef set changed = set()

        for i in range(self.nba_count):
            sid = self.nba_buf[i].sig_id
            v   = self.nba_buf[i].val
            m   = self.nba_buf[i].mask
            rm  = self.nba_buf[i].range_mask
            if rm == 0:
                new_v = v
                new_m = m
            else:
                new_v = (self.sig_val[sid] & ~rm) | (v & rm)
                new_m = (self.sig_mask[sid] & ~rm) | (m & rm)
            if self.sig_val[sid] != new_v or self.sig_mask[sid] != new_m:
                self.sig_val[sid] = new_v
                self.sig_mask[sid] = new_m
                changed.add(sid)

        self.nba_count = 0
        return changed

    # ── Signal access for Python (events / testbench) ────────────

    def write_signal(self, int sid, object val, object mask):
        """Write a signal from Python (drive_signal / testbench events)."""
        cdef int off, wi
        if not (0 <= sid < self.sig_count):
            return
        if self._wide_allocated and self.wide_sig_offset[sid] >= 0:
            off = self.wide_sig_offset[sid]
            for wi in range(WIDE_WORDS):
                self.wide_sig_val[off + wi]  = <unsigned long long>((int(val)  >> (64 * wi)) & 0xFFFFFFFFFFFFFFFF)
                self.wide_sig_mask[off + wi] = <unsigned long long>((int(mask) >> (64 * wi)) & 0xFFFFFFFFFFFFFFFF)
        else:
            self.sig_val[sid]  = <long long>(<unsigned long long>(val  & 0xFFFFFFFFFFFFFFFF))
            self.sig_mask[sid] = <long long>(<unsigned long long>(mask & 0xFFFFFFFFFFFFFFFF))

    def read_signal(self, int sid):
        """Read (val, mask) for a signal."""
        cdef int off, wi
        cdef object acc_v, acc_m
        if not (0 <= sid < self.sig_count):
            return (0, 0)
        if self._wide_allocated and self.wide_sig_offset[sid] >= 0:
            off = self.wide_sig_offset[sid]
            acc_v = 0; acc_m = 0
            for wi in range(WIDE_WORDS):
                acc_v |= (<object>self.wide_sig_val[off + wi]) << (64 * wi)
                acc_m |= (<object>self.wide_sig_mask[off + wi]) << (64 * wi)
            return (acc_v, acc_m)
        return (self.sig_val[sid], self.sig_mask[sid])

    def set_time(self, long long t):
        self.sim_time = t

    # ── Snapshot / edge detection helpers ─────────────────────────

    def snapshot_signals(self):
        """Return (list_val, list_mask) snapshot for edge detection."""
        cdef int i, off, wi
        cdef object acc_v, acc_m
        cdef list v = []
        cdef list m = []
        for i in range(self.sig_count):
            if self._wide_allocated and self.wide_sig_offset[i] >= 0:
                off = self.wide_sig_offset[i]
                acc_v = 0; acc_m = 0
                for wi in range(WIDE_WORDS):
                    acc_v |= (<object>self.wide_sig_val[off + wi]) << (64 * wi)
                    acc_m |= (<object>self.wide_sig_mask[off + wi]) << (64 * wi)
                v.append(acc_v)
                m.append(acc_m)
            else:
                v.append(self.sig_val[i])
                m.append(self.sig_mask[i])
        return (v, m)

    # ── Sync helpers (for Python-side scheduler compat) ──────────

    def sync_signals_from_lists(self, list val_list, list mask_list):
        """Copy Python lists back into C arrays (used after initial blocks)."""
        cdef int i, off, wi
        cdef object pv, pm
        for i in range(self.sig_count):
            pv = val_list[i]
            pm = mask_list[i]
            if self._wide_allocated and self.wide_sig_offset[i] >= 0:
                off = self.wide_sig_offset[i]
                for wi in range(WIDE_WORDS):
                    self.wide_sig_val[off + wi]  = <unsigned long long>((int(pv) >> (64 * wi)) & 0xFFFFFFFFFFFFFFFF)
                    self.wide_sig_mask[off + wi] = <unsigned long long>((int(pm) >> (64 * wi)) & 0xFFFFFFFFFFFFFFFF)
            else:
                # Values may arrive as large unsigned ints (0 to 2^64-1) from
                # the reference executor.  Mask to 64 bits then reinterpret as
                # signed so the C long long array doesn't overflow.
                self.sig_val[i]  = <long long>(<unsigned long long>(pv & 0xFFFFFFFFFFFFFFFF))
                self.sig_mask[i] = <long long>(<unsigned long long>(pm & 0xFFFFFFFFFFFFFFFF))

    def sync_signals_to_lists(self, list val_list, list mask_list):
        """Copy C arrays out to Python lists (used for ref_ctx sync)."""
        cdef int i, off, wi
        cdef object acc_v, acc_m
        for i in range(self.sig_count):
            if self._wide_allocated and self.wide_sig_offset[i] >= 0:
                off = self.wide_sig_offset[i]
                acc_v = 0; acc_m = 0
                for wi in range(WIDE_WORDS):
                    acc_v |= (<object>self.wide_sig_val[off + wi]) << (64 * wi)
                    acc_m |= (<object>self.wide_sig_mask[off + wi]) << (64 * wi)
                val_list[i]  = acc_v
                mask_list[i] = acc_m
            else:
                val_list[i]  = self.sig_val[i]
                mask_list[i] = self.sig_mask[i]

    # ── Delta-loop setup & execution ─────────────────────────────

    def setup_processes(
        self,
        list proc_types,         # [n_procs] int: 0=cont, 1=combo, 2=seq
        list sig_sens_lists,     # [sig_count] list[int] — proc indices sensitive to each sig
        list cont_indices_list,  # list[int] — global proc indices for continuous assigns
        list cont_sens_lists,    # [len(cont_indices)] list[int] — sig IDs each cont proc is sensitive to
        list proc_edge_lists,    # [n_procs] list[(sig_id, edge_type_int)] — 0=posedge, 1=negedge
    ):
        """Build C-level sensitivity / edge / process-type arrays.  Called once."""
        cdef int i, j, n, offset, total

        # ── Process type flags ──
        self.proc_is_combo = <char *>malloc(self.n_procs * sizeof(char))
        self.proc_is_seq   = <char *>malloc(self.n_procs * sizeof(char))
        for i in range(self.n_procs):
            pt = <int>proc_types[i]
            self.proc_is_combo[i] = 1 if pt == 1 else 0
            self.proc_is_seq[i]   = 1 if pt == 2 else 0

        # ── Sensitivity CSR: sig → proc indices ──
        total = 0
        for i in range(self.sig_count):
            total += len(sig_sens_lists[i])
        self.sens_offset = <int *>malloc((self.sig_count + 1) * sizeof(int))
        self.sens_procs  = <int *>malloc(max(total, 1) * sizeof(int))
        offset = 0
        for i in range(self.sig_count):
            self.sens_offset[i] = offset
            lst = sig_sens_lists[i]
            for j in range(len(lst)):
                self.sens_procs[offset] = <int>lst[j]
                offset += 1
        self.sens_offset[self.sig_count] = offset

        # ── Continuous assign indices + sensitivity ──
        self.cont_count = len(cont_indices_list)
        self.cont_indices = <int *>malloc(max(self.cont_count, 1) * sizeof(int))
        for i in range(self.cont_count):
            self.cont_indices[i] = <int>cont_indices_list[i]

        total = 0
        for i in range(self.cont_count):
            total += len(cont_sens_lists[i])
        self.cont_sens_offset = <int *>malloc((self.cont_count + 1) * sizeof(int))
        self.cont_sens_sigs   = <int *>malloc(max(total, 1) * sizeof(int))
        offset = 0
        for i in range(self.cont_count):
            self.cont_sens_offset[i] = offset
            lst = cont_sens_lists[i]
            for j in range(len(lst)):
                self.cont_sens_sigs[offset] = <int>lst[j]
                offset += 1
        self.cont_sens_offset[self.cont_count] = offset

        # ── Edge info CSR: proc → (sig_id, edge_type) ──
        total = 0
        for i in range(self.n_procs):
            total += len(proc_edge_lists[i])
        self.edge_offset = <int *>malloc((self.n_procs + 1) * sizeof(int))
        self.edge_sigs   = <int *>malloc(max(total, 1) * sizeof(int))
        self.edge_types  = <int *>malloc(max(total, 1) * sizeof(int))
        offset = 0
        for i in range(self.n_procs):
            self.edge_offset[i] = offset
            lst = proc_edge_lists[i]
            for j in range(len(lst)):
                pair = lst[j]
                self.edge_sigs[offset]  = <int>pair[0]
                self.edge_types[offset] = <int>pair[1]
                offset += 1
        self.edge_offset[self.n_procs] = offset

        # ── Snapshot arrays ──
        self.snap_val  = <long long *>malloc(self.sig_count * sizeof(long long))
        self.snap_mask = <long long *>malloc(self.sig_count * sizeof(long long))
        for i in range(self.sig_count):
            self.snap_val[i]  = self.sig_val[i]
            self.snap_mask[i] = self.sig_mask[i]

        # ── Working buffers ──
        self.seq_fired    = <char *>malloc(self.n_procs * sizeof(char))
        self.trig_flag    = <char *>malloc(self.n_procs * sizeof(char))
        self.triggered_buf = <int *>malloc(self.n_procs * sizeof(int))
        self.is_changed   = <char *>malloc(self.sig_count * sizeof(char))
        self.is_work      = <char *>malloc(self.sig_count * sizeof(char))
        self.changed_buf  = <int *>malloc(self.sig_count * sizeof(int))
        for i in range(self.n_procs):
            self.seq_fired[i] = 0
            self.trig_flag[i] = 0
        for i in range(self.sig_count):
            self.is_changed[i] = 0
            self.is_work[i] = 0

        self._procs_setup = True

    def take_snapshot(self):
        """Copy current signal values to snapshot arrays for edge detection."""
        cdef int i
        for i in range(self.sig_count):
            self.snap_val[i]  = self.sig_val[i]
            self.snap_mask[i] = self.sig_mask[i]

    def reset_seq_fired(self):
        """Reset per-timestep sequential-process fired flags."""
        cdef int i
        for i in range(self.n_procs):
            self.seq_fired[i] = 0

    def run_delta_loop(self, list changed_sids, int delta_limit):
        """Run the full delta cycle loop in C.

        Args:
            changed_sids: list of signal IDs changed by events.
            delta_limit:  max delta iterations before raising.

        Raises:
            CyStopSimulation: on $finish.
            RuntimeError: on delta limit exceeded.
        """
        cdef DeltaCtx dc
        cdef int changed_count = len(changed_sids)
        cdef int i, status, sid

        # Populate DeltaCtx from self
        dc.sig_val = self.sig_val
        dc.sig_mask = self.sig_mask
        dc.sig_width = self.sig_width
        dc.sig_count = self.sig_count
        dc.const_val = self.const_val
        dc.const_mask = self.const_mask
        dc.const_width = self.const_width
        dc.const_count = self.const_count
        dc.all_ops = self.all_ops
        dc.all_a1 = self.all_a1
        dc.prog_offset = self.prog_offset
        dc.prog_length = self.prog_length
        dc.sens_offset = self.sens_offset
        dc.sens_procs = self.sens_procs
        dc.proc_is_combo = self.proc_is_combo
        dc.proc_is_seq = self.proc_is_seq
        dc.cont_indices = self.cont_indices
        dc.cont_count = self.cont_count
        dc.cont_sens_offset = self.cont_sens_offset
        dc.cont_sens_sigs = self.cont_sens_sigs
        dc.edge_offset = self.edge_offset
        dc.edge_sigs = self.edge_sigs
        dc.edge_types = self.edge_types
        dc.snap_val = self.snap_val
        dc.snap_mask = self.snap_mask
        dc.seq_fired = self.seq_fired
        dc.is_changed = self.is_changed
        dc.is_work = self.is_work
        dc.changed_buf = self.changed_buf
        dc.trig_flag = self.trig_flag
        dc.triggered_buf = self.triggered_buf
        dc.nba_buf = self.nba_buf
        dc.nba_cap = self.nba_cap
        dc.dirty_buf = self.dirty_buf
        dc.dirty_cap = self.dirty_cap
        dc.nba_mem_buf = self.nba_mem_buf
        dc.nba_mem_cap = self.nba_mem_cap
        dc.disp_buf = self.disp_buf
        dc.disp_pos = &self.disp_pos
        dc.disp_cap = self.disp_cap
        dc.delta_limit = delta_limit
        dc.sim_time = self.sim_time
        dc.mem_val = self.mem_val
        dc.mem_mask = self.mem_mask
        dc.mem_elem_width = self.mem_elem_width
        dc.mem_depth = self.mem_depth
        dc.mem_base = self.mem_base
        dc.mem_count = self.mem_count
        self.wide_nba_count = 0           # reset before delta loop
        self.wide_part_nba_count = 0      # reset before delta loop
        dc.wctx = self.wctx_c   # copied by value; nba_count/nba_part_count ptrs stay valid

        # Populate the initial changed set
        for i in range(changed_count):
            sid = <int>changed_sids[i]
            self.changed_buf[i] = sid
            self.is_changed[sid] = 1

        # Run entirely in C (no GIL needed)
        with nogil:
            status = _run_delta_loop_core(&dc, &changed_count)

        if status == 1:
            raise CyStopSimulation()
        if status == 2:
            raise RuntimeError(
                "VM Cython interpreter: buffer overflow (NBA/display capacity exceeded). "
                "Increase NBA_MAX/DISP_BUF_CAP and rebuild."
            )
        if status == -1:
            raise RuntimeError(f"Delta cycle limit ({delta_limit}) exceeded")

    def drain_display_buffer(self):
        """Read display events from the C buffer and return as list of tuples.

        Each tuple is (fmt_id, is_monitor, [(val, mask, width), ...]).
        The caller (scheduler) is responsible for formatting using _format_display.
        Resets the buffer position to 0 after draining.
        """
        cdef int pos = 0
        cdef int fmt_id, n_args, is_monitor
        cdef int k
        events = []
        while pos < self.disp_pos:
            fmt_id = <int>self.disp_buf[pos]
            n_args = <int>self.disp_buf[pos + 1]
            is_monitor = <int>self.disp_buf[pos + 2]
            pos += 3
            args = []
            for k in range(n_args):
                args.append((self.disp_buf[pos], self.disp_buf[pos + 1], <int>self.disp_buf[pos + 2]))
                pos += 3
            events.append((fmt_id, is_monitor, args))
        self.disp_pos = 0
        return events
