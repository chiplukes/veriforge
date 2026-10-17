
# ``$__vf_real(op, a, b)`` (sim/reals.py, which has the reference version,
# real_op): IEEE 754 double operations on 64-bit bit patterns. Shared by the
# compiled engine (pasted into every generated module) and vm-fast
# (`include`d by sim/vm/_interp_fast.pyx). The op numbers are reals.py's.
from libc.math cimport pow as _vf_pow, trunc as _vf_trunc, fabs as _vf_fabs, fmod as _vf_fmod
from libc.string cimport memcpy as _vf_memcpy
cimport cython as _vf_cython

cdef inline double _vf_bits_to_d(unsigned long long b) noexcept nogil:
    cdef double x
    _vf_memcpy(&x, &b, 8)
    return x

cdef inline unsigned long long _vf_d_to_bits(double x) noexcept nogil:
    cdef unsigned long long b
    if x != x:
        return 0x7FF8000000000000ULL  # (reals.py CANONICAL_NAN)
    _vf_memcpy(&b, &x, 8)
    return b

cdef inline unsigned long long _vf_real_to_int(double x, int truncate) noexcept nogil:
    # Round half away from zero (or truncate); the exact value mod 2**64
    # when it doesn't fit; NaN and infinities give 0 (reals.py real_to_int).
    cdef double n, r
    if x != x or x - x != 0.0:
        return 0
    n = _vf_trunc(x)
    if not truncate and _vf_fabs(x - n) >= 0.5:
        n = n + 1.0 if x > 0 else n - 1.0
    if _vf_fabs(n) < 9.2e18:
        return <unsigned long long>(<long long>n)
    r = _vf_fmod(n, 18446744073709551616.0)  # (exact: n is an integer)
    if r < 0:
        r = r + 18446744073709551616.0
    if r >= 9223372036854775808.0:
        return <unsigned long long>(<long long>(r - 18446744073709551616.0))
    return <unsigned long long>(<long long>r)

@_vf_cython.cdivision(True)
cdef inline unsigned long long _vf_real_op(int op, unsigned long long a, unsigned long long am, int aw,
                                           unsigned long long b) noexcept nogil:
    cdef double x, y, r
    cdef unsigned long long v
    cdef int w
    if op == 0 or op == 1:  # ITOR_S / ITOR_U: a is a b-bit integer
        w = <int>b if b else aw
        if w <= 0:
            w = 1
        v = a & ~am
        if w < 64:
            v &= (1ULL << w) - 1
            if op == 0 and (v >> (w - 1)) & 1:
                return _vf_d_to_bits(<double>(<long long>(v | ~((1ULL << w) - 1))))
            return _vf_d_to_bits(<double>v)
        if op == 0:
            return _vf_d_to_bits(<double>(<long long>v))
        return _vf_d_to_bits(<double>v)
    x = _vf_bits_to_d(a)
    if op == 10:  # RTOI
        return _vf_real_to_int(x, 0)
    if op == 11:  # TRUNC
        return _vf_real_to_int(x, 1)
    y = _vf_bits_to_d(b)
    if op == 7:  # LT
        return 1 if x < y else 0
    if op == 8:  # LE
        return 1 if x <= y else 0
    if op == 9:  # EQ
        return 1 if x == y else 0
    if op == 2:
        r = x + y
    elif op == 3:
        r = x - y
    elif op == 4:
        r = x * y
    elif op == 5:
        r = x / y
    else:
        r = _vf_pow(x, y)
    return _vf_d_to_bits(r)
