cdef inline void _whole_assign_slice_const_signal(SimCtx *c, int dst_sid, int src_sid, int lsb) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, src_word, src_shift, remaining_w, changed = 0
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_word = (lsb + (i * 64)) >> 6
            src_shift = (lsb + (i * 64)) & 63
            lo_v = _sig_word_val(c, src_sid, src_word)
            lo_m = _sig_word_mask(c, src_sid, src_word)
            if src_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                hi_v = _sig_word_val(c, src_sid, src_word + 1)
                hi_m = _sig_word_mask(c, src_sid, src_word + 1)
                out_v = (lo_v >> src_shift) | (hi_v << (64 - src_shift))
                out_m = (lo_m >> src_shift) | (hi_m << (64 - src_shift))
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        src_word = lsb >> 6
        src_shift = lsb & 63
        lo_v = _sig_word_val(c, src_sid, src_word)
        lo_m = _sig_word_mask(c, src_sid, src_word)
        if src_shift == 0:
            out_v = lo_v
            out_m = lo_m
        else:
            hi_v = _sig_word_val(c, src_sid, src_word + 1)
            hi_m = _sig_word_mask(c, src_sid, src_word + 1)
            out_v = (lo_v >> src_shift) | (hi_v << (64 - src_shift))
            out_m = (lo_m >> src_shift) | (hi_m << (64 - src_shift))
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(out_v & tail_mask)
        new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline long long _sign_ext(long long v, int w) noexcept nogil:
    if w >= 64:
        return v
    if v & (1LL << (w - 1)):
        return v | (~((1LL << w) - 1))
    return v

cdef inline int _xor_reduce(long long v, int w) noexcept nogil:
    cdef int i, result = 0
    for i in range(w):
        result ^= <int>((v >> i) & 1)
    return result

cdef inline int _clog2(long long v) noexcept nogil:
    cdef int r = 0
    if v <= 1:
        return 0
    v -= 1
    while v > 0:
        v >>= 1
        r += 1
    return r

cdef inline void _out_char(SimCtx *c, char ch) noexcept nogil:
    if c.out_count < OUT_BUF_MAX - 1:
        c.out_buf[c.out_count] = ch
        c.out_count += 1

cdef inline void _out_newline(SimCtx *c) noexcept nogil:
    _out_char(c, 10)  # newline

cdef inline void _out_int_dec(SimCtx *c, long long v) noexcept nogil:
    cdef char buf[24]
    cdef int n, i
    n = snprintf(buf, 24, "%lld", v)
    for i in range(n):
        _out_char(c, buf[i])

cdef inline void _out_int_hex(SimCtx *c, long long v) noexcept nogil:
    cdef char buf[24]
    cdef int n, i
    n = snprintf(buf, 24, "%llx", v)
    for i in range(n):
        _out_char(c, buf[i])

cdef inline void _out_int_oct(SimCtx *c, long long v) noexcept nogil:
    cdef char buf[24]
    cdef int n, i
    n = snprintf(buf, 24, "%llo", v)
    for i in range(n):
        _out_char(c, buf[i])

cdef inline void _out_int_bin(SimCtx *c, long long v, int w) noexcept nogil:
    cdef int i
    for i in range(w - 1, -1, -1):
        if (v >> i) & 1:
            _out_char(c, 49)  # '1'
        else:
            _out_char(c, 48)  # '0'

cdef inline void _out_int_dec_w(SimCtx *c, long long v, int w, int zp) noexcept nogil:
    cdef char buf[24]
    cdef int n, i, pad_len
    n = snprintf(buf, 24, "%lld", v)
    pad_len = w - n if w > n else 0
    cdef char fill = 48 if zp else 32  # '0' or ' '
    for i in range(pad_len):
        _out_char(c, fill)
    for i in range(n):
        _out_char(c, buf[i])

cdef inline void _out_int_hex_w(SimCtx *c, long long v, int w, int zp) noexcept nogil:
    cdef char buf[24]
    cdef int n, i, pad_len
    n = snprintf(buf, 24, "%llx", v)
    pad_len = w - n if w > n else 0
    cdef char fill = 48 if zp else 32  # '0' or ' '
    for i in range(pad_len):
        _out_char(c, fill)
    for i in range(n):
        _out_char(c, buf[i])

cdef inline void _out_int_oct_w(SimCtx *c, long long v, int w, int zp) noexcept nogil:
    cdef char buf[24]
    cdef int n, i, pad_len
    n = snprintf(buf, 24, "%llo", v)
    pad_len = w - n if w > n else 0
    cdef char fill = 48 if zp else 32  # '0' or ' '
    for i in range(pad_len):
        _out_char(c, fill)
    for i in range(n):
        _out_char(c, buf[i])
