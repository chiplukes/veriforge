cdef inline long long wmask(int w) noexcept nogil:
    if w >= 64:
        return -1
    return (1LL << w) - 1

cdef inline unsigned long long _word_mask64(int w) noexcept nogil:
    if w >= 64:
        return <unsigned long long>-1
    return ((<unsigned long long>1) << w) - 1

cdef inline unsigned long long _sig_word_val(SimCtx *c, int sid, int word_index) noexcept nogil:
    if c.wide_words[sid] > 0:
        if 0 <= word_index < c.wide_words[sid]:
            return c.wide_val[c.wide_offset[sid] + word_index]
        return 0
    if word_index == 0:
        return <unsigned long long>c.val[sid] & _word_mask64(c.width[sid])
    return 0

cdef inline unsigned long long _sig_word_mask(SimCtx *c, int sid, int word_index) noexcept nogil:
    if c.wide_words[sid] > 0:
        if 0 <= word_index < c.wide_words[sid]:
            return c.wide_mask[c.wide_offset[sid] + word_index]
        return 0
    if word_index == 0:
        return <unsigned long long>c.mask[sid] & _word_mask64(c.width[sid])
    return 0

cdef inline bint _sig_has_unknown(SimCtx *c, int sid) noexcept nogil:
    cdef int word_count = c.wide_words[sid]
    cdef int i
    if word_count > 0:
        for i in range(word_count):
            if c.wide_mask[c.wide_offset[sid] + i] != 0:
                return True
        return False
    return (c.mask[sid] & _word_mask64(c.width[sid])) != 0

cdef inline unsigned long long _sig_extract_word_val(SimCtx *c, int sid, int lsb) noexcept nogil:
    cdef int src_word = lsb >> 6
    cdef int src_shift = lsb & 63
    cdef unsigned long long lo_v = _sig_word_val(c, sid, src_word)
    cdef unsigned long long hi_v
    if src_shift == 0:
        return lo_v
    hi_v = _sig_word_val(c, sid, src_word + 1)
    return (lo_v >> src_shift) | (hi_v << (64 - src_shift))

cdef inline unsigned long long _sig_extract_word_mask(SimCtx *c, int sid, int lsb) noexcept nogil:
    cdef int src_word = lsb >> 6
    cdef int src_shift = lsb & 63
    cdef unsigned long long lo_m = _sig_word_mask(c, sid, src_word)
    cdef unsigned long long hi_m
    if src_shift == 0:
        return lo_m
    hi_m = _sig_word_mask(c, sid, src_word + 1)
    return (lo_m >> src_shift) | (hi_m << (64 - src_shift))

cdef inline unsigned long long _sig_extract_word_val_sv(long long *sv, long long *sm, SimCtx *c, int sid, int lsb) noexcept nogil:
    cdef int src_word = lsb >> 6
    cdef int src_shift = lsb & 63
    cdef unsigned long long lo_v
    cdef unsigned long long hi_v
    if c.wide_words[sid] > 0:
        # Wide signal: sv[] only holds lower 64 bits; use original helper
        return _sig_extract_word_val(c, sid, lsb)
    # Narrow signal: read from pre-posedge snapshot
    lo_v = <unsigned long long>sv[sid] & _word_mask64(c.width[sid])
    if src_shift == 0:
        return lo_v
    return lo_v >> src_shift

cdef inline unsigned long long _sig_extract_word_mask_sv(long long *sm, SimCtx *c, int sid, int lsb) noexcept nogil:
    cdef int src_shift = lsb & 63
    cdef unsigned long long lo_m
    if c.wide_words[sid] > 0:
        return _sig_extract_word_mask(c, sid, lsb)
    lo_m = <unsigned long long>sm[sid] & _word_mask64(c.width[sid])
    if src_shift == 0:
        return lo_m
    return lo_m >> src_shift
