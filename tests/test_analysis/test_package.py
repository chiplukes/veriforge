"""Tests for SystemVerilog package and import support."""

from veriforge.codegen.verilog_emitter import emit_design, emit_package, _emit_import
from veriforge.model.design import Design
from veriforge.model.package import ImportDecl, Package
from veriforge.model.parameters import Parameter
from veriforge.model.expressions import Literal
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(src: str):
    """Parse Verilog source and return the design."""
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(text=src)
    return tree_to_design(tree)


def _parse_package(src: str):
    """Parse source and return the first package."""
    design = _parse(src)
    assert len(design.packages) >= 1, f"Expected package, got {design}"
    return design.packages[0]


def _parse_module(src: str):
    """Parse source and return the first module."""
    design = _parse(src)
    assert len(design.modules) >= 1, f"Expected module, got {design}"
    return design.modules[0]


# ---------------------------------------------------------------------------
# Grammar parse tests
# ---------------------------------------------------------------------------


class TestGrammarParse:
    """Verify that the grammar accepts package/import syntax."""

    def test_minimal_package(self):
        src = "package my_pkg; endpackage\n"
        pkg = _parse_package(src)
        assert pkg.name == "my_pkg"

    def test_package_with_localparam(self):
        src = """\
package my_pkg;
    localparam WIDTH = 8;
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "my_pkg"

    def test_package_with_parameter(self):
        src = """\
package my_pkg;
    parameter DATA_W = 32;
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "my_pkg"

    def test_package_with_typedef(self):
        src = """\
package my_pkg;
    typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "my_pkg"

    def test_package_with_function(self):
        src = """\
package math_pkg;
    function integer add;
        input integer a;
        input integer b;
        add = a + b;
    endfunction
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "math_pkg"

    def test_package_with_task(self):
        src = """\
package util_pkg;
    task print_val;
        input integer val;
        ;
    endtask
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "util_pkg"

    def test_package_with_import(self):
        src = """\
package pkg_b;
    import pkg_a::WIDTH;
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "pkg_b"

    def test_import_wildcard_in_module(self):
        src = """\
module top;
    import my_pkg::*;
endmodule
"""
        mod = _parse_module(src)
        assert mod.name == "top"

    def test_import_named_in_module(self):
        src = """\
module top;
    import my_pkg::WIDTH;
endmodule
"""
        mod = _parse_module(src)
        assert mod.name == "top"

    def test_multiple_imports_in_module(self):
        src = """\
module top;
    import pkg_a::*;
    import pkg_b::DEPTH;
endmodule
"""
        mod = _parse_module(src)
        assert mod.name == "top"

    def test_import_multi_item_single_statement(self):
        src = """\
module top;
    import my_pkg::A, my_pkg::B;
endmodule
"""
        mod = _parse_module(src)
        assert mod.name == "top"

    def test_package_and_module_together(self):
        src = """\
package my_pkg;
    localparam WIDTH = 8;
endpackage

module top;
    import my_pkg::*;
    wire [7:0] data;
endmodule
"""
        design = _parse(src)
        assert len(design.packages) == 1
        assert len(design.modules) == 1


# ---------------------------------------------------------------------------
# Model extraction tests
# ---------------------------------------------------------------------------


class TestModelExtraction:
    """Verify that the transformer correctly builds model objects."""

    def test_package_name(self):
        src = "package my_pkg; endpackage\n"
        pkg = _parse_package(src)
        assert pkg.name == "my_pkg"

    def test_package_localparam_extraction(self):
        src = """\
package my_pkg;
    localparam WIDTH = 8;
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.parameters) == 1
        assert pkg.parameters[0].name == "WIDTH"
        assert pkg.parameters[0].is_local is True

    def test_package_parameter_extraction(self):
        src = """\
package my_pkg;
    parameter DATA_W = 32;
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.parameters) == 1
        assert pkg.parameters[0].name == "DATA_W"
        assert pkg.parameters[0].is_local is False

    def test_package_typedef_extraction(self):
        src = """\
package my_pkg;
    typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.typedefs) == 1
        assert pkg.typedefs[0].name == "state_t"

    def test_package_function_extraction(self):
        src = """\
package math_pkg;
    function integer add;
        input integer a;
        input integer b;
        add = a + b;
    endfunction
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.functions) == 1
        assert pkg.functions[0].name == "add"

    def test_package_task_extraction(self):
        src = """\
package util_pkg;
    task print_val;
        input integer val;
        ;
    endtask
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.tasks) == 1
        assert pkg.tasks[0].name == "print_val"

    def test_package_import_extraction(self):
        src = """\
