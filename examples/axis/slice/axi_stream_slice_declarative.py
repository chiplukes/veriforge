"""axi_stream_slice, rebuilt in the declarative ``ModuleSpec`` style.

``axi_stream_slice.py`` declares every port imperatively (``m.input(...)``,
``m.output_reg(...)``); ``axi_stream_slice_iface.py`` declares the two
AXI-Stream buses in one call each via ``m.interface()``. This module
declares the *same* signals as ``axi_stream_slice.py`` -- individual raw
ports, not a bound bus -- but as ``ModuleSpec`` class attributes
(``In()``/``OutReg()``/``Reg()``/``Wire()``) instead of imperative builder
calls, per ``notes/dsl/dsl_guide.md``'s "declarative is the default/
recommended style for anything with a fixed interface" guidance.

The catch: ``tuser`` and ``tlast`` are only present when ``tuser_width`` /
``has_tlast`` say so, so the interface *isn't* fixed at class-body-text
time. The fix used here -- and the reason this file is a factory function
rather than a bare module-level class, matching
``top/gfwx_encode_core/dsl/gfwx_encode_core.py`` in gfwx-fpga -- is that the
``class AxiStreamSlice(ModuleSpec): ...`` body executes once *per call* to
:func:`build_axi_stream_slice_declarative`, with ``data_width``/``tuser_width``/
``has_tlast`` already bound in the enclosing closure. A plain ``if`` inside
the class body conditionally skips a descriptor assignment, which skips
``__set_name__`` firing for it, which skips the port entirely -- ordinary
Python control flow, no DSL trick needed. This is the boundary the DSL
guide draws for reaching for the imperative builder instead (a `for` loop,
a variable-length bus expansion); a plain `if` guarding a handful of named,
individually-typed optional ports still reads fine as a declarative class
body.

Functionally identical to ``axi_stream_slice.py`` -- see
``test_axi_stream_slice.py::test_declarative_variant_matches_raw``.
"""

from veriforge.dsl import In, ModuleSpec, OutReg, Reg, Wire, posedge


def build_axi_stream_slice_declarative(
    data_width=32, tuser_width=0, has_tlast=True, name="axi_stream_slice_declarative"
):
    class AxiStreamSliceDeclarative(ModuleSpec):
        module_name = name

        clk = In()
        rst = In()

        s_axis_tvalid = In()
        s_axis_tready = OutReg(init=1)
        s_axis_tdata = In(data_width)
        if tuser_width:
            s_axis_tuser = In(tuser_width)
        if has_tlast:
            s_axis_tlast = In()

        m_axis_tvalid = OutReg(init=0)
        m_axis_tready = In()
        m_axis_tdata = OutReg(data_width, init=0)
        if tuser_width:
            m_axis_tuser = OutReg(tuser_width, init=0)
        if has_tlast:
            m_axis_tlast = OutReg(init=0)

        # Skid register: catches the beat s_axis_tready already accepted the
        # cycle m_axis_tvalid&~m_axis_tready first held the output register
        # full.
        r_tdata = Reg(data_width, init=0)
        if tuser_width:
            r_tuser = Reg(tuser_width, init=0)
        if has_tlast:
            r_tlast = Reg(init=0)
        r_tvalid_w = Wire()

        def body(self, m):
            self.r_tvalid_w.assign = ~self.s_axis_tready

            with m.always(posedge(self.clk)):
                with m.if_(self.rst):
                    self.m_axis_tvalid <<= 0
                    self.s_axis_tready <<= 1
                    self.r_tdata <<= 0
                    if tuser_width:
                        self.r_tuser <<= 0
                    if has_tlast:
                        self.r_tlast <<= 0
                    self.m_axis_tdata <<= 0
                    if tuser_width:
                        self.m_axis_tuser <<= 0
                    if has_tlast:
                        self.m_axis_tlast <<= 0

                with m.else_():
                    with m.if_(~self.m_axis_tvalid | self.m_axis_tready):
                        self.m_axis_tvalid <<= self.s_axis_tvalid | self.r_tvalid_w

                    with m.if_((self.s_axis_tvalid & self.s_axis_tready) & (self.m_axis_tvalid & ~self.m_axis_tready)):
                        self.s_axis_tready <<= 0
                    with m.elif_(self.m_axis_tready):
                        self.s_axis_tready <<= 1

                    with m.if_(self.s_axis_tready):
                        self.r_tdata <<= self.s_axis_tdata
                        if tuser_width:
                            self.r_tuser <<= self.s_axis_tuser
                        if has_tlast:
                            self.r_tlast <<= self.s_axis_tlast

                    with m.if_(self.m_axis_tready | ~self.m_axis_tvalid):
                        with m.if_(self.r_tvalid_w):
                            self.m_axis_tdata <<= self.r_tdata
                            if tuser_width:
                                self.m_axis_tuser <<= self.r_tuser
                            if has_tlast:
                                self.m_axis_tlast <<= self.r_tlast
                        with m.elif_(self.s_axis_tvalid & self.s_axis_tready):
                            self.m_axis_tdata <<= self.s_axis_tdata
                            if tuser_width:
                                self.m_axis_tuser <<= self.s_axis_tuser
                            if has_tlast:
                                self.m_axis_tlast <<= self.s_axis_tlast

    return AxiStreamSliceDeclarative().build()
