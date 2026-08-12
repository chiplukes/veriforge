"""axi_stream_slice, declarative ``ModuleSpec`` style AND interface-bound buses.

Combines the two extensibility angles demonstrated separately elsewhere in
this directory: ``axi_stream_slice_declarative.py`` (declarative class-attribute
ports) and ``axi_stream_slice_iface.py`` (``m.interface()``-bound AXI-Stream
buses instead of individual ports).

``notes/dsl/dsl_guide.md`` lists "you're expanding a bus ``Interface``" as
one of the specific cases where the *imperative* builder is still the right
tool inside an otherwise-declarative module -- binding a variable-width,
prefix-driven signal group isn't a fixed, nameable class attribute the way
``clk = In()`` is. The DSL guide's own "Mixing both styles in one module"
section, and gfwx-fpga's ``top/gfwx_encode_core/dsl/gfwx_encode_core.py``
(the project's own reference example), show the resolution: declare the
*fixed* signals (clock, reset, internal registers) as ``ModuleSpec`` class
attributes as usual, and call ``m.interface()`` procedurally inside
``body(self, m)`` for the bus ports, same as the plain imperative builder
would. The two are not an either/or choice.

```python
class AxiStreamSliceDeclarativeIface(ModuleSpec):
    clk = In()
    rst = In()
    r_tdata = Reg(data_width, init=0)          # declarative: fixed signals

    def body(self, m):
        s = m.interface("s_axis", intf, role="slave", reg=True)    # procedural: the bus
        out = m.interface("m_axis", intf, role="master", reg=True)
        ...
```

Same trade-off as ``axi_stream_slice_iface.py``: the library's ``axi_stream()``
interface always includes ``tlast`` (no ``has_tlast=False`` here), and
``reg=True`` doesn't expose a per-signal ``init=``, so ``s_axis_tready``'s
power-on default comes from the reset branch alone. Functionally identical
to the other three variants once out of reset -- see
``test_axi_stream_slice.py::test_declarative_iface_variant_matches_raw``.
"""

from veriforge.dsl import In, ModuleSpec, Reg, Wire, posedge
from veriforge.dsl.lib.axi_stream import axi_stream


def build_axi_stream_slice_declarative_iface(data_width=32, tuser_width=0, name="axi_stream_slice_declarative_iface"):
    intf = axi_stream(data_width, tuser_width=tuser_width)

    class AxiStreamSliceDeclarativeIface(ModuleSpec):
        module_name = name

        clk = In()
        rst = In()

        # Skid register: catches the beat s_axis_tready already accepted the
        # cycle m_axis_tvalid&~m_axis_tready first held the output register
        # full.
        r_tdata = Reg(data_width, init=0)
        if tuser_width:
            r_tuser = Reg(tuser_width, init=0)
        r_tlast = Reg(init=0)
        r_tvalid_w = Wire()

        def body(self, m):
            s = m.interface("s_axis", intf, role="slave", reg=True)  # s.tready is output_reg
            out = m.interface("m_axis", intf, role="master", reg=True)  # out.tvalid/tdata/tuser/tlast are output_reg

            self.r_tvalid_w.assign = ~s.tready

            with m.always(posedge(self.clk)):
                with m.if_(self.rst):
                    out.tvalid <<= 0
                    s.tready <<= 1
                    self.r_tdata <<= 0
                    if tuser_width:
                        self.r_tuser <<= 0
                    self.r_tlast <<= 0
                    out.tdata <<= 0
                    if tuser_width:
                        out.tuser <<= 0
                    out.tlast <<= 0

                with m.else_():
                    with m.if_(~out.tvalid | out.tready):
                        out.tvalid <<= s.tvalid | self.r_tvalid_w

                    # Same stall-detect condition as the other three
                    # variants -- (s.tvalid & s.tready), not
                    # (s.tvalid & s.tvalid); see axi_stream_slice.py.
                    with m.if_((s.tvalid & s.tready) & (out.tvalid & ~out.tready)):
                        s.tready <<= 0
                    with m.elif_(out.tready):
                        s.tready <<= 1

                    with m.if_(s.tready):
                        self.r_tdata <<= s.tdata
                        if tuser_width:
                            self.r_tuser <<= s.tuser
                        self.r_tlast <<= s.tlast

                    with m.if_(out.tready | ~out.tvalid):
                        with m.if_(self.r_tvalid_w):
                            out.tdata <<= self.r_tdata
                            if tuser_width:
                                out.tuser <<= self.r_tuser
                            out.tlast <<= self.r_tlast
                        with m.elif_(s.tvalid & s.tready):
                            out.tdata <<= s.tdata
                            if tuser_width:
                                out.tuser <<= s.tuser
                            out.tlast <<= s.tlast

    return AxiStreamSliceDeclarativeIface().build()
