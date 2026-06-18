"""Design and Module model classes for the Verilog semantic model."""

from __future__ import annotations

import json

from .base import SourceLocation, VerilogNode
from .assignments import ContinuousAssign
from .behavioral import AlwaysBlock, InitialBlock
from .functions import FunctionDecl, TaskDecl
from .generate import GenerateCase, GenerateFor, GenerateIf, GenvarDecl
from .instances import Instance
from .nets import Net
from .parameters import Parameter
from .ports import Port, PortDirection
from .specify import SpecifyBlock
from .sv_types import TypedefDecl
from .interface import Interface
from .package import ImportDecl, Package
from .variables import Variable


class Module(VerilogNode):  # cm:b2d4f1
    """A Verilog module declaration."""

    __slots__ = (
        "always_blocks",
        "attributes",
        "continuous_assigns",
        "functions",
        "generate_blocks",
        "hierarchy_map",
        "imports",
        "initial_blocks",
        "instances",
        "interface_instances",
        "name",
        "nets",
        "parameters",
        "ports",
        "specify_blocks",
        "tasks",
        "typedefs",
        "variables",
    )

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        parameters: list[Parameter] | None = None,
        ports: list[Port] | None = None,
        nets: list[Net] | None = None,
        variables: list[Variable] | None = None,
        instances: list[Instance] | None = None,
        continuous_assigns: list[ContinuousAssign] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.parameters = parameters or []
        self.ports = ports or []
        self.nets = nets or []
        self.variables = variables or []
        self.instances: list[Instance] = instances or []
        self.continuous_assigns: list[ContinuousAssign] = continuous_assigns or []
        # Phase 3 — behavioral (populated by transformer)
        self.always_blocks: list[AlwaysBlock] = []
        self.initial_blocks: list[InitialBlock] = []
        # Phase 5 — functions, tasks, generate
        self.functions: list[FunctionDecl] = []
        self.tasks: list[TaskDecl] = []
        self.generate_blocks: list[GenerateFor | GenerateIf | GenerateCase | GenvarDecl] = []
        self.specify_blocks: list[SpecifyBlock] = []
        self.typedefs: list[TypedefDecl] = []
        self.imports: list[ImportDecl] = []
        self.attributes = {}  # type: ignore[assignment]
        self.hierarchy_map: dict[str, str] = {}
        self.interface_instances: list[tuple[str, Interface]] = []  # (instance_name, interface)

    def __repr__(self) -> str:
        parts = [f"Module({self.name!r}"]
        parts.append(f"ports={len(self.ports)}")
        if self.instances:
            parts.append(f"inst={len(self.instances)}")
        if self.continuous_assigns:
            parts.append(f"assigns={len(self.continuous_assigns)}")
        parts.append(f"nets={len(self.nets)}")
        parts.append(f"vars={len(self.variables)}")
        return ", ".join(parts) + ")"

    def get_port(self, name: str) -> Port | None:
        """Look up a port by name."""
        for p in self.ports:
            if p.name == name:
                return p
        return None

    def get_net(self, name: str) -> Net | None:
        """Look up a net by name."""
        for n in self.nets:
            if n.name == name:
                return n
        return None

    def get_variable(self, name: str) -> Variable | None:
        """Look up a variable by name."""
        for v in self.variables:
            if v.name == name:
                return v
        return None

    def get_parameter(self, name: str) -> Parameter | None:
        """Look up a parameter by name."""
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def input_ports(self) -> list[Port]:
        """Return all input ports."""
        return [p for p in self.ports if p.direction == PortDirection.INPUT]

    def output_ports(self) -> list[Port]:
        """Return all output ports."""
        return [p for p in self.ports if p.direction == PortDirection.OUTPUT]

    def inout_ports(self) -> list[Port]:
        """Return all inout ports."""
        return [p for p in self.ports if p.direction == PortDirection.INOUT]

    def all_signals(self) -> list[Net | Variable]:
        """All nets and variables in declaration order."""
        result: list[Net | Variable] = []
        result.extend(self.nets)
        result.extend(self.variables)
        return result

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        nodes.extend(self.parameters)
        nodes.extend(self.ports)
        nodes.extend(self.nets)
        nodes.extend(self.variables)
        nodes.extend(self.instances)
        nodes.extend(self.continuous_assigns)
        nodes.extend(self.always_blocks)
        nodes.extend(self.initial_blocks)
        nodes.extend(self.functions)
        nodes.extend(self.tasks)
        nodes.extend(self.generate_blocks)
        nodes.extend(self.specify_blocks)
        return nodes

    def to_dict(self) -> dict:  # noqa: PLR0912
        d = super().to_dict()
        d["name"] = self.name
        if self.parameters:
            d["parameters"] = [p.to_dict() for p in self.parameters]
        if self.ports:
            d["ports"] = [p.to_dict() for p in self.ports]
        if self.nets:
            d["nets"] = [n.to_dict() for n in self.nets]
        if self.variables:
            d["variables"] = [v.to_dict() for v in self.variables]
        if self.instances:
            d["instances"] = [i.to_dict() for i in self.instances]
        if self.continuous_assigns:
            d["continuous_assigns"] = [a.to_dict() for a in self.continuous_assigns]
        if self.always_blocks:
            d["always_blocks"] = [a.to_dict() for a in self.always_blocks]
        if self.initial_blocks:
            d["initial_blocks"] = [a.to_dict() for a in self.initial_blocks]
        if self.functions:
            d["functions"] = [f.to_dict() for f in self.functions]
        if self.tasks:
            d["tasks"] = [t.to_dict() for t in self.tasks]
        if self.generate_blocks:
            d["generate_blocks"] = [g.to_dict() for g in self.generate_blocks]
        if self.specify_blocks:
            d["specify_blocks"] = [s.to_dict() for s in self.specify_blocks]
        if self.imports:
            d["imports"] = [i.to_dict() for i in self.imports]
        if self.attributes:
            d["attributes"] = dict(self.attributes)
        if self.hierarchy_map:
            d["hierarchy_map"] = dict(self.hierarchy_map)
        return d


