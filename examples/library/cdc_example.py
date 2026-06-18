"""Example: CDC synchronizer and edge detectors from the library."""

from veriforge.codegen import emit_module
from veriforge.dsl.lib import edge_detector, synchronizer


def main():
    # 2-stage CDC synchronizer (standard for single-bit signals)
    sync2 = synchronizer(width=1, stages=2)
    print("=== 2-Stage Synchronizer ===")
    print(emit_module(sync2.build()))

    # 3-stage synchronizer for higher MTBF
    sync3 = synchronizer(width=1, stages=3, name="sync_3stage")
    print("\n=== 3-Stage Synchronizer ===")
    print(emit_module(sync3.build()))

    # Multi-bit synchronizer (use with caution — gray coding recommended)
    sync_bus = synchronizer(width=4, stages=2, name="bus_sync")
    print("\n=== 4-bit Bus Synchronizer ===")
    print(emit_module(sync_bus.build()))

    # Edge detectors
    for edge in ("rising", "falling", "any"):
        det = edge_detector(edge)
        print(f"\n=== {edge.title()} Edge Detector ===")
        print(emit_module(det.build()))


if __name__ == "__main__":
    main()
