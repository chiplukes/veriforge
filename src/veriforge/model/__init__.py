"""Verilog Semantic Model — public API.

Usage:
    from veriforge.model import Design, Module, Port, Net, Variable, Parameter
    from veriforge.model import Instance, PortConnection, ParameterBinding
    from veriforge.model import ContinuousAssign
    from veriforge.model import AlwaysBlock, InitialBlock, SensitivityType
    from veriforge.model import (
        Statement, BlockingAssign, NonblockingAssign, IfStatement, CaseStatement,
        CaseItem, ForLoop, WhileLoop, ForeverLoop, RepeatLoop, SeqBlock, ParBlock,
        WaitStatement, DisableStatement, EventTrigger, TaskEnable, SystemTaskCall,
        DelayControl, EventControl, SensitivityEdge,
    )
    from veriforge.model import PortDirection, NetKind, VariableKind
    from veriforge.model import Expression, Identifier, Literal, BinaryOp, Range
    from veriforge.model import VerilogNode, SourceLocation, Comment
"""

# Assignments
from .assignments import ContinuousAssign

# Base classes
from .base import Comment, SourceLocation, VerilogNode

# Behavioral
from .behavioral import AlwaysBlock, InitialBlock, SensitivityType

# Design & Module
from .design import Design, Module

# Functions & Tasks
from .functions import FunctionDecl, TaskDecl

# Generate constructs
from .generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)

# Expressions
from .expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)

# Instances
from .instances import Instance, ParameterBinding, PortConnection

# Nets
from .nets import Net, NetKind

# Parameters
from .parameters import Parameter

# Ports
from .ports import Port, PortDirection

# Specify
from .specify import SpecifyBlock

# SV types
from .sv_types import EnumMember, EnumType, StructField, StructType, TypedefDecl, UnionType

# Interfaces
from .interface import Interface, Modport, ModportPort

# Packages
from .package import ImportDecl, Package

# Statements
from .statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventControl,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
    IfStatement,
)

# Variables
from .variables import Variable, VariableKind

__all__ = [
    "AlwaysBlock",
    "BinaryOp",
    "BitSelect",
    "BlockingAssign",
    "CaseItem",
    "CaseStatement",
    "Comment",
    "Concatenation",
    "ContinuousAssign",
    "DelayControl",
    "Design",
    "DisableStatement",
    "EnumMember",
    "EnumType",
    "EventControl",
    "EventTrigger",
    "Expression",
    "ForLoop",
    "ForeverLoop",
    "FunctionCall",
    "FunctionDecl",
    "GenerateBlock",
    "GenerateCase",
    "GenerateCaseItem",
    "GenerateFor",
    "GenerateIf",
    "GenvarDecl",
    "Identifier",
    "IfStatement",
    "ImportDecl",
    "InitialBlock",
    "Instance",
    "Interface",
    "Literal",
    "Mintypmax",
    "Modport",
    "ModportPort",
    "Module",
    "Net",
    "NetKind",
    "NonblockingAssign",
    "Package",
    "ParBlock",
    "Parameter",
    "ParameterBinding",
    "PartSelect",
    "Port",
    "PortConnection",
    "PortDirection",
    "Range",
    "RangeSelect",
    "RepeatLoop",
    "Replication",
    "SensitivityEdge",
    "SensitivityType",
    "SeqBlock",
    "SourceLocation",
    "SpecifyBlock",
    "Statement",
    "StringLiteral",
    "StructField",
    "StructType",
    "SystemTaskCall",
    "TaskDecl",
    "TaskEnable",
    "TernaryOp",
    "TypedefDecl",
    "UnaryOp",
    "UnionType",
    "Variable",
    "VariableKind",
    "VerilogNode",
    "WaitStatement",
    "WhileLoop",
]