class Design(VerilogNode):  # cm:5c8e7a
    """Root container for a parsed Verilog design."""

    __slots__ = ("interfaces", "modules", "packages", "source_files")

    def __init__(
        self,
        modules: list[Module] | None = None,
        source_files: list[str] | None = None,
        *,
        interfaces: list[Interface] | None = None,
        packages: list[Package] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.modules = modules or []
        self.interfaces: list[Interface] = interfaces or []
        self.packages: list[Package] = packages or []
        self.source_files = source_files or []

    def __repr__(self) -> str:
        return f"Design(modules={len(self.modules)}, files={len(self.source_files)})"

    def get_module(self, name: str) -> Module | None:
        """Look up a module by name."""
        for m in self.modules:
            if m.name == name:
                return m
        return None

    def get_top_modules(self) -> list[Module]:
        """Return modules that are never instantiated by other modules."""
        # Requires Layer 3 (instance resolution) to be accurate.
        # For now, returns all modules.
        instantiated: set[str] = set()
        for m in self.modules:
            for inst in m.instances:
                if hasattr(inst, "module_name"):
                    instantiated.add(inst.module_name)
        return [m for m in self.modules if m.name not in instantiated]

    def merge(self, other: Design) -> None:
        """Merge another Design into this one.

        Modules, interfaces, and packages are added if no item with the
        same name already exists.  Source files are always appended
        (deduplicated).
        """
        existing_modules = {m.name for m in self.modules}
        for m in other.modules:
            if m.name not in existing_modules:
                self.modules.append(m)
                existing_modules.add(m.name)

        existing_intfs = {i.name for i in self.interfaces}
        for i in other.interfaces:
            if i.name not in existing_intfs:
                self.interfaces.append(i)
                existing_intfs.add(i.name)

        existing_pkgs = {p.name for p in self.packages}
        for p in other.packages:
            if p.name not in existing_pkgs:
                self.packages.append(p)
                existing_pkgs.add(p.name)

        existing_files = set(self.source_files)
        for f in other.source_files:
            if f not in existing_files:
                self.source_files.append(f)
                existing_files.add(f)

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.modules)
        nodes.extend(self.interfaces)
        nodes.extend(self.packages)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.source_files:
            d["source_files"] = self.source_files
        d["modules"] = [m.to_dict() for m in self.modules]
        if self.interfaces:
            d["interfaces"] = [i.to_dict() for i in self.interfaces]
        if self.packages:
            d["packages"] = [p.to_dict() for p in self.packages]
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize entire design to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