package pkg_b;
    import pkg_a::WIDTH;
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.imports) == 1
        assert pkg.imports[0].package_name == "pkg_a"
        assert pkg.imports[0].item_name == "WIDTH"
        assert pkg.imports[0].is_wildcard is False

    def test_module_import_wildcard(self):
        src = """\
module top;
    import my_pkg::*;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.imports) == 1
        assert mod.imports[0].package_name == "my_pkg"
        assert mod.imports[0].item_name == "*"
        assert mod.imports[0].is_wildcard is True

    def test_module_import_named(self):
        src = """\
module top;
    import my_pkg::WIDTH;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.imports) == 1
        assert mod.imports[0].package_name == "my_pkg"
        assert mod.imports[0].item_name == "WIDTH"
        assert mod.imports[0].is_wildcard is False

    def test_multiple_import_statements(self):
        src = """\
module top;
    import pkg_a::*;
    import pkg_b::DEPTH;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.imports) == 2
        assert mod.imports[0].package_name == "pkg_a"
        assert mod.imports[0].is_wildcard is True
        assert mod.imports[1].package_name == "pkg_b"
        assert mod.imports[1].item_name == "DEPTH"

    def test_multi_item_import(self):
        src = """\
module top;
    import my_pkg::A, my_pkg::B;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.imports) == 2
        assert mod.imports[0].item_name == "A"
        assert mod.imports[1].item_name == "B"

    def test_design_packages_list(self):
        src = """\
package pkg_a;
    localparam A = 1;
endpackage

package pkg_b;
    localparam B = 2;
endpackage
"""
        design = _parse(src)
        assert len(design.packages) == 2
        assert design.packages[0].name == "pkg_a"
        assert design.packages[1].name == "pkg_b"

    def test_parent_references(self):
        src = """\
package my_pkg;
    localparam WIDTH = 8;
endpackage
"""
        pkg = _parse_package(src)
        for p in pkg.parameters:
            assert p.parent is pkg


# ---------------------------------------------------------------------------
# Emitter tests
# ---------------------------------------------------------------------------


class TestEmitter:
    """Verify emitter output for packages and imports."""

    def test_emit_empty_package(self):
        pkg = Package(name="empty_pkg")
        result = emit_package(pkg)
        assert "package empty_pkg;" in result
        assert "endpackage" in result

    def test_emit_package_with_localparam(self):
        param = Parameter(name="WIDTH", default_value=Literal("8"), is_local=True)
        pkg = Package(name="my_pkg", parameters=[param])
        result = emit_package(pkg)
        assert "package my_pkg;" in result
        assert "localparam WIDTH = 8;" in result
        assert "endpackage" in result

    def test_emit_package_with_parameter(self):
        param = Parameter(name="DATA_W", default_value=Literal("32"), is_local=False)
        pkg = Package(name="my_pkg", parameters=[param])
        result = emit_package(pkg)
        assert "package my_pkg;" in result
        assert "parameter DATA_W = 32;" in result
        assert "endpackage" in result

    def test_emit_import_named(self):
        imp = ImportDecl(package_name="my_pkg", item_name="WIDTH")
        result = _emit_import(imp, "    ")
        assert result == "    import my_pkg::WIDTH;"

    def test_emit_import_wildcard(self):
        imp = ImportDecl(package_name="my_pkg", item_name="*")
        result = _emit_import(imp, "    ")
        assert result == "    import my_pkg::*;"

    def test_emit_design_with_package(self):
        pkg = Package(name="my_pkg")
        design = Design(packages=[pkg])
        result = emit_design(design)
        assert "package my_pkg;" in result
        assert "endpackage" in result


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Verify parse → emit → parse round-trip fidelity."""

    def test_round_trip_minimal_package(self):
        src = "package my_pkg;\n\nendpackage\n"
        pkg = _parse_package(src)
        result = emit_package(pkg)
        assert "package my_pkg;" in result
        assert "endpackage" in result

    def test_round_trip_package_with_localparam(self):
        src = """\
package my_pkg;
    localparam WIDTH = 8;
endpackage
"""
        pkg = _parse_package(src)
        emitted = emit_package(pkg)
        assert "localparam WIDTH = 8;" in emitted
        # Parse again
        full_src = emitted + "\n"
        design2 = _parse(full_src)
        assert len(design2.packages) == 1
        pkg2 = design2.packages[0]
        assert pkg2.name == "my_pkg"
        assert len(pkg2.parameters) == 1
        assert pkg2.parameters[0].name == "WIDTH"

    def test_round_trip_import_in_module(self):
        src = """\
module top;
    import my_pkg::*;
