"""Simulation endpoint helpers.

This package holds transaction-level helpers that sit on top of the existing
Simulator API.
"""

from .axi_lite_request_driver import AXILiteRequestDriver
from .axi_lite_response_driver import AXILiteResponseDriver
from .axi_lite_responder import AXILiteResponder, AXILiteProtocolError
from .axi_lite_master import AXILiteMaster, AXILiteResponseError
from .axi4_master import AXI4Master, AXI4ResponseError
from .axi4_responder import AXI4Responder
from .axis_sink import AXIStreamSink
from .axis_source import AXIStreamSource
from .detect import (
    DetectedInterface,
    InterfaceDetectionError,
    NearMissInterface,
    detect_axi4_interfaces,
    detect_axi_lite_interfaces,
    detect_axi_stream_interfaces,
    detect_interfaces,
    detect_membus_interfaces,
    detect_near_misses,
    detect_relaxed_interfaces,
    detect_stream_interfaces,
)
from ._generator import GeneratorEndpoint
from .frame import AXIStreamFrame, BeatSizeError, ElementSizeError
from .helpers import CombinationalCoordinator, DomainCoordinator, EndpointCoordinator, MultiDomainRunner
from .membus_master import MemBusMaster
from .membus_responder import MemBusResponder
from .pause import PauseGenerator
from .stream_sink import StreamSink
from .stream_source import StreamSource

__all__ = [
    "AXI4Master",
    "AXI4Responder",
    "AXI4ResponseError",
    "AXILiteMaster",
    "AXILiteProtocolError",
    "AXILiteRequestDriver",
    "AXILiteResponder",
    "AXILiteResponseDriver",
    "AXILiteResponseError",
    "AXIStreamFrame",
    "AXIStreamSink",
    "AXIStreamSource",
    "BeatSizeError",
    "CombinationalCoordinator",
    "DetectedInterface",
    "DomainCoordinator",
    "ElementSizeError",
    "EndpointCoordinator",
    "GeneratorEndpoint",
    "InterfaceDetectionError",
    "MemBusMaster",
    "MemBusResponder",
    "MultiDomainRunner",
    "NearMissInterface",
    "PauseGenerator",
    "StreamSink",
    "StreamSource",
    "detect_axi4_interfaces",
    "detect_axi_lite_interfaces",
    "detect_axi_stream_interfaces",
    "detect_interfaces",
    "detect_membus_interfaces",
    "detect_near_misses",
    "detect_relaxed_interfaces",
    "detect_stream_interfaces",
]
