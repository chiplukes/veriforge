
cdef inline void _whole_assign_sar_signal(SimCtx *c, int dst_sid, int src_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef int sign_index = c.width[src_sid] - 1
    cdef int sign_word_index = sign_index >> 6
    cdef int sign_bit_index = sign_index & 63
    cdef unsigned long long sign_word_v = _sig_word_val(c, src_sid, sign_word_index)
    cdef unsigned long long sign_word_m = _sig_word_mask(c, src_sid, sign_word_index)
    cdef unsigned long long sign_bit_v = (sign_word_v >> sign_bit_index) & 1
    cdef unsigned long long sign_bit_m = (sign_word_m >> sign_bit_index) & 1
    cdef int fill_start = c.width[src_sid] - shift
    cdef int fill_lo, fill_hi, local_lo, local_hi
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, fill_mask
    cdef int i, src_index, remaining_w, changed = 0
    cdef long long new_v, new_m
    if fill_start < 0:
        fill_start = 0
    if dst_words > 0:
        for i in range(dst_words):
            src_index = i + word_shift
            lo_v = _sig_word_val(c, src_sid, src_index)
            lo_m = _sig_word_mask(c, src_sid, src_index)
            if bit_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                hi_v = _sig_word_val(c, src_sid, src_index + 1)
                hi_m = _sig_word_mask(c, src_sid, src_index + 1)
                out_v = (lo_v >> bit_shift) | (hi_v << (64 - bit_shift))
                out_m = (lo_m >> bit_shift) | (hi_m << (64 - bit_shift))
            fill_lo = max(i * 64, fill_start)
            fill_hi = min((i * 64) + 63, c.width[dst_sid] - 1)
            if fill_lo <= fill_hi:
                local_lo = fill_lo - (i * 64)
                local_hi = fill_hi - (i * 64)
                fill_mask = _word_mask64(local_hi + 1) & ~_word_mask64(local_lo)
                if sign_bit_m != 0:
                    out_m |= fill_mask
                elif sign_bit_v != 0:
                    out_v |= fill_mask
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
        if shift >= 64:
            if sign_bit_m != 0:
                new_v = 0
                new_m = <long long>_word_mask64(c.width[dst_sid])
            elif sign_bit_v != 0:
                new_v = <long long>_word_mask64(c.width[dst_sid])
                new_m = 0
            else:
                new_v = 0
                new_m = 0
        else:
            tail_mask = _word_mask64(c.width[dst_sid])
            out_v = _sig_word_val(c, src_sid, 0) >> shift
            out_m = _sig_word_mask(c, src_sid, 0) >> shift
            if c.width[dst_sid] > shift:
                fill_mask = tail_mask & ~_word_mask64(c.width[dst_sid] - shift)
                if sign_bit_m != 0:
                    out_m |= fill_mask
                elif sign_bit_v != 0:
                    out_v |= fill_mask
            new_v = <long long>(out_v & tail_mask)
            new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1


cdef inline void _whole_assign_ternary_shl_signal(SimCtx *c, int dst_sid, int cond_sid, int true_sid, int false_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, src_index, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long cond_v = <unsigned long long>c.val[cond_sid]
    cdef unsigned long long cond_m = <unsigned long long>c.mask[cond_sid]
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, agree
    if cond_m == 0:
        if cond_v != 0:
            _whole_assign_shl_signal(c, dst_sid, true_sid, shift)
        else:
            _whole_assign_shl_signal(c, dst_sid, false_sid, shift)
        return
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_index = i - word_shift
            if src_index < 0:
                out_v = 0
                out_m = 0
            else:
                lo_v = _sig_word_val(c, true_sid, src_index)
                lo_m = _sig_word_mask(c, true_sid, src_index)
                agree = ~(lo_v ^ _sig_word_val(c, false_sid, src_index)) & ~(lo_m ^ _sig_word_mask(c, false_sid, src_index))
                lo_m = ~agree
                lo_v &= _sig_word_val(c, false_sid, src_index) & ~lo_m
                if bit_shift == 0:
                    out_v = lo_v
                    out_m = lo_m
                else:
                    hi_v = _sig_word_val(c, true_sid, src_index - 1)
                    hi_m = _sig_word_mask(c, true_sid, src_index - 1)
                    agree = ~(hi_v ^ _sig_word_val(c, false_sid, src_index - 1)) & ~(hi_m ^ _sig_word_mask(c, false_sid, src_index - 1))
                    hi_m = ~agree
                    hi_v &= _sig_word_val(c, false_sid, src_index - 1) & ~hi_m
                    out_v = (lo_v << bit_shift) | (hi_v >> (64 - bit_shift))
                    out_m = (lo_m << bit_shift) | (hi_m >> (64 - bit_shift))
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
        new_v = 0
        new_m = 0
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_ternary_shr_signal(SimCtx *c, int dst_sid, int cond_sid, int true_sid, int false_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, src_index, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long cond_v = <unsigned long long>c.val[cond_sid]
    cdef unsigned long long cond_m = <unsigned long long>c.mask[cond_sid]
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, agree
    if cond_m == 0:
        if cond_v != 0:
            _whole_assign_shr_signal(c, dst_sid, true_sid, shift)
        else:
            _whole_assign_shr_signal(c, dst_sid, false_sid, shift)
        return
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_index = i + word_shift
            lo_v = _sig_word_val(c, true_sid, src_index)
            lo_m = _sig_word_mask(c, true_sid, src_index)
            agree = ~(lo_v ^ _sig_word_val(c, false_sid, src_index)) & ~(lo_m ^ _sig_word_mask(c, false_sid, src_index))
            lo_m = ~agree
            lo_v &= _sig_word_val(c, false_sid, src_index) & ~lo_m
            if bit_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                hi_v = _sig_word_val(c, true_sid, src_index + 1)
                hi_m = _sig_word_mask(c, true_sid, src_index + 1)
                agree = ~(hi_v ^ _sig_word_val(c, false_sid, src_index + 1)) & ~(hi_m ^ _sig_word_mask(c, false_sid, src_index + 1))
                hi_m = ~agree
                hi_v &= _sig_word_val(c, false_sid, src_index + 1) & ~hi_m
                out_v = (lo_v >> bit_shift) | (hi_v << (64 - bit_shift))
                out_m = (lo_m >> bit_shift) | (hi_m << (64 - bit_shift))
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
        new_v = 0
        new_m = 0
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_shl_signal(SimCtx *c, int dst_sid, int src_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[src_sid] if c.wide_words[src_sid] > 0 else 1
    cdef int i, src_index, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_index = i - word_shift
            if src_index < 0:
                out_v = 0
                out_m = 0
            else:
                lo_v = _sig_word_val(c, src_sid, src_index)
                lo_m = _sig_word_mask(c, src_sid, src_index)
                if bit_shift == 0:
                    out_v = lo_v
                    out_m = lo_m
                else:
                    hi_v = _sig_word_val(c, src_sid, src_index - 1)
                    hi_m = _sig_word_mask(c, src_sid, src_index - 1)
                    out_v = (lo_v << bit_shift) | (hi_v >> (64 - bit_shift))
                    out_m = (lo_m << bit_shift) | (hi_m >> (64 - bit_shift))
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
        if shift >= 64:
            out_v = 0
            out_m = 0
        else:
            out_v = _sig_word_val(c, src_sid, 0) << shift
            out_m = _sig_word_mask(c, src_sid, 0) << shift
            tail_mask = _word_mask64(c.width[dst_sid])
            new_v = <long long>(out_v & tail_mask)
            new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_shr_signal(SimCtx *c, int dst_sid, int src_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[src_sid] if c.wide_words[src_sid] > 0 else 1
    cdef int i, src_index, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_index = i + word_shift
            lo_v = _sig_word_val(c, src_sid, src_index)
            lo_m = _sig_word_mask(c, src_sid, src_index)
            if bit_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                hi_v = _sig_word_val(c, src_sid, src_index + 1)
                hi_m = _sig_word_mask(c, src_sid, src_index + 1)
                out_v = (lo_v >> bit_shift) | (hi_v << (64 - bit_shift))
                out_m = (lo_m >> bit_shift) | (hi_m << (64 - bit_shift))
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
        src_index = word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            lo_v = _sig_word_val(c, src_sid, src_index)
            lo_m = _sig_word_mask(c, src_sid, src_index)
            if bit_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                if src_index + 1 < src_words:
                    hi_v = _sig_word_val(c, src_sid, src_index + 1)
                    hi_m = _sig_word_mask(c, src_sid, src_index + 1)
                else:
                    hi_v = 0
                    hi_m = 0
                out_v = (lo_v >> bit_shift) | (hi_v << (64 - bit_shift))
                out_m = (lo_m >> bit_shift) | (hi_m << (64 - bit_shift))
            tail_mask = _word_mask64(c.width[dst_sid])
            new_v = <long long>(out_v & tail_mask)
            new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_and_const(SimCtx *c, int dst_sid, int lhs_sid, unsigned long long rhs_const) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, rv, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rv = rhs_const if i == 0 else 0
            out_v = lv & rv
            out_m = lm & rv
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
        lv = _sig_word_val(c, lhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv & rhs_const) & tail_mask)
        new_m = <long long>((lm & rhs_const) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_const(SimCtx *c, int dst_sid, int lhs_sid, unsigned long long rhs_const) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, rv, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rv = rhs_const if i == 0 else 0
            out_v = lv | rv
            out_m = lm & ~rv
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
        lv = _sig_word_val(c, lhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv | rhs_const) & tail_mask)
        new_m = <long long>((lm & ~rhs_const) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const(SimCtx *c, int dst_sid, int lhs_sid, unsigned long long rhs_const) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, rv, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rv = rhs_const if i == 0 else 0
            out_v = lv ^ rv
            out_m = lm
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
        lv = _sig_word_val(c, lhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv ^ rhs_const) & tail_mask)
        new_m = <long long>((lm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_or_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv & cv
            left_m = lm & cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv & rhs_const) | rv) & tail_mask)
        new_m = <long long>((((lm & rhs_const) | rm) & ~((lv & rhs_const) & ~(lm & rhs_const)) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_and_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv & cv
            left_m = lm & cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv & rhs_const) & rv) & tail_mask)
        new_m = <long long>((((lm & rhs_const) | rm) & ~(~(lv & rhs_const) & ~(lm & rhs_const)) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_xor_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv & cv
            left_m = lm & cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv & rhs_const) ^ rv) & tail_mask)
        new_m = <long long>(((lm & rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_mask_xor_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv | cv
            left_m = lm & ~cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv | rhs_const) ^ rv) & tail_mask)
        new_m = <long long>(((lm & ~rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_add_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv & cv
            left_m = lm & cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            sum1 = left_v + rv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < left_v:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv & rhs_const) + rv) & tail_mask)
        new_m = <long long>(((lm & rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_or_mask(SimCtx *c, int dst_sid, int sub_sid, int mix_sid, unsigned long long rhs_const) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, tv, tm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            tv = _sig_word_val(c, mix_sid, i)
            tm = _sig_word_mask(c, mix_sid, i)
            cv = rhs_const if i == 0 else 0
            right_v = tv | cv
            right_m = tm & ~cv
            tmp = right_v + <unsigned long long>borrow_in
            out_v = sv - tmp
            borrow_out = 0
            if tmp < right_v:
                borrow_out = 1
            elif sv < tmp:
                borrow_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        tv = _sig_word_val(c, mix_sid, 0)
        tm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (tv | rhs_const)) & tail_mask))
        new_m = <long long>((sm | (tm & ~rhs_const)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_mask_add_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv | cv
            left_m = lm & ~cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            sum1 = left_v + rv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < left_v:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv | rhs_const) + rv) & tail_mask)
        new_m = <long long>(((lm & ~rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_sub_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv & cv
            left_m = lm & cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            tmp = rv + <unsigned long long>borrow_in
            out_v = left_v - tmp
            borrow_out = 0
            if tmp < rv:
                borrow_out = 1
            elif left_v < tmp:
                borrow_out = 1
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv & rhs_const) - rv) & tail_mask)
        new_m = <long long>(((lm & rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_mask_sub_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv | cv
            left_m = lm & ~cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            tmp = rv + <unsigned long long>borrow_in
            out_v = left_v - tmp
            borrow_out = 0
            if tmp < rv:
                borrow_out = 1
            elif left_v < tmp:
                borrow_out = 1
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv | rhs_const) - rv) & tail_mask)
        new_m = <long long>(((lm & ~rhs_const) | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_mask(SimCtx *c, int dst_sid, int sub_sid, int mix_sid, unsigned long long rhs_const) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, tv, tm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            tv = _sig_word_val(c, mix_sid, i)
            tm = _sig_word_mask(c, mix_sid, i)
            cv = rhs_const if i == 0 else 0
            right_v = tv & cv
            right_m = tm & cv
            tmp = right_v + <unsigned long long>borrow_in
            out_v = sv - tmp
            borrow_out = 0
            if tmp < right_v:
                borrow_out = 1
            elif sv < tmp:
                borrow_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        tv = _sig_word_val(c, mix_sid, 0)
        tm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (tv & rhs_const)) & tail_mask))
        new_m = <long long>((sm | (tm & rhs_const)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_and_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            left_v = sv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif sv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - const_word) & rv) & tail_mask)
        new_m = <long long>(((sm | rm) & ~(~(sv - const_word) & ~sm) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_or_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            left_v = sv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif sv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - const_word) | rv) & tail_mask)
        new_m = <long long>(((sm | rm) & ~((sv - const_word) & ~sm) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_and_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = sv + <unsigned long long>borrow1_in
            left_v = cv - tmp
            borrow1_out = 0
            if tmp < sv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((const_word - sv) & rv) & tail_mask)
        new_m = <long long>(((sm | rm) & ~(~(const_word - sv) & ~sm) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_or_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = sv + <unsigned long long>borrow1_in
            left_v = cv - tmp
            borrow1_out = 0
            if tmp < sv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((const_word - sv) | rv) & tail_mask)
        new_m = <long long>(((sm | rm) & ~((const_word - sv) & ~sm) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_mask_and_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv | cv
            left_m = lm & ~cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv | rhs_const) & rv) & tail_mask)
        new_m = <long long>((((lm & ~rhs_const) | rm) & ~(~(lv | rhs_const) & ~(lm & ~rhs_const)) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_mask_or_signal(SimCtx *c, int dst_sid, int left_sid, unsigned long long rhs_const, int right_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, left_sid, i)
            lm = _sig_word_mask(c, left_sid, i)
            cv = rhs_const if i == 0 else 0
            left_v = lv | cv
            left_m = lm & ~cv
            rv = _sig_word_val(c, right_sid, i)
            rm = _sig_word_mask(c, right_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
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
        lv = _sig_word_val(c, left_sid, 0)
        lm = _sig_word_mask(c, left_sid, 0)
        rv = _sig_word_val(c, right_sid, 0)
        rm = _sig_word_mask(c, right_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((lv | rhs_const) | rv) & tail_mask)
        new_m = <long long>((((lm & ~rhs_const) | rm) & ~((lv | rhs_const) & ~(lm & ~rhs_const)) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_add_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long av, am, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry1_in = 0
    cdef int carry1_out
    cdef unsigned long long rv, rm
    cdef int carry2_in = 0
    cdef int carry2_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            av = _sig_word_val(c, add_sid, i)
            am = _sig_word_mask(c, add_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = av + cv
            sum2 = sum1 + <unsigned long long>carry1_in
            carry1_out = 0
            if sum1 < av:
                carry1_out = 1
            if sum2 < sum1:
                carry1_out = 1
            left_v = sum2
            left_m = am
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            sum1 = left_v + rv
            sum2 = sum1 + <unsigned long long>carry2_in
            carry2_out = 0
            if sum1 < left_v:
                carry2_out = 1
            if sum2 < sum1:
                carry2_out = 1
            out_v = sum2
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry1_in = carry1_out
            carry2_in = carry2_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        av = _sig_word_val(c, add_sid, 0)
        am = _sig_word_mask(c, add_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((av + const_word) + rv) & tail_mask)
        new_m = <long long>((am | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_sub_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long av, am, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry1_in = 0
    cdef int carry1_out
    cdef unsigned long long rv, rm
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            av = _sig_word_val(c, add_sid, i)
            am = _sig_word_mask(c, add_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = av + cv
            sum2 = sum1 + <unsigned long long>carry1_in
            carry1_out = 0
            if sum1 < av:
                carry1_out = 1
            if sum2 < sum1:
                carry1_out = 1
            left_v = sum2
            left_m = am
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            tmp = rv + <unsigned long long>borrow_in
            out_v = left_v - tmp
            borrow_out = 0
            if tmp < rv:
                borrow_out = 1
            elif left_v < tmp:
                borrow_out = 1
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry1_in = carry1_out
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        av = _sig_word_val(c, add_sid, 0)
        am = _sig_word_mask(c, add_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((av + const_word) - rv) & tail_mask)
        new_m = <long long>((am | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_add_const(SimCtx *c, int dst_sid, int sub_sid, int mix_sid, unsigned long long const_word) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, mv, mm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2, tmp
    cdef int carry_in = 0
    cdef int carry_out
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            mv = _sig_word_val(c, mix_sid, i)
            mm = _sig_word_mask(c, mix_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = mv + cv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < mv:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            right_v = sum2
            right_m = mm
            tmp = right_v + <unsigned long long>borrow_in
            out_v = sv - tmp
            borrow_out = 0
            if tmp < right_v:
                borrow_out = 1
            elif sv < tmp:
                borrow_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        mv = _sig_word_val(c, mix_sid, 0)
        mm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (mv + const_word))) & tail_mask)
        new_m = <long long>((sm | mm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_xor_const(SimCtx *c, int dst_sid, int sub_sid, int mix_sid, unsigned long long const_word) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, mv, mm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            mv = _sig_word_val(c, mix_sid, i)
            mm = _sig_word_mask(c, mix_sid, i)
            cv = const_word if i == 0 else 0
            right_v = mv ^ cv
            right_m = mm
            tmp = right_v + <unsigned long long>borrow_in
            out_v = sv - tmp
            borrow_out = 0
            if tmp < right_v:
                borrow_out = 1
            elif sv < tmp:
                borrow_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        mv = _sig_word_val(c, mix_sid, 0)
        mm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (mv ^ const_word))) & tail_mask)
        new_m = <long long>((sm | mm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_add_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            left_v = sv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif sv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            sum1 = left_v + rv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < left_v:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - const_word) + rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_add_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = sv + <unsigned long long>borrow1_in
            left_v = cv - tmp
            borrow1_out = 0
            if tmp < sv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            sum1 = left_v + rv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < left_v:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((const_word - sv) + rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_sub_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef int borrow2_in = 0
    cdef int borrow2_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = sv + <unsigned long long>borrow1_in
            left_v = cv - tmp
            borrow1_out = 0
            if tmp < sv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            tmp = rv + <unsigned long long>borrow2_in
            out_v = left_v - tmp
            borrow2_out = 0
            if tmp < rv:
                borrow2_out = 1
            elif left_v < tmp:
                borrow2_out = 1
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            borrow2_in = borrow2_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((const_word - sv) - rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_sub_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef int borrow2_in = 0
    cdef int borrow2_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            left_v = sv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif sv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            tmp = rv + <unsigned long long>borrow2_in
            out_v = left_v - tmp
            borrow2_out = 0
            if tmp < rv:
                borrow2_out = 1
            elif left_v < tmp:
                borrow2_out = 1
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            borrow2_in = borrow2_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - const_word) - rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_sub_const(SimCtx *c, int dst_sid, int sub_sid, int mix_sid, unsigned long long const_word) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, mv, mm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef int borrow2_in = 0
    cdef int borrow2_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            mv = _sig_word_val(c, mix_sid, i)
            mm = _sig_word_mask(c, mix_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            right_v = mv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif mv < tmp:
                borrow1_out = 1
            right_m = mm
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            tmp = right_v + <unsigned long long>borrow2_in
            out_v = sv - tmp
            borrow2_out = 0
            if tmp < right_v:
                borrow2_out = 1
            elif sv < tmp:
                borrow2_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            borrow2_in = borrow2_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        mv = _sig_word_val(c, mix_sid, 0)
        mm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (mv - const_word)) & tail_mask))
        new_m = <long long>((sm | mm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_const_sub(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int mix_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, mv, mm, cv, right_v, right_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef int borrow2_in = 0
    cdef int borrow2_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            mv = _sig_word_val(c, mix_sid, i)
            mm = _sig_word_mask(c, mix_sid, i)
            cv = const_word if i == 0 else 0
            tmp = mv + <unsigned long long>borrow1_in
            right_v = cv - tmp
            borrow1_out = 0
            if tmp < mv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            right_m = mm
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            tmp = right_v + <unsigned long long>borrow2_in
            out_v = sv - tmp
            borrow2_out = 0
            if tmp < right_v:
                borrow2_out = 1
            elif sv < tmp:
                borrow2_out = 1
            out_m = sm | right_m
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
            borrow2_in = borrow2_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        mv = _sig_word_val(c, mix_sid, 0)
        mm = _sig_word_mask(c, mix_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - (const_word - mv)) & tail_mask))
        new_m = <long long>((sm | mm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_sub_signal(SimCtx *c, int dst_sid, int xor_sid, unsigned long long const_word, int sub_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long xv, xm, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sv, sm
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            xv = _sig_word_val(c, xor_sid, i)
            xm = _sig_word_mask(c, xor_sid, i)
            cv = const_word if i == 0 else 0
            left_v = xv ^ cv
            left_m = xm
            sv, sm = _sig_word_val(c, sub_sid, i), _sig_word_mask(c, sub_sid, i)
            tmp = sv + <unsigned long long>borrow_in
            out_v = left_v - tmp
            borrow_out = 0
            if tmp < sv:
                borrow_out = 1
            elif left_v < tmp:
                borrow_out = 1
            out_m = left_m | sm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        xv = _sig_word_val(c, xor_sid, 0)
        xm = _sig_word_mask(c, xor_sid, 0)
        sv, sm = _sig_word_val(c, sub_sid, 0), _sig_word_mask(c, sub_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((xv ^ const_word) - sv) & tail_mask)
        new_m = <long long>((xm | sm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_xor_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow1_in
            left_v = sv - tmp
            borrow1_out = 0
            if tmp < cv:
                borrow1_out = 1
            elif sv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((sv - const_word) ^ rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_xor_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long sv, sm, cv, rv, rm, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow1_in = 0
    cdef int borrow1_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            sv = _sig_word_val(c, sub_sid, i)
            sm = _sig_word_mask(c, sub_sid, i)
            cv = const_word if i == 0 else 0
            tmp = sv + <unsigned long long>borrow1_in
            left_v = cv - tmp
            borrow1_out = 0
            if tmp < sv:
                borrow1_out = 1
            elif cv < tmp:
                borrow1_out = 1
            left_m = sm
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow1_in = borrow1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        sv = _sig_word_val(c, sub_sid, 0)
        sm = _sig_word_mask(c, sub_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((const_word - sv) ^ rv) & tail_mask)
        new_m = <long long>((sm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_add_signal(SimCtx *c, int dst_sid, int xor_sid, unsigned long long const_word, int add_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long xv, xm, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long av, am
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            xv = _sig_word_val(c, xor_sid, i)
            xm = _sig_word_mask(c, xor_sid, i)
            cv = const_word if i == 0 else 0
            left_v = xv ^ cv
            left_m = xm
            av, am = _sig_word_val(c, add_sid, i), _sig_word_mask(c, add_sid, i)
            sum1 = left_v + av
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < left_v:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = left_m | am
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        xv = _sig_word_val(c, xor_sid, 0)
        xm = _sig_word_mask(c, xor_sid, 0)
        av, am = _sig_word_val(c, add_sid, 0), _sig_word_mask(c, add_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((xv ^ const_word) + av) & tail_mask)
        new_m = <long long>((xm | am) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_and_signal(SimCtx *c, int dst_sid, int xor_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long xv, xm, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            xv = _sig_word_val(c, xor_sid, i)
            xm = _sig_word_mask(c, xor_sid, i)
            cv = const_word if i == 0 else 0
            left_v = xv ^ cv
            left_m = xm
            rv, rm = _sig_word_val(c, rhs_sid, i), _sig_word_mask(c, rhs_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
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
        xv = _sig_word_val(c, xor_sid, 0)
        xm = _sig_word_mask(c, xor_sid, 0)
        rv, rm = _sig_word_val(c, rhs_sid, 0), _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((xv ^ const_word) & rv) & tail_mask)
        new_m = <long long>(((xm | rm) & ~(~(xv ^ const_word) & ~xm) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_or_signal(SimCtx *c, int dst_sid, int xor_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long xv, xm, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            xv = _sig_word_val(c, xor_sid, i)
            xm = _sig_word_mask(c, xor_sid, i)
            cv = const_word if i == 0 else 0
            left_v = xv ^ cv
            left_m = xm
            rv, rm = _sig_word_val(c, rhs_sid, i), _sig_word_mask(c, rhs_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
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
        xv = _sig_word_val(c, xor_sid, 0)
        xm = _sig_word_mask(c, xor_sid, 0)
        rv, rm = _sig_word_val(c, rhs_sid, 0), _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((xv ^ const_word) | rv) & tail_mask)
        new_m = <long long>(((xm | rm) & ~((xv ^ const_word) & ~xm) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_xor_signal(SimCtx *c, int dst_sid, int xor_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long xv, xm, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            xv = _sig_word_val(c, xor_sid, i)
            xm = _sig_word_mask(c, xor_sid, i)
            cv = const_word if i == 0 else 0
            left_v = xv ^ cv
            left_m = xm
            rv, rm = _sig_word_val(c, rhs_sid, i), _sig_word_mask(c, rhs_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
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
        xv = _sig_word_val(c, xor_sid, 0)
        xm = _sig_word_mask(c, xor_sid, 0)
        rv, rm = _sig_word_val(c, rhs_sid, 0), _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((xv ^ const_word) ^ rv) & tail_mask)
        new_m = <long long>((xm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_and_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long av, am, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry1_in = 0
    cdef int carry1_out
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            av = _sig_word_val(c, add_sid, i)
            am = _sig_word_mask(c, add_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = av + cv
            sum2 = sum1 + <unsigned long long>carry1_in
            carry1_out = 0
            if sum1 < av:
                carry1_out = 1
            if sum2 < sum1:
                carry1_out = 1
            left_v = sum2
            left_m = am
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v & rv
            out_m = (left_m | rm) & ~(~left_v & ~left_m) & ~(~rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry1_in = carry1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        av = _sig_word_val(c, add_sid, 0)
        am = _sig_word_mask(c, add_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((av + const_word) & rv) & tail_mask)
        new_m = <long long>(((am | rm) & ~(~(av + const_word) & ~am) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_or_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long av, am, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry1_in = 0
    cdef int carry1_out
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            av = _sig_word_val(c, add_sid, i)
            am = _sig_word_mask(c, add_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = av + cv
            sum2 = sum1 + <unsigned long long>carry1_in
            carry1_out = 0
            if sum1 < av:
                carry1_out = 1
            if sum2 < sum1:
                carry1_out = 1
            left_v = sum2
            left_m = am
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v | rv
            out_m = (left_m | rm) & ~(left_v & ~left_m) & ~(rv & ~rm)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry1_in = carry1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        av = _sig_word_val(c, add_sid, 0)
        am = _sig_word_mask(c, add_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((av + const_word) | rv) & tail_mask)
        new_m = <long long>(((am | rm) & ~((av + const_word) & ~am) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_xor_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long av, am, cv, left_v, left_m, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry1_in = 0
    cdef int carry1_out
    cdef unsigned long long rv, rm
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            av = _sig_word_val(c, add_sid, i)
            am = _sig_word_mask(c, add_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = av + cv
            sum2 = sum1 + <unsigned long long>carry1_in
            carry1_out = 0
            if sum1 < av:
                carry1_out = 1
            if sum2 < sum1:
                carry1_out = 1
            left_v = sum2
            left_m = am
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = left_v ^ rv
            out_m = left_m | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry1_in = carry1_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        av = _sig_word_val(c, add_sid, 0)
        am = _sig_word_mask(c, add_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(((av + const_word) ^ rv) & tail_mask)
        new_m = <long long>((am | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const(SimCtx *c, int dst_sid, int lhs_sid, unsigned long long const_word) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2, tmp
    cdef int carry_in = 0
    cdef int carry_out
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            cv = const_word if i == 0 else 0
            sum1 = lv + cv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < lv:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = lm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, lhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv + const_word) & tail_mask)
        new_m = <long long>(lm & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1


cdef inline void _whole_assign_mask_shl(SimCtx *c, int dst_sid, int mask_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mask_sid] if c.wide_words[mask_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                mv = _sig_word_val(c, mask_sid, curr_index)
                mm = _sig_word_mask(c, mask_sid, curr_index)
                rv = rhs_const if curr_index == 0 else 0
                curr_v = mv & rv
                curr_m = mm & rv
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mask_shr(SimCtx *c, int dst_sid, int mask_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mask_sid] if c.wide_words[mask_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        mv = _sig_word_val(c, mask_sid, next_index)
        mm = _sig_word_mask(c, mask_sid, next_index)
        rv = rhs_const if next_index == 0 else 0
        next_v = mv & rv
        next_m = mm & rv
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    mv = _sig_word_val(c, mask_sid, next_index)
                    mm = _sig_word_mask(c, mask_sid, next_index)
                    rv = rhs_const if next_index == 0 else 0
                    next_v = mv & rv
                    next_m = mm & rv
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_const_shl(SimCtx *c, int dst_sid, int signal_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                mv = _sig_word_val(c, signal_sid, curr_index)
                mm = _sig_word_mask(c, signal_sid, curr_index)
                rv = rhs_const if curr_index == 0 else 0
                curr_v = mv | rv
                curr_m = mm & (~rv)
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_const_shr(SimCtx *c, int dst_sid, int signal_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        mv = _sig_word_val(c, signal_sid, next_index)
        mm = _sig_word_mask(c, signal_sid, next_index)
        rv = rhs_const if next_index == 0 else 0
        next_v = mv | rv
        next_m = mm & (~rv)
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    mv = _sig_word_val(c, signal_sid, next_index)
                    mm = _sig_word_mask(c, signal_sid, next_index)
                    rv = rhs_const if next_index == 0 else 0
                    next_v = mv | rv
                    next_m = mm & (~rv)
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_shl(SimCtx *c, int dst_sid, int signal_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                mv = _sig_word_val(c, signal_sid, curr_index)
                mm = _sig_word_mask(c, signal_sid, curr_index)
                rv = rhs_const if curr_index == 0 else 0
                curr_v = mv ^ rv
                curr_m = mm
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_const_shr(SimCtx *c, int dst_sid, int signal_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, rv, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        mv = _sig_word_val(c, signal_sid, next_index)
        mm = _sig_word_mask(c, signal_sid, next_index)
        rv = rhs_const if next_index == 0 else 0
        next_v = mv ^ rv
        next_m = mm
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    mv = _sig_word_val(c, signal_sid, next_index)
                    mm = _sig_word_mask(c, signal_sid, next_index)
                    rv = rhs_const if next_index == 0 else 0
                    next_v = mv ^ rv
                    next_m = mm
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_not_shl(SimCtx *c, int dst_sid, int signal_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long mv, mm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                mv = _sig_word_val(c, signal_sid, curr_index)
                mm = _sig_word_mask(c, signal_sid, curr_index)
                curr_v = ~mv
                curr_m = mm
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_not_shr(SimCtx *c, int dst_sid, int signal_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, src_remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, src_tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            lo_v = _sig_word_val(c, signal_sid, src_index)
            lo_m = _sig_word_mask(c, signal_sid, src_index)
            src_remaining_w = c.width[signal_sid] - (src_index * 64)
            src_tail_mask = _word_mask64(src_remaining_w)
            lo_v = (~lo_v) & src_tail_mask
            lo_m &= src_tail_mask
            if bit_shift == 0:
                out_v = lo_v
                out_m = lo_m
            else:
                if src_index + 1 < src_words:
                    hi_v = _sig_word_val(c, signal_sid, src_index + 1)
                    hi_m = _sig_word_mask(c, signal_sid, src_index + 1)
                    src_remaining_w = c.width[signal_sid] - ((src_index + 1) * 64)
                    src_tail_mask = _word_mask64(src_remaining_w)
                    hi_v = (~hi_v) & src_tail_mask
                    hi_m &= src_tail_mask
                else:
                    hi_v = 0
                    hi_m = 0
                out_v = (lo_v >> bit_shift) | (hi_v << (64 - bit_shift))
                out_m = (lo_m >> bit_shift) | (hi_m << (64 - bit_shift))
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_lnot_shl(SimCtx *c, int dst_sid, int signal_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, changed = 0
    cdef unsigned long long word_v, word_m, known_one = 0, any_unknown = 0, base_v = 0, base_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1):
        word_v = _sig_word_val(c, signal_sid, i)
        word_m = _sig_word_mask(c, signal_sid, i)
        if (word_v & ~word_m) != 0:
            known_one = 1
            break
        if word_m != 0:
            any_unknown = 1
    if known_one == 0:
        if any_unknown != 0:
            base_m = 1
        else:
            base_v = 1
    if shift != 0:
        base_v = 0
        base_m = 0
    for i in range(out_words):
        out_v = base_v if i == 0 else 0
        out_m = base_m if i == 0 else 0
        tail_mask = _word_mask64(c.width[dst_sid] - (i * 64))
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_x_signal_shl(SimCtx *c, int dst_sid, int src_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[src_sid] if c.wide_words[src_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long curr_m = 0, prev_m = 0, out_m, tail_mask
    cdef unsigned long long out_v = 0
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_m = curr_m
                curr_m = _word_mask64(c.width[src_sid] - (curr_index * 64))
            if bit_shift == 0:
                out_m = curr_m
            else:
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_m &= tail_mask
        if dst_words > 0:
            if out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = 0
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            elif c.wide_val[c.wide_offset[dst_sid] + i] != 0:
                c.wide_val[c.wide_offset[dst_sid] + i] = 0
                changed = 1
        else:
            new_v = 0
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_x_signal_shr(SimCtx *c, int dst_sid, int src_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[src_sid] if c.wide_words[src_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long curr_m = 0, next_m = 0, out_m, tail_mask
    cdef unsigned long long out_v = 0
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        next_m = _word_mask64(c.width[src_sid] - (next_index * 64))
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_m = next_m
                if next_index < src_words:
                    next_m = _word_mask64(c.width[src_sid] - (next_index * 64))
                else:
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_m = curr_m
            else:
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_m &= tail_mask
        if dst_words > 0:
            if out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = 0
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            elif c.wide_val[c.wide_offset[dst_sid] + i] != 0:
                c.wide_val[c.wide_offset[dst_sid] + i] = 0
                changed = 1
        else:
            new_v = 0
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_neg_shl(SimCtx *c, int dst_sid, int signal_sid, int shift) noexcept nogil:
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int i
    cdef unsigned long long word_m, src_tail_mask
    for i in range(src_words):
        src_tail_mask = _word_mask64(c.width[signal_sid] - (i * 64))
        word_m = _sig_word_mask(c, signal_sid, i) & src_tail_mask
        if word_m != 0:
            _whole_assign_x_signal_shl(c, dst_sid, signal_sid, shift)
            return
    _whole_assign_const_sub_shl(c, dst_sid, (<unsigned long long>0), signal_sid, shift)

cdef inline void _whole_assign_neg_shr(SimCtx *c, int dst_sid, int signal_sid, int shift) noexcept nogil:
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int i
    cdef unsigned long long word_m, src_tail_mask
    for i in range(src_words):
        src_tail_mask = _word_mask64(c.width[signal_sid] - (i * 64))
        word_m = _sig_word_mask(c, signal_sid, i) & src_tail_mask
        if word_m != 0:
            _whole_assign_x_signal_shr(c, dst_sid, signal_sid, shift)
            return
    _whole_assign_const_sub_shr(c, dst_sid, (<unsigned long long>0), signal_sid, shift)

cdef inline void _whole_assign_all_x(SimCtx *c, int dst_sid):
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        if dst_words > 0:
            if c.wide_val[c.wide_offset[dst_sid] + i] != 0 or c.wide_mask[c.wide_offset[dst_sid] + i] != tail_mask:
                c.wide_val[c.wide_offset[dst_sid] + i] = 0
                c.wide_mask[c.wide_offset[dst_sid] + i] = tail_mask
                changed = 1
        else:
            new_v = 0
            new_m = <long long>tail_mask
    if dst_words > 0:
        new_v = 0
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_py_value(SimCtx *c, int dst_sid, object value):
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long out_v, tail_mask
    cdef long long new_v = 0
    for i in range(out_words):
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v = <unsigned long long>(((value >> (i * 64))) & tail_mask)
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or c.wide_mask[c.wide_offset[dst_sid] + i] != 0:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = 0
                changed = 1
        else:
            new_v = <long long>out_v
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or c.mask[dst_sid] != 0:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = 0
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_py_bits(SimCtx *c, int dst_sid, object value, object mask):
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v = <unsigned long long>(((value >> (i * 64))) & tail_mask)
        out_m = <unsigned long long>(((mask >> (i * 64))) & tail_mask)
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sar_sub_const_xor_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long sub_const, int sub_const_width, int xor_sid, int shift):
    cdef int sub_width = c.width[sub_sid] if c.width[sub_sid] >= sub_const_width else sub_const_width
    cdef int result_width = sub_width if sub_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object sub_mask, width_mask, dst_mask, sub_val, xor_raw_val, xor_raw_mask, diff_val, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if _signal_has_x(c, sub_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    if fill_start < 0:
        fill_start = 0
    sub_mask = (((<object>1) << sub_width)) - 1
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    sub_val = _sig_py_unsigned(c, sub_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    diff_val = (sub_val - sub_const) & sub_mask
    xor_val = (diff_val ^ xor_raw_val) & width_mask
    xor_mask = xor_raw_mask & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_const_sub_xor_signal(SimCtx *c, int dst_sid, int sub_sid, unsigned long long sub_const, int sub_const_width, int xor_sid, int shift):
    cdef int sub_width = c.width[sub_sid] if c.width[sub_sid] >= sub_const_width else sub_const_width
    cdef int result_width = sub_width if sub_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object sub_mask, width_mask, dst_mask, sub_val, xor_raw_val, xor_raw_mask, diff_val, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if _signal_has_x(c, sub_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    if fill_start < 0:
        fill_start = 0
    sub_mask = (((<object>1) << sub_width)) - 1
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    sub_val = _sig_py_unsigned(c, sub_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    diff_val = (sub_const - sub_val) & sub_mask
    xor_val = (diff_val ^ xor_raw_val) & width_mask
    xor_mask = xor_raw_mask & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_mask_xor_signal(SimCtx *c, int dst_sid, int mask_sid, unsigned long long mask_const, int mask_const_width, int xor_sid, int shift):
    cdef int result_width = c.width[mask_sid] if c.width[mask_sid] >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, mask_width_mask, mask_val, mask_bits, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    mask_width_mask = (((<object>1) << mask_const_width)) - 1
    mask_val = _sig_py_unsigned(c, mask_sid)
    mask_bits = (<object>mask_const) & mask_width_mask
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = mask_val & mask_bits
    left_mask = _sig_py_mask(c, mask_sid) & mask_bits
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_or_const_xor_signal(SimCtx *c, int dst_sid, int or_sid, unsigned long long or_const, int or_const_width, int xor_sid, int shift):
    cdef int result_width = c.width[or_sid] if c.width[or_sid] >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, or_width_mask, or_val, or_bits, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    or_width_mask = (((<object>1) << or_const_width)) - 1
    or_val = _sig_py_unsigned(c, or_sid)
    or_bits = (<object>or_const) & or_width_mask
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = (or_val | or_bits) & width_mask
    left_mask = _sig_py_mask(c, or_sid) & (width_mask ^ or_bits)
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_xor_const_xor_signal(SimCtx *c, int dst_sid, int xor1_sid, unsigned long long xor_const, int xor_const_width, int xor_sid, int shift):
    cdef int result_width = c.width[xor1_sid] if c.width[xor1_sid] >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, xor_width_mask, xor1_val, xor_bits, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    xor_width_mask = (((<object>1) << xor_const_width)) - 1
    xor1_val = _sig_py_unsigned(c, xor1_sid)
    xor_bits = (<object>xor_const) & xor_width_mask
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = (xor1_val ^ xor_bits) & width_mask
    left_mask = _sig_py_mask(c, xor1_sid) & width_mask
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_add_const_xor_signal(SimCtx *c, int dst_sid, int add_sid, unsigned long long add_const, int add_const_width, int xor_sid, int shift):
    cdef int add_width = c.width[add_sid] if c.width[add_sid] >= add_const_width else add_const_width
    cdef int result_width = add_width if add_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object add_mask, width_mask, dst_mask, add_val, xor_raw_val, xor_raw_mask, sum_val, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if _signal_has_x(c, add_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    if fill_start < 0:
        fill_start = 0
    add_mask = (((<object>1) << add_width)) - 1
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    add_val = _sig_py_unsigned(c, add_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    sum_val = (add_val + add_const) & add_mask
    xor_val = (sum_val ^ xor_raw_val) & width_mask
    xor_mask = xor_raw_mask & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_xor_xor_signal(SimCtx *c, int dst_sid, int xor1_sid, int xor2_sid, int xor_sid, int shift):
    cdef int xor_width = c.width[xor1_sid] if c.width[xor1_sid] >= c.width[xor2_sid] else c.width[xor2_sid]
    cdef int result_width = xor_width if xor_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, xor1_val, xor2_val, xor1_mask, xor2_mask, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    xor1_val = _sig_py_unsigned(c, xor1_sid)
    xor2_val = _sig_py_unsigned(c, xor2_sid)
    xor1_mask = _sig_py_mask(c, xor1_sid)
    xor2_mask = _sig_py_mask(c, xor2_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = (xor1_val ^ xor2_val) & width_mask
    left_mask = (xor1_mask | xor2_mask) & width_mask
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_or_xor_signal(SimCtx *c, int dst_sid, int or1_sid, int or2_sid, int xor_sid, int shift):
    cdef int or_width = c.width[or1_sid] if c.width[or1_sid] >= c.width[or2_sid] else c.width[or2_sid]
    cdef int result_width = or_width if or_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, or1_val, or2_val, or1_mask, or2_mask, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    or1_val = _sig_py_unsigned(c, or1_sid)
    or2_val = _sig_py_unsigned(c, or2_sid)
    or1_mask = _sig_py_mask(c, or1_sid)
    or2_mask = _sig_py_mask(c, or2_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = (or1_val | or2_val) & width_mask
    left_mask = ((or1_mask | or2_mask) & ~(or1_val & ~or1_mask) & ~(or2_val & ~or2_mask)) & width_mask
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_and_xor_signal(SimCtx *c, int dst_sid, int and1_sid, int and2_sid, int xor_sid, int shift):
    cdef int and_width = c.width[and1_sid] if c.width[and1_sid] >= c.width[and2_sid] else c.width[and2_sid]
    cdef int result_width = and_width if and_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, and1_val, and2_val, and1_mask, and2_mask, xor_raw_val, xor_raw_mask, left_val, left_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    and1_val = _sig_py_unsigned(c, and1_sid)
    and2_val = _sig_py_unsigned(c, and2_sid)
    and1_mask = _sig_py_mask(c, and1_sid)
    and2_mask = _sig_py_mask(c, and2_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    left_val = (and1_val & and2_val) & width_mask
    left_mask = ((and1_mask | and2_mask) & ~(~and1_val & ~and1_mask) & ~(~and2_val & ~and2_mask)) & width_mask
    xor_val = (left_val ^ xor_raw_val) & width_mask
    xor_mask = (left_mask | xor_raw_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_add_xor_signal(SimCtx *c, int dst_sid, int add1_sid, int add2_sid, int xor_sid, int shift):
    cdef int add_width = c.width[add1_sid] if c.width[add1_sid] >= c.width[add2_sid] else c.width[add2_sid]
    cdef int result_width = add_width if add_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object add_mask, width_mask, dst_mask, add1_val, add2_val, xor_raw_val, xor_raw_mask, sum_val, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if _signal_has_x(c, add1_sid) or _signal_has_x(c, add2_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    if fill_start < 0:
        fill_start = 0
    add_mask = (((<object>1) << add_width)) - 1
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    add1_val = _sig_py_unsigned(c, add1_sid)
    add2_val = _sig_py_unsigned(c, add2_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    sum_val = (add1_val + add2_val) & add_mask
    xor_val = (sum_val ^ xor_raw_val) & width_mask
    xor_mask = xor_raw_mask & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_sub_xor_signal(SimCtx *c, int dst_sid, int sub1_sid, int sub2_sid, int xor_sid, int shift):
    cdef int sub_width = c.width[sub1_sid] if c.width[sub1_sid] >= c.width[sub2_sid] else c.width[sub2_sid]
    cdef int result_width = sub_width if sub_width >= c.width[xor_sid] else c.width[xor_sid]
    cdef int fill_start = result_width - shift
    cdef object sub_mask, width_mask, dst_mask, sub1_val, sub2_val, xor_raw_val, xor_raw_mask, diff_val, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if _signal_has_x(c, sub1_sid) or _signal_has_x(c, sub2_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    if fill_start < 0:
        fill_start = 0
    sub_mask = (((<object>1) << sub_width)) - 1
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    sub1_val = _sig_py_unsigned(c, sub1_sid)
    sub2_val = _sig_py_unsigned(c, sub2_sid)
    xor_raw_val = _sig_py_unsigned(c, xor_sid)
    xor_raw_mask = _sig_py_mask(c, xor_sid)
    diff_val = (sub1_val - sub2_val) & sub_mask
    xor_val = (diff_val ^ xor_raw_val) & width_mask
    xor_mask = xor_raw_mask & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_sar_add_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef object lhs_val, rhs_val, sum_val, sign_bit
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    sum_val = (lhs_val + rhs_val) & ((((<object>1) << result_width)) - 1)
    sign_bit = (<object>1) << (result_width - 1)
    if sum_val & sign_bit:
        sum_val -= (<object>1) << result_width
    _whole_assign_py_value(c, dst_sid, sum_val >> shift)

cdef inline void _whole_assign_sar_sub_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef object lhs_val, rhs_val, diff_val, sign_bit
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_all_x(c, dst_sid)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    diff_val = (lhs_val - rhs_val) & ((((<object>1) << result_width)) - 1)
    sign_bit = (<object>1) << (result_width - 1)
    if diff_val & sign_bit:
        diff_val -= (<object>1) << result_width
    _whole_assign_py_value(c, dst_sid, diff_val >> shift)

cdef inline void _whole_assign_sar_and_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, lhs_val, rhs_val, lhs_mask, rhs_mask, and_val, and_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    lhs_mask = _sig_py_mask(c, lhs_sid)
    rhs_mask = _sig_py_mask(c, rhs_sid)
    and_val = (lhs_val & rhs_val) & width_mask
    and_mask = ((lhs_mask | rhs_mask) & ~(~lhs_val & ~lhs_mask) & ~(~rhs_val & ~rhs_mask)) & width_mask
    if and_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = and_val & sign_bit
    sign_is_unknown = and_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    and_val = and_val >> shift
    and_mask = and_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            and_mask |= fill_mask
        elif sign_is_one:
            and_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, and_val, and_mask)

cdef inline void _whole_assign_sar_or_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, lhs_val, rhs_val, lhs_mask, rhs_mask, or_val, or_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    lhs_mask = _sig_py_mask(c, lhs_sid)
    rhs_mask = _sig_py_mask(c, rhs_sid)
    or_val = (lhs_val | rhs_val) & width_mask
    or_mask = ((lhs_mask | rhs_mask) & ~(lhs_val & ~lhs_mask) & ~(rhs_val & ~rhs_mask)) & width_mask
    if or_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = or_val & sign_bit
    sign_is_unknown = or_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    or_val = or_val >> shift
    or_mask = or_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            or_mask |= fill_mask
        elif sign_is_one:
            or_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, or_val, or_mask)

cdef inline void _whole_assign_sar_xor_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int fill_start = result_width - shift
    cdef object width_mask, dst_mask, lhs_val, rhs_val, lhs_mask, rhs_mask, xor_val, xor_mask, sign_bit, sign_is_one, sign_is_unknown, fill_mask
    if fill_start < 0:
        fill_start = 0
    width_mask = (((<object>1) << result_width)) - 1
    dst_mask = (((<object>1) << c.width[dst_sid])) - 1
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    lhs_mask = _sig_py_mask(c, lhs_sid)
    rhs_mask = _sig_py_mask(c, rhs_sid)
    xor_val = (lhs_val ^ rhs_val) & width_mask
    xor_mask = (lhs_mask | rhs_mask) & width_mask
    if xor_mask != 0:
        _whole_assign_all_x(c, dst_sid)
        return
    sign_bit = (<object>1) << (result_width - 1)
    sign_is_one = xor_val & sign_bit
    sign_is_unknown = xor_mask & sign_bit
    fill_mask = 0
    if fill_start < c.width[dst_sid]:
        fill_mask = dst_mask ^ ((((<object>1) << fill_start)) - 1)
    xor_val = xor_val >> shift
    xor_mask = xor_mask >> shift
    if fill_mask != 0:
        if sign_is_unknown:
            xor_mask |= fill_mask
        elif sign_is_one:
            xor_val |= fill_mask
    _whole_assign_py_bits(c, dst_sid, xor_val, xor_mask)

cdef inline void _whole_assign_div_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_sid = lhs_sid if c.width[lhs_sid] >= c.width[rhs_sid] else rhs_sid
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef object lhs_val, rhs_val, quotient, shifted
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_x_signal_shl(c, dst_sid, result_sid, shift)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    if rhs_val == 0:
        _whole_assign_x_signal_shl(c, dst_sid, result_sid, shift)
        return
    quotient = lhs_val // rhs_val
    shifted = (
        (quotient << shift) & ((((<object>1) << result_width)) - 1)
    )
    _whole_assign_py_value(c, dst_sid, shifted)

cdef inline void _whole_assign_div_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_sid = lhs_sid if c.width[lhs_sid] >= c.width[rhs_sid] else rhs_sid
    cdef object lhs_val, rhs_val, quotient
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_x_signal_shr(c, dst_sid, result_sid, shift)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    if rhs_val == 0:
        _whole_assign_x_signal_shr(c, dst_sid, result_sid, shift)
        return
    quotient = lhs_val // rhs_val
    _whole_assign_py_value(c, dst_sid, quotient >> shift)

cdef inline void _whole_assign_mod_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_sid = lhs_sid if c.width[lhs_sid] >= c.width[rhs_sid] else rhs_sid
    cdef int result_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef object lhs_val, rhs_val, remainder_val, shifted
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_x_signal_shl(c, dst_sid, result_sid, shift)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    if rhs_val == 0:
        _whole_assign_x_signal_shl(c, dst_sid, result_sid, shift)
        return
    remainder_val = lhs_val % rhs_val
    shifted = (
        (remainder_val << shift) & ((((<object>1) << result_width)) - 1)
    )
    _whole_assign_py_value(c, dst_sid, shifted)

cdef inline void _whole_assign_mod_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift):
    cdef int result_sid = lhs_sid if c.width[lhs_sid] >= c.width[rhs_sid] else rhs_sid
    cdef object lhs_val, rhs_val, remainder_val
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        _whole_assign_x_signal_shr(c, dst_sid, result_sid, shift)
        return
    lhs_val = _sig_py_unsigned(c, lhs_sid)
    rhs_val = _sig_py_unsigned(c, rhs_sid)
    if rhs_val == 0:
        _whole_assign_x_signal_shr(c, dst_sid, result_sid, shift)
        return
    remainder_val = lhs_val % rhs_val
    _whole_assign_py_value(c, dst_sid, remainder_val >> shift)

cdef inline void _whole_assign_div_const_shl(SimCtx *c, int dst_sid, int div_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[div_sid] if c.wide_words[div_sid] > 0 else 1
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, out_index, src_index, digit_index, word_index, digit_bits, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef int q_digits = (c.width[div_sid] + 31) >> 5
    cdef unsigned long long mask32 = <unsigned long long>0xFFFFFFFF
    cdef unsigned long long word_v, word_m, limb, acc, q_digit, remainder = 0, out_v, tail_mask
    cdef long long new_v = 0
    for i in range(src_words):
        word_m = _sig_word_mask(c, div_sid, i) & _word_mask64(c.width[div_sid] - (i * 64))
        if word_m != 0:
            _whole_assign_x_signal_shl(c, dst_sid, div_sid, shift)
            return
    for i in range(dst_words):
        c.wide_nba_val[dst_offset + i] = 0
    for i in range(q_digits):
        digit_index = q_digits - 1 - i
        word_index = digit_index >> 1
        word_v = _sig_word_val(c, div_sid, word_index)
        if (digit_index & 1) == 0:
            limb = word_v & mask32
        else:
            limb = (word_v >> 32) & mask32
        digit_bits = c.width[div_sid] - (digit_index * 32)
        if digit_bits < 32:
            limb &= _word_mask64(digit_bits)
        acc = (remainder << 32) | limb
        q_digit = acc // rhs_const
        remainder = acc % rhs_const
        if (digit_index & 1) == 0:
            c.wide_nba_val[dst_offset + word_index] |= q_digit
        else:
            c.wide_nba_val[dst_offset + word_index] |= q_digit << 32
    for i in range(dst_words):
        out_index = dst_words - 1 - i
        src_index = out_index - word_shift
        if src_index < 0 or src_index >= dst_words:
            out_v = 0
        else:
            out_v = c.wide_nba_val[dst_offset + src_index]
            if bit_shift != 0:
                out_v <<= bit_shift
                if src_index > 0:
                    out_v |= c.wide_nba_val[dst_offset + src_index - 1] >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (out_index * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        c.wide_nba_val[dst_offset + out_index] = out_v
        if out_v != c.wide_val[dst_offset + out_index] or c.wide_mask[dst_offset + out_index] != 0:
            c.wide_val[dst_offset + out_index] = out_v
            c.wide_mask[dst_offset + out_index] = 0
            changed = 1
    new_v = <long long>c.wide_val[dst_offset]
    if new_v != c.val[dst_sid] or c.mask[dst_sid] != 0:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = 0
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_div_const_shr(SimCtx *c, int dst_sid, int div_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[div_sid] if c.wide_words[div_sid] > 0 else 1
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, out_index, src_index, digit_index, word_index, digit_bits, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef int q_digits = (c.width[div_sid] + 31) >> 5
    cdef unsigned long long mask32 = <unsigned long long>0xFFFFFFFF
    cdef unsigned long long word_v, word_m, limb, acc, q_digit, remainder = 0, out_v, tail_mask
    cdef long long new_v = 0
    for i in range(src_words):
        word_m = _sig_word_mask(c, div_sid, i) & _word_mask64(c.width[div_sid] - (i * 64))
        if word_m != 0:
            _whole_assign_x_signal_shr(c, dst_sid, div_sid, shift)
            return
    for i in range(dst_words):
        c.wide_nba_val[dst_offset + i] = 0
    for i in range(q_digits):
        digit_index = q_digits - 1 - i
        word_index = digit_index >> 1
        word_v = _sig_word_val(c, div_sid, word_index)
        if (digit_index & 1) == 0:
            limb = word_v & mask32
        else:
            limb = (word_v >> 32) & mask32
        digit_bits = c.width[div_sid] - (digit_index * 32)
        if digit_bits < 32:
            limb &= _word_mask64(digit_bits)
        acc = (remainder << 32) | limb
        q_digit = acc // rhs_const
        remainder = acc % rhs_const
        if (digit_index & 1) == 0:
            c.wide_nba_val[dst_offset + word_index] |= q_digit
        else:
            c.wide_nba_val[dst_offset + word_index] |= q_digit << 32
    for i in range(dst_words):
        src_index = i + word_shift
        if src_index >= dst_words:
            out_v = 0
        else:
            out_v = c.wide_nba_val[dst_offset + src_index]
            if bit_shift != 0:
                out_v >>= bit_shift
                if src_index + 1 < dst_words:
                    out_v |= c.wide_nba_val[dst_offset + src_index + 1] << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        c.wide_nba_val[dst_offset + i] = out_v
        if out_v != c.wide_val[dst_offset + i] or c.wide_mask[dst_offset + i] != 0:
            c.wide_val[dst_offset + i] = out_v
            c.wide_mask[dst_offset + i] = 0
            changed = 1
    new_v = <long long>c.wide_val[dst_offset]
    if new_v != c.val[dst_sid] or c.mask[dst_sid] != 0:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = 0
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mod_const_shl(SimCtx *c, int dst_sid, int mod_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mod_sid] if c.wide_words[mod_sid] > 0 else 1
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, out_index, src_index, digit_index, word_index, digit_bits, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef int q_digits = (c.width[mod_sid] + 31) >> 5
    cdef unsigned long long mask32 = <unsigned long long>0xFFFFFFFF
    cdef unsigned long long word_v, word_m, limb, acc, remainder = 0, out_v, tail_mask
    cdef long long new_v = 0
    for i in range(src_words):
        word_m = _sig_word_mask(c, mod_sid, i) & _word_mask64(c.width[mod_sid] - (i * 64))
        if word_m != 0:
            _whole_assign_x_signal_shl(c, dst_sid, mod_sid, shift)
            return
    for i in range(q_digits):
        digit_index = q_digits - 1 - i
        word_index = digit_index >> 1
        word_v = _sig_word_val(c, mod_sid, word_index)
        if (digit_index & 1) == 0:
            limb = word_v & mask32
        else:
            limb = (word_v >> 32) & mask32
        digit_bits = c.width[mod_sid] - (digit_index * 32)
        if digit_bits < 32:
            limb &= _word_mask64(digit_bits)
        acc = (remainder << 32) | limb
        remainder = acc % rhs_const
    for i in range(dst_words):
        out_index = dst_words - 1 - i
        src_index = out_index - word_shift
        if src_index == 0:
            out_v = remainder
            if bit_shift != 0:
                out_v <<= bit_shift
        elif src_index == 1 and bit_shift != 0:
            out_v = remainder >> (64 - bit_shift)
        else:
            out_v = 0
        remaining_w = c.width[dst_sid] - (out_index * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        if out_v != c.wide_val[dst_offset + out_index] or c.wide_mask[dst_offset + out_index] != 0:
            c.wide_val[dst_offset + out_index] = out_v
            c.wide_mask[dst_offset + out_index] = 0
            changed = 1
    new_v = <long long>c.wide_val[dst_offset]
    if new_v != c.val[dst_sid] or c.mask[dst_sid] != 0:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = 0
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mod_const_shr(SimCtx *c, int dst_sid, int mod_sid, unsigned long long rhs_const, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mod_sid] if c.wide_words[mod_sid] > 0 else 1
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, out_index, src_index, digit_index, word_index, digit_bits, remaining_w, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef int q_digits = (c.width[mod_sid] + 31) >> 5
    cdef unsigned long long mask32 = <unsigned long long>0xFFFFFFFF
    cdef unsigned long long word_v, word_m, limb, acc, remainder = 0, out_v, tail_mask
    cdef long long new_v = 0
    for i in range(src_words):
        word_m = _sig_word_mask(c, mod_sid, i) & _word_mask64(c.width[mod_sid] - (i * 64))
        if word_m != 0:
            _whole_assign_x_signal_shr(c, dst_sid, mod_sid, shift)
            return
    for i in range(q_digits):
        digit_index = q_digits - 1 - i
        word_index = digit_index >> 1
        word_v = _sig_word_val(c, mod_sid, word_index)
        if (digit_index & 1) == 0:
            limb = word_v & mask32
        else:
            limb = (word_v >> 32) & mask32
        digit_bits = c.width[mod_sid] - (digit_index * 32)
        if digit_bits < 32:
            limb &= _word_mask64(digit_bits)
        acc = (remainder << 32) | limb
        remainder = acc % rhs_const
    for i in range(dst_words):
        src_index = i + word_shift
        if src_index == 0:
            out_v = remainder
            if bit_shift != 0:
                out_v >>= bit_shift
        else:
            out_v = 0
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        if out_v != c.wide_val[dst_offset + i] or c.wide_mask[dst_offset + i] != 0:
            c.wide_val[dst_offset + i] = out_v
            c.wide_mask[dst_offset + i] = 0
            changed = 1
    new_v = <long long>c.wide_val[dst_offset]
    if new_v != c.val[dst_sid] or c.mask[dst_sid] != 0:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = 0
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mul_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int lhs_digits = (c.width[lhs_sid] + 15) >> 4
    cdef int rhs_digits = (c.width[rhs_sid] + 15) >> 4
    cdef int prod_width = c.width[lhs_sid] + c.width[rhs_sid]
    cdef int prod_words = (prod_width + 63) >> 6
    cdef int prod_digits = (prod_width + 15) >> 4
    cdef int i, src_index, remaining_w, curr_index = -1, digit_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long curr_v = 0, prev_v = 0, curr_m = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long carry = 0
    cdef long long new_v = 0, new_m = 0
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        for i in range(out_words):
            src_index = i - word_shift
            out_v = 0
            if src_index < 0 or src_index >= prod_words:
                out_m = 0
            else:
                curr_m = _word_mask64(prod_width - (src_index * 64))
                if bit_shift == 0:
                    out_m = curr_m
                else:
                    out_m = curr_m << bit_shift
                    if src_index > 0:
                        prev_m = _word_mask64(prod_width - ((src_index - 1) * 64))
                        out_m |= prev_m >> (64 - bit_shift)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if dst_words > 0:
                if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                    c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                    c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                    changed = 1
            else:
                new_v = <long long>out_v
                new_m = <long long>out_m
        if dst_words > 0:
            new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
            new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
        if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
            c.val[dst_sid] = new_v
            c.mask[dst_sid] = new_m
            changed = 1
        if changed:
            c.dirty[dst_sid] = 1
        return
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= prod_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                curr_v = _mul_signal_next_word(c, lhs_sid, rhs_sid, lhs_digits, rhs_digits, prod_digits, &digit_index, &carry)
                curr_m = 0
                tail_mask = _word_mask64(prod_width - (curr_index * 64))
                curr_v &= tail_mask
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mul_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int lhs_digits = (c.width[lhs_sid] + 15) >> 4
    cdef int rhs_digits = (c.width[rhs_sid] + 15) >> 4
    cdef int prod_width = c.width[lhs_sid] + c.width[rhs_sid]
    cdef int prod_words = (prod_width + 63) >> 6
    cdef int prod_digits = (prod_width + 15) >> 4
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, digit_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long carry = 0
    cdef long long new_v = 0, new_m = 0
    if _signal_has_x(c, lhs_sid) or _signal_has_x(c, rhs_sid):
        for i in range(out_words):
            src_index = i + word_shift
            out_v = 0
            if src_index >= prod_words:
                out_m = 0
            else:
                curr_m = _word_mask64(prod_width - (src_index * 64))
                if bit_shift == 0:
                    out_m = curr_m
                else:
                    out_m = curr_m >> bit_shift
                    if src_index + 1 < prod_words:
                        next_m = _word_mask64(prod_width - ((src_index + 1) * 64))
                        out_m |= next_m << (64 - bit_shift)
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if dst_words > 0:
                if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                    c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                    c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                    changed = 1
            else:
                new_v = <long long>out_v
                new_m = <long long>out_m
        if dst_words > 0:
            new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
            new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
        if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
            c.val[dst_sid] = new_v
            c.mask[dst_sid] = new_m
            changed = 1
        if changed:
            c.dirty[dst_sid] = 1
        return
    if next_index < prod_words:
        next_v = _mul_signal_next_word(c, lhs_sid, rhs_sid, lhs_digits, rhs_digits, prod_digits, &digit_index, &carry)
        tail_mask = _word_mask64(prod_width - (next_index * 64))
        next_v &= tail_mask
        next_m = 0
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= prod_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < prod_words:
                    next_v = _mul_signal_next_word(c, lhs_sid, rhs_sid, lhs_digits, rhs_digits, prod_digits, &digit_index, &carry)
                    tail_mask = _word_mask64(prod_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m = 0
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < prod_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mul_const_shl(SimCtx *c, int dst_sid, int mul_sid, unsigned long long rhs_const, int rhs_width, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mul_sid] if c.wide_words[mul_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long av, am, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long carry_in = 0, carry_out = 0
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                av = _sig_word_val(c, mul_sid, curr_index)
                am = _sig_word_mask(c, mul_sid, curr_index)
                _umul64_addcarry(av, rhs_const, carry_in, &curr_v, &carry_out)
                curr_m = am
                tail_mask = _word_mask64(c.width[mul_sid] - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
                carry_in = carry_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_mul_const_shr(SimCtx *c, int dst_sid, int mul_sid, unsigned long long rhs_const, int rhs_width, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[mul_sid] if c.wide_words[mul_sid] > 0 else 1
    cdef int prod_width = c.width[mul_sid] + rhs_width
    cdef int prod_words = (prod_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long av, am, word_m, src_tail_mask, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long carry_in = 0, carry_out = 0
    cdef long long new_v = 0, new_m = 0
    for i in range(src_words):
        src_tail_mask = _word_mask64(c.width[mul_sid] - (i * 64))
        word_m = _sig_word_mask(c, mul_sid, i) & src_tail_mask
        if word_m != 0:
            for i in range(out_words):
                remaining_w = c.width[dst_sid] - (i * 64)
                tail_mask = _word_mask64(remaining_w)
                out_v = 0
                out_m = _word_mask64((prod_width - shift) - (i * 64)) if prod_width > shift else 0
                out_v &= tail_mask
                out_m &= tail_mask
                if dst_words > 0:
                    if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                        c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                        c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                        changed = 1
                else:
                    new_v = <long long>out_v
                    new_m = <long long>out_m
            if dst_words > 0:
                new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
                new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
            if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
                c.val[dst_sid] = new_v
                c.mask[dst_sid] = new_m
                changed = 1
            if changed:
                c.dirty[dst_sid] = 1
            return
    if next_index < prod_words:
        av = _sig_word_val(c, mul_sid, next_index) if next_index < src_words else 0
        _umul64_addcarry(av, rhs_const, carry_in, &next_v, &carry_out)
        next_m = 0
        tail_mask = _word_mask64(prod_width - (next_index * 64))
        next_v &= tail_mask
        carry_in = carry_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= prod_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < prod_words:
                    av = _sig_word_val(c, mul_sid, next_index) if next_index < src_words else 0
                    _umul64_addcarry(av, rhs_const, carry_in, &next_v, &carry_out)
                    next_m = 0
                    tail_mask = _word_mask64(prod_width - (next_index * 64))
                    next_v &= tail_mask
                    carry_in = carry_out
                else:
                    next_v = 0
                    next_m = 0
                    carry_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < prod_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_reduce_or_shift(SimCtx *c, int dst_sid, int signal_sid, int shift, int invert) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int i, src_remaining_w, changed = 0
    cdef unsigned long long word_v, word_m, src_tail_mask, known_one = 0, any_unknown = 0, base_v = 0, base_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(src_words):
        src_remaining_w = c.width[signal_sid] - (i * 64)
        src_tail_mask = _word_mask64(src_remaining_w)
        word_v = _sig_word_val(c, signal_sid, i) & src_tail_mask
        word_m = _sig_word_mask(c, signal_sid, i) & src_tail_mask
        if (word_v & ~word_m) != 0:
            known_one = 1
            break
        if word_m != 0:
            any_unknown = 1
    if known_one != 0:
        base_v = 1
    elif any_unknown != 0:
        base_m = 1
    if invert != 0 and base_m == 0:
        base_v ^= 1
    if shift != 0:
        base_v = 0
        base_m = 0
    for i in range(out_words):
        out_v = base_v if i == 0 else 0
        out_m = base_m if i == 0 else 0
        tail_mask = _word_mask64(c.width[dst_sid] - (i * 64))
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_reduce_and_shift(SimCtx *c, int dst_sid, int signal_sid, int shift, int invert) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int i, src_remaining_w, changed = 0
    cdef unsigned long long word_v, word_m, src_tail_mask, known_zero = 0, any_unknown = 0, base_v = 0, base_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(src_words):
        src_remaining_w = c.width[signal_sid] - (i * 64)
        src_tail_mask = _word_mask64(src_remaining_w)
        word_v = _sig_word_val(c, signal_sid, i) & src_tail_mask
        word_m = _sig_word_mask(c, signal_sid, i) & src_tail_mask
        if ((~word_v) & (~word_m) & src_tail_mask) != 0:
            known_zero = 1
            break
        if word_m != 0:
            any_unknown = 1
    if known_zero == 0:
        if any_unknown != 0:
            base_m = 1
        else:
            base_v = 1
    if invert != 0 and base_m == 0:
        base_v ^= 1
    if shift != 0:
        base_v = 0
        base_m = 0
    for i in range(out_words):
        out_v = base_v if i == 0 else 0
        out_m = base_m if i == 0 else 0
        tail_mask = _word_mask64(c.width[dst_sid] - (i * 64))
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_reduce_xor_shift(SimCtx *c, int dst_sid, int signal_sid, int shift, int invert) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int src_words = c.wide_words[signal_sid] if c.wide_words[signal_sid] > 0 else 1
    cdef int i, src_remaining_w, word_bits, parity = 0, changed = 0
    cdef unsigned long long word_v, word_m, src_tail_mask, base_v = 0, base_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(src_words):
        src_remaining_w = c.width[signal_sid] - (i * 64)
        src_tail_mask = _word_mask64(src_remaining_w)
        word_v = _sig_word_val(c, signal_sid, i) & src_tail_mask
        word_m = _sig_word_mask(c, signal_sid, i) & src_tail_mask
        if word_m != 0:
            base_m = 1
            break
        word_bits = 64 if src_remaining_w > 64 else src_remaining_w
        parity ^= _xor_reduce(<long long>word_v, word_bits)
    if base_m == 0:
        base_v = parity & 1
        if invert != 0:
            base_v ^= 1
    if shift != 0:
        base_v = 0
        base_m = 0
    for i in range(out_words):
        out_v = base_v if i == 0 else 0
        out_m = base_m if i == 0 else 0
        tail_mask = _word_mask64(c.width[dst_sid] - (i * 64))
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_shl(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[add_sid] if c.wide_words[add_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long av, am, rv, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                av = _sig_word_val(c, add_sid, curr_index)
                am = _sig_word_mask(c, add_sid, curr_index)
                rv = const_word if curr_index == 0 else 0
                sum1 = av + rv
                sum2 = sum1 + <unsigned long long>carry_in
                carry_out = 0
                if sum1 < av:
                    carry_out = 1
                if sum2 < sum1:
                    carry_out = 1
                curr_v = sum2
                curr_m = am
                carry_in = carry_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_const_shr(SimCtx *c, int dst_sid, int add_sid, unsigned long long const_word, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[add_sid] if c.wide_words[add_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long av, am, rv, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        av = _sig_word_val(c, add_sid, next_index)
        am = _sig_word_mask(c, add_sid, next_index)
        rv = const_word if next_index == 0 else 0
        sum1 = av + rv
        sum2 = sum1 + <unsigned long long>carry_in
        carry_out = 0
        if sum1 < av:
            carry_out = 1
        if sum2 < sum1:
            carry_out = 1
        next_v = sum2
        next_m = am
        carry_in = carry_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    av = _sig_word_val(c, add_sid, next_index)
                    am = _sig_word_mask(c, add_sid, next_index)
                    rv = const_word if next_index == 0 else 0
                    sum1 = av + rv
                    sum2 = sum1 + <unsigned long long>carry_in
                    carry_out = 0
                    if sum1 < av:
                        carry_out = 1
                    if sum2 < sum1:
                        carry_out = 1
                    next_v = sum2
                    next_m = am
                    carry_in = carry_out
                else:
                    next_v = 0
                    next_m = 0
                    carry_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_shl(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[sub_sid] if c.wide_words[sub_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long sv, sm, rv, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                sv = _sig_word_val(c, sub_sid, curr_index)
                sm = _sig_word_mask(c, sub_sid, curr_index)
                rv = const_word if curr_index == 0 else 0
                tmp = rv + <unsigned long long>borrow_in
                curr_v = sv - tmp
                borrow_out = 0
                if tmp < rv:
                    borrow_out = 1
                elif sv < tmp:
                    borrow_out = 1
                curr_m = sm
                borrow_in = borrow_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const_shr(SimCtx *c, int dst_sid, int sub_sid, unsigned long long const_word, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[sub_sid] if c.wide_words[sub_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long sv, sm, rv, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        sv = _sig_word_val(c, sub_sid, next_index)
        sm = _sig_word_mask(c, sub_sid, next_index)
        rv = const_word if next_index == 0 else 0
        tmp = rv + <unsigned long long>borrow_in
        next_v = sv - tmp
        borrow_out = 0
        if tmp < rv:
            borrow_out = 1
        elif sv < tmp:
            borrow_out = 1
        next_m = sm
        borrow_in = borrow_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    sv = _sig_word_val(c, sub_sid, next_index)
                    sm = _sig_word_mask(c, sub_sid, next_index)
                    rv = const_word if next_index == 0 else 0
                    tmp = rv + <unsigned long long>borrow_in
                    next_v = sv - tmp
                    borrow_out = 0
                    if tmp < rv:
                        borrow_out = 1
                    elif sv < tmp:
                        borrow_out = 1
                    next_m = sm
                    borrow_in = borrow_out
                else:
                    next_v = 0
                    next_m = 0
                    borrow_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_const(SimCtx *c, int dst_sid, int lhs_sid, unsigned long long const_word) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2, tmp
    cdef int carry_in = 0
    cdef int carry_out
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            cv = const_word if i == 0 else 0
            tmp = cv + <unsigned long long>borrow_in
            out_v = lv - tmp
            borrow_out = 0
            if tmp < cv:
                borrow_out = 1
            elif lv < tmp:
                borrow_out = 1
            out_m = lm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, lhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv - const_word) & tail_mask)
        new_m = <long long>(lm & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_shl(SimCtx *c, int dst_sid, unsigned long long const_word, int sub_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[sub_sid] if c.wide_words[sub_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = const_word if curr_index == 0 else 0
                rv = _sig_word_val(c, sub_sid, curr_index)
                rm = _sig_word_mask(c, sub_sid, curr_index)
                tmp = rv + <unsigned long long>borrow_in
                curr_v = lv - tmp
                borrow_out = 0
                if tmp < rv:
                    borrow_out = 1
                elif lv < tmp:
                    borrow_out = 1
                curr_m = rm
                tail_mask = _word_mask64(c.width[sub_sid] - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
                borrow_in = borrow_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_shr(SimCtx *c, int dst_sid, unsigned long long const_word, int sub_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[sub_sid] if c.wide_words[sub_sid] > 0 else 1
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = const_word if next_index == 0 else 0
        rv = _sig_word_val(c, sub_sid, next_index)
        rm = _sig_word_mask(c, sub_sid, next_index)
        tmp = rv + <unsigned long long>borrow_in
        next_v = lv - tmp
        borrow_out = 0
        if tmp < rv:
            borrow_out = 1
        elif lv < tmp:
            borrow_out = 1
        next_m = rm
        tail_mask = _word_mask64(c.width[sub_sid] - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
        borrow_in = borrow_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = const_word if next_index == 0 else 0
                    rv = _sig_word_val(c, sub_sid, next_index)
                    rm = _sig_word_mask(c, sub_sid, next_index)
                    tmp = rv + <unsigned long long>borrow_in
                    next_v = lv - tmp
                    borrow_out = 0
                    if tmp < rv:
                        borrow_out = 1
                    elif lv < tmp:
                        borrow_out = 1
                    next_m = rm
                    tail_mask = _word_mask64(c.width[sub_sid] - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                    borrow_in = borrow_out
                else:
                    next_v = 0
                    next_m = 0
                    borrow_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_const_sub_signal(SimCtx *c, int dst_sid, unsigned long long const_word, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, lm, cv, rv, rm, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2, tmp
    cdef int carry_in = 0
    cdef int carry_out
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = const_word if i == 0 else 0
            rv = _sig_word_val(c, rhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            tmp = rv + <unsigned long long>borrow_in
            out_v = lv - tmp
            borrow_out = 0
            if tmp < rv:
                borrow_out = 1
            elif lv < tmp:
                borrow_out = 1
            out_m = rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        rv = _sig_word_val(c, rhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((const_word - rv) & tail_mask)
        new_m = <long long>(rm & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, rv, lm, rm, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            rv = _sig_word_val(c, rhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            tmp = rv + <unsigned long long>borrow_in
            out_v = lv - tmp
            borrow_out = 0
            if tmp < rv:
                borrow_out = 1
            elif lv < tmp:
                borrow_out = 1
            out_m = lm | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            borrow_in = borrow_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, lhs_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv - rv) & tail_mask)
        new_m = <long long>((lm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, rv, lm, rm, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            rv = _sig_word_val(c, rhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            sum1 = lv + rv
            sum2 = sum1 + <unsigned long long>carry_in
            carry_out = 0
            if sum1 < lv:
                carry_out = 1
            if sum2 < sum1:
                carry_out = 1
            out_v = sum2
            out_m = lm | rm
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            out_v &= tail_mask
            out_m &= tail_mask
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
            carry_in = carry_out
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    else:
        lv = _sig_word_val(c, lhs_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv + rv) & tail_mask)
        new_m = <long long>((lm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = _sig_word_val(c, lhs_sid, curr_index)
                rv = _sig_word_val(c, rhs_sid, curr_index)
                lm = _sig_word_mask(c, lhs_sid, curr_index)
                rm = _sig_word_mask(c, rhs_sid, curr_index)
                sum1 = lv + rv
                sum2 = sum1 + <unsigned long long>carry_in
                carry_out = 0
                if sum1 < lv:
                    carry_out = 1
                if sum2 < sum1:
                    carry_out = 1
                curr_v = sum2
                curr_m = lm | rm
                tail_mask = _word_mask64(src_width - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
                carry_in = carry_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_add_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long sum1, sum2
    cdef int carry_in = 0
    cdef int carry_out
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = _sig_word_val(c, lhs_sid, next_index)
        rv = _sig_word_val(c, rhs_sid, next_index)
        lm = _sig_word_mask(c, lhs_sid, next_index)
        rm = _sig_word_mask(c, rhs_sid, next_index)
        sum1 = lv + rv
        sum2 = sum1 + <unsigned long long>carry_in
        carry_out = 0
        if sum1 < lv:
            carry_out = 1
        if sum2 < sum1:
            carry_out = 1
        next_v = sum2
        next_m = lm | rm
        tail_mask = _word_mask64(src_width - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
        carry_in = carry_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = _sig_word_val(c, lhs_sid, next_index)
                    rv = _sig_word_val(c, rhs_sid, next_index)
                    lm = _sig_word_mask(c, lhs_sid, next_index)
                    rm = _sig_word_mask(c, rhs_sid, next_index)
                    sum1 = lv + rv
                    sum2 = sum1 + <unsigned long long>carry_in
                    carry_out = 0
                    if sum1 < lv:
                        carry_out = 1
                    if sum2 < sum1:
                        carry_out = 1
                    next_v = sum2
                    next_m = lm | rm
                    tail_mask = _word_mask64(src_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                    carry_in = carry_out
                else:
                    next_v = 0
                    next_m = 0
                    carry_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = _sig_word_val(c, lhs_sid, curr_index)
                rv = _sig_word_val(c, rhs_sid, curr_index)
                lm = _sig_word_mask(c, lhs_sid, curr_index)
                rm = _sig_word_mask(c, rhs_sid, curr_index)
                tmp = rv + <unsigned long long>borrow_in
                curr_v = lv - tmp
                borrow_out = 0
                if tmp < rv:
                    borrow_out = 1
                elif lv < tmp:
                    borrow_out = 1
                curr_m = lm | rm
                tail_mask = _word_mask64(src_width - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
                borrow_in = borrow_out
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_sub_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef unsigned long long tmp
    cdef int borrow_in = 0
    cdef int borrow_out
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = _sig_word_val(c, lhs_sid, next_index)
        rv = _sig_word_val(c, rhs_sid, next_index)
        lm = _sig_word_mask(c, lhs_sid, next_index)
        rm = _sig_word_mask(c, rhs_sid, next_index)
        tmp = rv + <unsigned long long>borrow_in
        next_v = lv - tmp
        borrow_out = 0
        if tmp < rv:
            borrow_out = 1
        elif lv < tmp:
            borrow_out = 1
        next_m = lm | rm
        tail_mask = _word_mask64(src_width - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
        borrow_in = borrow_out
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = _sig_word_val(c, lhs_sid, next_index)
                    rv = _sig_word_val(c, rhs_sid, next_index)
                    lm = _sig_word_mask(c, lhs_sid, next_index)
                    rm = _sig_word_mask(c, rhs_sid, next_index)
                    tmp = rv + <unsigned long long>borrow_in
                    next_v = lv - tmp
                    borrow_out = 0
                    if tmp < rv:
                        borrow_out = 1
                    elif lv < tmp:
                        borrow_out = 1
                    next_m = lm | rm
                    tail_mask = _word_mask64(src_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                    borrow_in = borrow_out
                else:
                    next_v = 0
                    next_m = 0
                    borrow_in = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_and_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = _sig_word_val(c, lhs_sid, curr_index)
                rv = _sig_word_val(c, rhs_sid, curr_index)
                lm = _sig_word_mask(c, lhs_sid, curr_index)
                rm = _sig_word_mask(c, rhs_sid, curr_index)
                curr_v = lv & rv
                curr_m = (lm | rm) & ~(~lv & ~lm) & ~(~rv & ~rm)
                tail_mask = _word_mask64(src_width - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_and_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = _sig_word_val(c, lhs_sid, next_index)
        rv = _sig_word_val(c, rhs_sid, next_index)
        lm = _sig_word_mask(c, lhs_sid, next_index)
        rm = _sig_word_mask(c, rhs_sid, next_index)
        next_v = lv & rv
        next_m = (lm | rm) & ~(~lv & ~lm) & ~(~rv & ~rm)
        tail_mask = _word_mask64(src_width - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = _sig_word_val(c, lhs_sid, next_index)
                    rv = _sig_word_val(c, rhs_sid, next_index)
                    lm = _sig_word_mask(c, lhs_sid, next_index)
                    rm = _sig_word_mask(c, rhs_sid, next_index)
                    next_v = lv & rv
                    next_m = (lm | rm) & ~(~lv & ~lm) & ~(~rv & ~rm)
                    tail_mask = _word_mask64(src_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = _sig_word_val(c, lhs_sid, curr_index)
                rv = _sig_word_val(c, rhs_sid, curr_index)
                lm = _sig_word_mask(c, lhs_sid, curr_index)
                rm = _sig_word_mask(c, rhs_sid, curr_index)
                curr_v = lv | rv
                curr_m = (lm | rm) & ~(lv & ~lm) & ~(rv & ~rm)
                tail_mask = _word_mask64(src_width - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = _sig_word_val(c, lhs_sid, next_index)
        rv = _sig_word_val(c, rhs_sid, next_index)
        lm = _sig_word_mask(c, lhs_sid, next_index)
        rm = _sig_word_mask(c, rhs_sid, next_index)
        next_v = lv | rv
        next_m = (lm | rm) & ~(lv & ~lm) & ~(rv & ~rm)
        tail_mask = _word_mask64(src_width - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = _sig_word_val(c, lhs_sid, next_index)
                    rv = _sig_word_val(c, rhs_sid, next_index)
                    lm = _sig_word_mask(c, lhs_sid, next_index)
                    rm = _sig_word_mask(c, rhs_sid, next_index)
                    next_v = lv | rv
                    next_m = (lm | rm) & ~(lv & ~lm) & ~(rv & ~rm)
                    tail_mask = _word_mask64(src_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_signal_shl(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, prev_v = 0, prev_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    for i in range(out_words):
        src_index = i - word_shift
        if src_index < 0 or src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                prev_v = curr_v
                prev_m = curr_m
                lv = _sig_word_val(c, lhs_sid, curr_index)
                rv = _sig_word_val(c, rhs_sid, curr_index)
                lm = _sig_word_mask(c, lhs_sid, curr_index)
                rm = _sig_word_mask(c, rhs_sid, curr_index)
                curr_v = lv ^ rv
                curr_m = lm | rm
                tail_mask = _word_mask64(src_width - (curr_index * 64))
                curr_v &= tail_mask
                curr_m &= tail_mask
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v << bit_shift
                out_m = curr_m << bit_shift
                if src_index > 0:
                    out_v |= prev_v >> (64 - bit_shift)
                    out_m |= prev_m >> (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_signal_shr(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid, int shift) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_width = c.width[lhs_sid] if c.width[lhs_sid] >= c.width[rhs_sid] else c.width[rhs_sid]
    cdef int src_words = (src_width + 63) >> 6
    cdef int out_words = dst_words if dst_words > 0 else 1
    cdef int i, src_index, remaining_w, curr_index = -1, next_index = 0, changed = 0
    cdef int word_shift = shift >> 6
    cdef int bit_shift = shift & 63
    cdef unsigned long long lv, rv, lm, rm, curr_v = 0, curr_m = 0, next_v = 0, next_m = 0, out_v, out_m, tail_mask
    cdef long long new_v = 0, new_m = 0
    if next_index < src_words:
        lv = _sig_word_val(c, lhs_sid, next_index)
        rv = _sig_word_val(c, rhs_sid, next_index)
        lm = _sig_word_mask(c, lhs_sid, next_index)
        rm = _sig_word_mask(c, rhs_sid, next_index)
        next_v = lv ^ rv
        next_m = lm | rm
        tail_mask = _word_mask64(src_width - (next_index * 64))
        next_v &= tail_mask
        next_m &= tail_mask
    next_index += 1
    for i in range(out_words):
        src_index = i + word_shift
        if src_index >= src_words:
            out_v = 0
            out_m = 0
        else:
            while curr_index < src_index:
                curr_index += 1
                curr_v = next_v
                curr_m = next_m
                if next_index < src_words:
                    lv = _sig_word_val(c, lhs_sid, next_index)
                    rv = _sig_word_val(c, rhs_sid, next_index)
                    lm = _sig_word_mask(c, lhs_sid, next_index)
                    rm = _sig_word_mask(c, rhs_sid, next_index)
                    next_v = lv ^ rv
                    next_m = lm | rm
                    tail_mask = _word_mask64(src_width - (next_index * 64))
                    next_v &= tail_mask
                    next_m &= tail_mask
                else:
                    next_v = 0
                    next_m = 0
                next_index += 1
            if bit_shift == 0:
                out_v = curr_v
                out_m = curr_m
            else:
                out_v = curr_v >> bit_shift
                out_m = curr_m >> bit_shift
                if src_index + 1 < src_words:
                    out_v |= next_v << (64 - bit_shift)
                    out_m |= next_m << (64 - bit_shift)
        remaining_w = c.width[dst_sid] - (i * 64)
        tail_mask = _word_mask64(remaining_w)
        out_v &= tail_mask
        out_m &= tail_mask
        if dst_words > 0:
            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:
                c.wide_val[c.wide_offset[dst_sid] + i] = out_v
                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m
                changed = 1
        else:
            new_v = <long long>out_v
            new_m = <long long>out_m
    if dst_words > 0:
        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]
        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_and_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, rv, lm, rm, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            rv = _sig_word_val(c, rhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = lv & rv
            out_m = (lm | rm) & ~(~lv & ~lm) & ~(~rv & ~rm)
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
        lv = _sig_word_val(c, lhs_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv & rv) & tail_mask)
        new_m = <long long>(((lm | rm) & ~(~lv & ~lm) & ~(~rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_or_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, rv, lm, rm, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            rv = _sig_word_val(c, rhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = lv | rv
            out_m = (lm | rm) & ~(lv & ~lm) & ~(rv & ~rm)
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
        lv = _sig_word_val(c, lhs_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv | rv) & tail_mask)
        new_m = <long long>(((lm | rm) & ~(lv & ~lm) & ~(rv & ~rm)) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_xor_signal(SimCtx *c, int dst_sid, int lhs_sid, int rhs_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long lv, rv, lm, rm, out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            lv = _sig_word_val(c, lhs_sid, i)
            rv = _sig_word_val(c, rhs_sid, i)
            lm = _sig_word_mask(c, lhs_sid, i)
            rm = _sig_word_mask(c, rhs_sid, i)
            out_v = lv ^ rv
            out_m = lm | rm
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
        lv = _sig_word_val(c, lhs_sid, 0)
        rv = _sig_word_val(c, rhs_sid, 0)
        lm = _sig_word_mask(c, lhs_sid, 0)
        rm = _sig_word_mask(c, rhs_sid, 0)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>((lv ^ rv) & tail_mask)
        new_m = <long long>((lm | rm) & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_concat2_signal(SimCtx *c, int dst_sid, int hi_sid, int lo_sid, int lo_width) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, base, remaining_w, carry, changed = 0
    cdef unsigned long long out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            base = i * 64
            if base < lo_width:
                out_v = _sig_extract_word_val(c, lo_sid, base)
                out_m = _sig_extract_word_mask(c, lo_sid, base)
                carry = lo_width - base
                if carry < 64:
                    out_v |= _sig_extract_word_val(c, hi_sid, 0) << carry
                    out_m |= _sig_extract_word_mask(c, hi_sid, 0) << carry
            else:
                out_v = _sig_extract_word_val(c, hi_sid, base - lo_width)
                out_m = _sig_extract_word_mask(c, hi_sid, base - lo_width)
            remaining_w = c.width[dst_sid] - base
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
        out_v = _sig_extract_word_val(c, lo_sid, 0)
        out_m = _sig_extract_word_mask(c, lo_sid, 0)
        if lo_width < 64:
            out_v |= _sig_extract_word_val(c, hi_sid, 0) << lo_width
            out_m |= _sig_extract_word_mask(c, hi_sid, 0) << lo_width
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(out_v & tail_mask)
        new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_concat3_signal(SimCtx *c, int dst_sid, int a_sid, int b_sid, int c_sid, int b_width, int c_width) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, base, remaining_w, carry, changed = 0
    cdef unsigned long long out_v, out_m, tail_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            base = i * 64
            out_v = _sig_extract_word_val(c, c_sid, base)
            out_m = _sig_extract_word_mask(c, c_sid, base)
            if base < c_width:
                carry = c_width - base
                if carry < 64:
                    out_v |= _sig_extract_word_val(c, b_sid, 0) << carry
                    out_m |= _sig_extract_word_mask(c, b_sid, 0) << carry
                if carry + b_width < 64:
                    out_v |= _sig_extract_word_val(c, a_sid, 0) << (carry + b_width)
                    out_m |= _sig_extract_word_mask(c, a_sid, 0) << (carry + b_width)
            elif base < c_width + b_width:
                out_v = _sig_extract_word_val(c, b_sid, base - c_width)
                out_m = _sig_extract_word_mask(c, b_sid, base - c_width)
                carry = c_width + b_width - base
                if carry < 64:
                    out_v |= _sig_extract_word_val(c, a_sid, 0) << carry
                    out_m |= _sig_extract_word_mask(c, a_sid, 0) << carry
            else:
                out_v = _sig_extract_word_val(c, a_sid, base - c_width - b_width)
                out_m = _sig_extract_word_mask(c, a_sid, base - c_width - b_width)
            remaining_w = c.width[dst_sid] - base
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
        out_v = _sig_extract_word_val(c, c_sid, 0)
        out_m = _sig_extract_word_mask(c, c_sid, 0)
        if c_width < 64:
            out_v |= _sig_extract_word_val(c, b_sid, 0) << c_width
            out_m |= _sig_extract_word_mask(c, b_sid, 0) << c_width
        if c_width + b_width < 64:
            out_v |= _sig_extract_word_val(c, a_sid, 0) << (c_width + b_width)
            out_m |= _sig_extract_word_mask(c, a_sid, 0) << (c_width + b_width)
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(out_v & tail_mask)
        new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_stage_signal(SimCtx *c, int dst_sid, int src_sid) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int src_words = c.wide_words[src_sid]
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int src_offset = c.wide_offset[src_sid]
    cdef int i, remaining_w
    cdef unsigned long long word_v, word_m, tail_mask
    if dst_words > 0:
        for i in range(dst_words):
            if src_words > 0:
                if i < src_words:
                    word_v = c.wide_val[src_offset + i]
                    word_m = c.wide_mask[src_offset + i]
                else:
                    word_v = 0
                    word_m = 0
            elif i == 0:
                word_v = <unsigned long long>c.val[src_sid]
                word_m = <unsigned long long>c.mask[src_sid]
            else:
                word_v = 0
                word_m = 0
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            c.wide_nba_val[dst_offset + i] = word_v & tail_mask
            c.wide_nba_mask[dst_offset + i] = word_m & tail_mask
        c.nba_val[dst_sid] = <long long>c.wide_nba_val[dst_offset]
        c.nba_mask[dst_sid] = <long long>c.wide_nba_mask[dst_offset]
    elif src_words > 0:
        c.nba_val[dst_sid] = <long long>(c.wide_val[src_offset] & _word_mask64(c.width[dst_sid]))
        c.nba_mask[dst_sid] = <long long>(c.wide_mask[src_offset] & _word_mask64(c.width[dst_sid]))
    else:
        c.nba_val[dst_sid] = c.val[src_sid] & wmask(c.width[dst_sid])
        c.nba_mask[dst_sid] = c.mask[src_sid] & wmask(c.width[dst_sid])
    c.nba_dirty[dst_sid] = 1
    c.nba_pending = 1

cdef inline void _whole_stage_const_word(SimCtx *c, int dst_sid, unsigned long long word_v, unsigned long long word_m) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, remaining_w
    cdef unsigned long long tail_mask, out_v, out_m
    if dst_words > 0:
        for i in range(dst_words):
            out_v = word_v if i == 0 else 0
            out_m = word_m if i == 0 else 0
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            c.wide_nba_val[dst_offset + i] = out_v & tail_mask
            c.wide_nba_mask[dst_offset + i] = out_m & tail_mask
        c.nba_val[dst_sid] = <long long>c.wide_nba_val[dst_offset]
        c.nba_mask[dst_sid] = <long long>c.wide_nba_mask[dst_offset]
    else:
        tail_mask = _word_mask64(c.width[dst_sid])
        c.nba_val[dst_sid] = <long long>(word_v & tail_mask)
        c.nba_mask[dst_sid] = <long long>(word_m & tail_mask)
    c.nba_dirty[dst_sid] = 1
    c.nba_pending = 1

cdef inline void _whole_stage_insert_word(SimCtx *c, int dst_sid, int lsb, int width, unsigned long long word_v, unsigned long long word_m) noexcept nogil:
    cdef int remaining = width
    cdef int src_shift = 0
    cdef int bit = lsb
    cdef int word_idx, word_shift, chunk, dst_offset, i
    cdef unsigned long long chunk_mask, clear_mask, base_v, base_m, out_v, out_m
    if c.nba_dirty[dst_sid] == 0:
        if c.wide_words[dst_sid] > 0:
            for i in range(c.wide_words[dst_sid]):
                dst_offset = c.wide_offset[dst_sid] + i
                c.wide_nba_val[dst_offset] = c.wide_val[dst_offset]
                c.wide_nba_mask[dst_offset] = c.wide_mask[dst_offset]
            c.nba_val[dst_sid] = <long long>c.wide_nba_val[c.wide_offset[dst_sid]]
            c.nba_mask[dst_sid] = <long long>c.wide_nba_mask[c.wide_offset[dst_sid]]
        else:
            c.nba_val[dst_sid] = c.val[dst_sid]
            c.nba_mask[dst_sid] = c.mask[dst_sid]
    while remaining > 0:
        word_idx = bit >> 6
        word_shift = bit & 63
        chunk = remaining if remaining < (64 - word_shift) else (64 - word_shift)
        chunk_mask = _word_mask64(chunk)
        clear_mask = ~(chunk_mask << word_shift)
        out_v = (((word_v >> src_shift) & chunk_mask) & ~((word_m >> src_shift) & chunk_mask)) << word_shift
        out_m = (((word_m >> src_shift) & chunk_mask)) << word_shift
        if c.wide_words[dst_sid] > 0:
            dst_offset = c.wide_offset[dst_sid] + word_idx
            base_v = c.wide_nba_val[dst_offset]
            base_m = c.wide_nba_mask[dst_offset]
            c.wide_nba_val[dst_offset] = (base_v & clear_mask) | out_v
            c.wide_nba_mask[dst_offset] = (base_m & clear_mask) | out_m
        else:
            base_v = <unsigned long long>c.nba_val[dst_sid]
            base_m = <unsigned long long>c.nba_mask[dst_sid]
            c.nba_val[dst_sid] = <long long>((base_v & clear_mask) | out_v)
            c.nba_mask[dst_sid] = <long long>((base_m & clear_mask) | out_m)
        remaining -= chunk
        bit += chunk
        src_shift += chunk
    if c.wide_words[dst_sid] > 0:
        c.nba_val[dst_sid] = <long long>c.wide_nba_val[c.wide_offset[dst_sid]]
        c.nba_mask[dst_sid] = <long long>c.wide_nba_mask[c.wide_offset[dst_sid]]
    c.nba_dirty[dst_sid] = 1
    c.nba_pending = 1

cdef inline void _whole_stage_repeat_word(SimCtx *c, int dst_sid, unsigned long long word_v, unsigned long long word_m, int elem_width, int count) noexcept nogil:
    cdef int i
    _whole_stage_const_word(c, dst_sid, 0, 0)
    for i in range(count):
        _whole_stage_insert_word(c, dst_sid, i * elem_width, elem_width, word_v, word_m)

cdef inline void _whole_stage_repeat_signal_slice(SimCtx *c, int dst_sid, int src_sid, int src_lsb, int elem_width, int count) noexcept nogil:
    cdef int i, src_bit, chunk
    cdef unsigned long long word_v, word_m
    _whole_stage_const_word(c, dst_sid, 0, 0)
    for i in range(count):
        src_bit = 0
        while src_bit < elem_width:
            chunk = elem_width - src_bit
            if chunk > 64:
                chunk = 64
            word_v = _sig_extract_word_val(c, src_sid, src_lsb + src_bit)
            word_m = _sig_extract_word_mask(c, src_sid, src_lsb + src_bit)
            _whole_stage_insert_word(c, dst_sid, i * elem_width + src_bit, chunk, word_v, word_m)
            src_bit += chunk

cdef inline void _whole_stage_insert_signal(SimCtx *c, int dst_sid, int lsb, int src_sid, int src_width) noexcept nogil:
    cdef int src_bit = 0
    cdef int chunk
    cdef unsigned long long word_v, word_m
    while src_bit < src_width:
        chunk = src_width - src_bit
        if chunk > 64:
            chunk = 64
        word_v = _sig_extract_word_val(c, src_sid, src_bit)
        word_m = _sig_extract_word_mask(c, src_sid, src_bit)
        _whole_stage_insert_word(c, dst_sid, lsb + src_bit, chunk, word_v, word_m)
        src_bit += chunk

cdef inline void _whole_stage_insert_signal_slice(SimCtx *c, int dst_sid, int dst_lsb, int src_sid, int src_lsb, int src_width) noexcept nogil:
    cdef int src_bit = 0
    cdef int chunk
    cdef unsigned long long word_v, word_m
    while src_bit < src_width:
        chunk = src_width - src_bit
        if chunk > 64:
            chunk = 64
        word_v = _sig_extract_word_val(c, src_sid, src_lsb + src_bit)
        word_m = _sig_extract_word_mask(c, src_sid, src_lsb + src_bit)
        _whole_stage_insert_word(c, dst_sid, dst_lsb + src_bit, chunk, word_v, word_m)
        src_bit += chunk

cdef inline void _whole_assign_const_word(SimCtx *c, int dst_sid, unsigned long long word_v, unsigned long long word_m) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, remaining_w, changed = 0
    cdef unsigned long long tail_mask, out_v, out_m
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            out_v = word_v if i == 0 else 0
            out_m = word_m if i == 0 else 0
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
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(word_v & tail_mask)
        new_m = <long long>(word_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_assign_repeat_word(SimCtx *c, int dst_sid, unsigned long long word_v, unsigned long long word_m, int elem_width, int count) noexcept nogil:
    cdef int i
    _whole_assign_const_word(c, dst_sid, 0, 0)
    for i in range(count):
        _whole_assign_insert_word(c, dst_sid, i * elem_width, elem_width, word_v, word_m)

cdef inline void _whole_assign_repeat_signal_slice(SimCtx *c, int dst_sid, int src_sid, int src_lsb, int elem_width, int count) noexcept nogil:
    cdef int i, src_bit, chunk
    cdef unsigned long long word_v, word_m
    _whole_assign_const_word(c, dst_sid, 0, 0)
    for i in range(count):
        src_bit = 0
        while src_bit < elem_width:
            chunk = elem_width - src_bit
            if chunk > 64:
                chunk = 64
            word_v = _sig_extract_word_val(c, src_sid, src_lsb + src_bit)
            word_m = _sig_extract_word_mask(c, src_sid, src_lsb + src_bit)
            _whole_assign_insert_word(c, dst_sid, i * elem_width + src_bit, chunk, word_v, word_m)
            src_bit += chunk

cdef inline void _whole_assign_insert_signal(SimCtx *c, int dst_sid, int lsb, int src_sid, int src_width) noexcept nogil:
    cdef int src_bit = 0
    cdef int chunk
    cdef unsigned long long word_v, word_m
    while src_bit < src_width:
        chunk = src_width - src_bit
        if chunk > 64:
            chunk = 64
        word_v = _sig_extract_word_val(c, src_sid, src_bit)
        word_m = _sig_extract_word_mask(c, src_sid, src_bit)
        _whole_assign_insert_word(c, dst_sid, lsb + src_bit, chunk, word_v, word_m)
        src_bit += chunk

cdef inline void _whole_assign_insert_signal_slice(SimCtx *c, int dst_sid, int dst_lsb, int src_sid, int src_lsb, int src_width) noexcept nogil:
    cdef int src_bit = 0
    cdef int chunk
    cdef unsigned long long word_v, word_m
    while src_bit < src_width:
        chunk = src_width - src_bit
        if chunk > 64:
            chunk = 64
        word_v = _sig_extract_word_val(c, src_sid, src_lsb + src_bit)
        word_m = _sig_extract_word_mask(c, src_sid, src_lsb + src_bit)
        _whole_assign_insert_word(c, dst_sid, dst_lsb + src_bit, chunk, word_v, word_m)
        src_bit += chunk

cdef inline void _whole_assign_insert_word(SimCtx *c, int dst_sid, int lsb, int width, unsigned long long word_v, unsigned long long word_m) noexcept nogil:
    cdef int remaining = width
    cdef int src_shift = 0
    cdef int bit = lsb
    cdef int word_idx, word_shift, chunk, dst_offset, changed = 0
    cdef unsigned long long chunk_mask, clear_mask, base_v, base_m, out_v, out_m
    cdef long long new_v, new_m
    while remaining > 0:
        word_idx = bit >> 6
        word_shift = bit & 63
        chunk = remaining if remaining < (64 - word_shift) else (64 - word_shift)
        chunk_mask = _word_mask64(chunk)
        clear_mask = ~(chunk_mask << word_shift)
        out_v = (((word_v >> src_shift) & chunk_mask) & ~((word_m >> src_shift) & chunk_mask)) << word_shift
        out_m = (((word_m >> src_shift) & chunk_mask)) << word_shift
        if c.wide_words[dst_sid] > 0:
            dst_offset = c.wide_offset[dst_sid] + word_idx
            base_v = c.wide_val[dst_offset]
            base_m = c.wide_mask[dst_offset]
            new_v = <long long>((base_v & clear_mask) | out_v)
            new_m = <long long>((base_m & clear_mask) | out_m)
            if new_v != <long long>base_v or new_m != <long long>base_m:
                c.wide_val[dst_offset] = <unsigned long long>new_v
                c.wide_mask[dst_offset] = <unsigned long long>new_m
                changed = 1
        else:
            base_v = <unsigned long long>c.val[dst_sid]
            base_m = <unsigned long long>c.mask[dst_sid]
            new_v = <long long>((base_v & clear_mask) | out_v)
            new_m = <long long>((base_m & clear_mask) | out_m)
            if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
                c.val[dst_sid] = new_v
                c.mask[dst_sid] = new_m
                changed = 1
        remaining -= chunk
        bit += chunk
        src_shift += chunk
    if c.wide_words[dst_sid] > 0:
        c.val[dst_sid] = <long long>c.wide_val[c.wide_offset[dst_sid]]
        c.mask[dst_sid] = <long long>c.wide_mask[c.wide_offset[dst_sid]]
    if changed:
        c.dirty[dst_sid] = 1

cdef inline void _whole_stage_slice_width_signal(SimCtx *c, int dst_sid, int src_sid, int lsb, int src_width) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int dst_offset = c.wide_offset[dst_sid]
    cdef int i, src_word, src_shift, remaining_w, src_remaining_w
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, src_mask
    if dst_words > 0:
        for i in range(dst_words):
            src_remaining_w = src_width - (i * 64)
            if src_remaining_w <= 0:
                out_v = 0
                out_m = 0
            else:
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
                src_mask = _word_mask64(src_remaining_w)
                out_v &= src_mask
                out_m &= src_mask
            remaining_w = c.width[dst_sid] - (i * 64)
            tail_mask = _word_mask64(remaining_w)
            c.wide_nba_val[dst_offset + i] = out_v & tail_mask
            c.wide_nba_mask[dst_offset + i] = out_m & tail_mask
        c.nba_val[dst_sid] = <long long>c.wide_nba_val[dst_offset]
        c.nba_mask[dst_sid] = <long long>c.wide_nba_mask[dst_offset]
    else:
        if src_width <= 0:
            out_v = 0
            out_m = 0
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
            src_mask = _word_mask64(src_width)
            out_v &= src_mask
            out_m &= src_mask
        tail_mask = _word_mask64(c.width[dst_sid])
        c.nba_val[dst_sid] = <long long>(out_v & tail_mask)
        c.nba_mask[dst_sid] = <long long>(out_m & tail_mask)
    c.nba_dirty[dst_sid] = 1
    c.nba_pending = 1

cdef inline void _whole_assign_slice_width_signal(SimCtx *c, int dst_sid, int src_sid, int lsb, int src_width) noexcept nogil:
    cdef int dst_words = c.wide_words[dst_sid]
    cdef int i, src_word, src_shift, remaining_w, src_remaining_w, changed = 0
    cdef unsigned long long lo_v, hi_v, lo_m, hi_m, out_v, out_m, tail_mask, src_mask
    cdef long long new_v, new_m
    if dst_words > 0:
        for i in range(dst_words):
            src_remaining_w = src_width - (i * 64)
            if src_remaining_w <= 0:
                out_v = 0
                out_m = 0
            else:
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
                src_mask = _word_mask64(src_remaining_w)
                out_v &= src_mask
                out_m &= src_mask
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
        if src_width <= 0:
            out_v = 0
            out_m = 0
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
            src_mask = _word_mask64(src_width)
            out_v &= src_mask
            out_m &= src_mask
        tail_mask = _word_mask64(c.width[dst_sid])
        new_v = <long long>(out_v & tail_mask)
        new_m = <long long>(out_m & tail_mask)
    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:
        c.val[dst_sid] = new_v
        c.mask[dst_sid] = new_m
        changed = 1
    if changed:
        c.dirty[dst_sid] = 1