endmodule
"""
        design = _parse(src)
        emitted = emit_design(design)
        assert "import my_pkg::*;" in emitted
        # Parse again
        design2 = _parse(emitted)
        assert len(design2.modules) == 1
        mod2 = design2.modules[0]
        assert len(mod2.imports) == 1
        assert mod2.imports[0].is_wildcard is True

    def test_round_trip_named_import(self):
        src = """\
module top;
    import my_pkg::WIDTH;
endmodule
"""
        design = _parse(src)
        emitted = emit_design(design)
        assert "import my_pkg::WIDTH;" in emitted
        design2 = _parse(emitted)
        mod2 = design2.modules[0]
        assert mod2.imports[0].package_name == "my_pkg"
        assert mod2.imports[0].item_name == "WIDTH"

    def test_round_trip_package_and_module(self):
        src = """\
package cfg_pkg;
    localparam DEPTH = 16;
endpackage

module consumer;
    import cfg_pkg::DEPTH;
endmodule
"""
        design = _parse(src)
        emitted = emit_design(design)
        assert "package cfg_pkg;" in emitted
        assert "localparam DEPTH = 16;" in emitted
        assert "endpackage" in emitted
        assert "import cfg_pkg::DEPTH;" in emitted

    def test_round_trip_package_with_typedef(self):
        src = """\
package type_pkg;
    typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
endpackage
"""
        pkg = _parse_package(src)
        emitted = emit_package(pkg)
        assert "package type_pkg;" in emitted
        assert "typedef" in emitted
        assert "state_t" in emitted
        assert "endpackage" in emitted


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and special scenarios."""

    def test_empty_package(self):
        src = "package empty_pkg; endpackage\n"
        pkg = _parse_package(src)
        assert pkg.name == "empty_pkg"
        assert len(pkg.parameters) == 0
        assert len(pkg.typedefs) == 0
        assert len(pkg.functions) == 0
        assert len(pkg.tasks) == 0
        assert len(pkg.imports) == 0

    def test_multiple_localparams_in_package(self):
        src = """\
package cfg;
    localparam A = 1;
    localparam B = 2;
    localparam C = 3;
endpackage
"""
        pkg = _parse_package(src)
        assert len(pkg.parameters) == 3
        names = [p.name for p in pkg.parameters]
        assert "A" in names
        assert "B" in names
        assert "C" in names

    def test_package_repr(self):
        pkg = Package(name="test_pkg")
        r = repr(pkg)
        assert "Package" in r
        assert "test_pkg" in r

    def test_import_repr(self):
        imp = ImportDecl(package_name="pkg", item_name="X")
        r = repr(imp)
        assert "ImportDecl" in r
        assert "pkg" in r
        assert "X" in r

    def test_import_to_dict(self):
        imp = ImportDecl(package_name="pkg", item_name="*")
        d = imp.to_dict()
        assert d["package_name"] == "pkg"
        assert d["item_name"] == "*"

    def test_package_to_dict(self):
        param = Parameter(name="W", default_value=Literal("8"), is_local=True)
        pkg = Package(name="test_pkg", parameters=[param])
        d = pkg.to_dict()
        assert d["name"] == "test_pkg"
        assert len(d["parameters"]) == 1

    def test_import_is_wildcard_property(self):
        wc = ImportDecl(package_name="pkg", item_name="*")
        assert wc.is_wildcard is True
        named = ImportDecl(package_name="pkg", item_name="FOO")
        assert named.is_wildcard is False

    def test_package_child_nodes(self):
        param = Parameter(name="X", default_value=Literal("1"), is_local=True)
        imp = ImportDecl(package_name="other", item_name="*")
        pkg = Package(name="test_pkg", parameters=[param], imports=[imp])
        children = pkg._child_nodes()
        assert len(children) == 2  # param + import

    def test_module_imports_with_other_declarations(self):
        src = """\
module mixed;
    import my_pkg::*;
    wire [7:0] data;
    reg valid;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.imports) == 1
        assert len(mod.nets) >= 1
        assert len(mod.variables) >= 1

    def test_package_mixed_content(self):
        src = """\
package full_pkg;
    localparam WIDTH = 8;
    parameter DEPTH = 16;
    typedef enum logic [1:0] {IDLE, RUN} state_t;
    function integer double;
        input integer x;
        double = x * 2;
    endfunction
endpackage
"""
        pkg = _parse_package(src)
        assert pkg.name == "full_pkg"
        local_params = [p for p in pkg.parameters if p.is_local]
        non_local_params = [p for p in pkg.parameters if not p.is_local]
        assert len(local_params) >= 1
        assert len(non_local_params) >= 1
        assert len(pkg.typedefs) >= 1
        assert len(pkg.functions) >= 1
