"""Backward-compatible re-export shim.

Testbench wrapper generation used to live here. It now lives in
``veriforge.sim.bench.skeleton`` (moved there to break a sim <-> dsl import
cycle: the code depends on ``sim.endpoints``, and ``sim.bench.lowering``
depends on ``dsl`` -- both under ``dsl`` created a cycle at the package
level). Existing code using ``from veriforge.dsl.testbench import ...``
continues to work unchanged via this shim. New code should import from
``veriforge.sim.bench.skeleton`` directly.
"""

from veriforge.sim.bench.skeleton import *  # noqa: F401, F403
from veriforge.sim.bench.skeleton import (  # noqa: F401
    _is_active_low_reset,
    _is_clock,
    _is_reset,
    _port_width_int,
)
