# from treelib import Node, Tree
from rich import print  # noqa: A004
from rich.tree import Tree


class relation_def(object):
    """object that stores relationship information for each node"""

    def __init__(self, nname="", is_supported=False, line_number=None, generate_python=None):
        """
        nname = string node (or rule) name
        is_supported = boolean, rule is supported
        line_number = integer, line number of rule from verilog.lark
        generate_python = "yes", "no", "force"
        """
        self.nname = nname
        self.is_supported = is_supported
        self.generate_python = generate_python
        self.line_number = line_number
        self.clst = []
        self.plst = []

    def add_child(self, cname):
        self.clst.append(cname)

    def add_parent(self, pname):
        self.plst.append(pname)


# class vtree(object):
#     def __init__(self, treetop="start", relation_lst=None):
#         self.relation_lst = relation_lst
#         self.clist = []
#         # list of treenodes
#         if self.child is None:
#             self.clst = []

#     def add_subtree(self, child):
#         self.clst.append(child)


# class treenode(object):
#     def __init__(self, nname="", depth=None):
#         self.nname = nname
#         self.depth = depth
#         self.clist = depth
#         # list of treenodes
#         if self.child is None:
#             self.clst = []

#     def add_child(self, child):
#         self.clst.append(child)


def print_tree(
    node_lst=None,
    cur_node_name=None,
    cur_depth=None,
    max_depth=10,
    rtree=None,
    show_all=False,
    visited=None,
    quiet=False,
):
    """
    recursively prints out the parse tree
    """
    if cur_depth > max_depth:
        return

    # Track visited nodes to avoid infinite recursion
    if visited is None:
        visited = set()
    if cur_node_name in visited:
        rtree.add(f"[dim]{cur_node_name}[/dim] (recursive)")
        return
    visited = visited | {cur_node_name}

    # find current node location in list
    node = None
    for n in node_lst:
        if n.nname == cur_node_name:
            node = n
            break

    if node is None:
        # Terminal or unknown - show as leaf
        rtree.add(f"[dim]{cur_node_name}[/dim]")
        return

    if not show_all and not node.is_supported:
        return

    # print current node
    ident = "  " * cur_depth
    support_marker = "" if node.is_supported else " [red](unsupported)[/red]"
    if not quiet:
        print(f"{ident}{cur_node_name}:")
    branch = rtree.add(f"{cur_node_name}:{node.line_number}{support_marker}")
    # branch.guide_style = "bright_red"

    # recursively print each child node
    for cname in node.clst:
        print_tree(
            node_lst=node_lst,
            cur_node_name=cname,
            cur_depth=cur_depth + 1,
            max_depth=max_depth,
            rtree=branch,
            show_all=show_all,
            visited=visited,
            quiet=quiet,
        )


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Generate tree from verilog.lark grammar")
    parser.add_argument("-a", "--all", action="store_true", help="Show all rules, not just supported ones")
    parser.add_argument("-d", "--depth", type=int, default=8, help="Maximum depth to display (default: 8)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only show rich tree, suppress text output")
    parser.add_argument("-r", "--root", type=str, default="verilog", help="Root rule to start from (default: verilog)")
    args = parser.parse_args()

    lark_file = Path(__file__).parent / "verilog.lark"
    with open(lark_file, mode="r") as f:
        vl = f.readlines()

    # find all unique rules
    rule_lst = []  # nodes of tree
    kw_lst = []
    ident_lst = []
    find_child_rules = False
    is_supported = False
    generate_python = "no"
    for line_number, line in enumerate(vl):
        # replace ( ) * ? | ";" "(" ")" "[" "]" ";"
        lnew = line.replace('"', " ")
        lnew = lnew.replace("(", " ")
        lnew = lnew.replace(")", " ")
        lnew = lnew.replace("*", " ")
        lnew = lnew.replace("?", " ")
        lnew = lnew.replace('";"', " ")
        # lnew = lnew.replace('"("', " ")
        lnew = lnew.replace("(", " ")
        # lnew = lnew.replace('")"', " ")
        lnew = lnew.replace(")", " ")
        # lnew = lnew.replace('"["', " ")
        lnew = lnew.replace("[", " ")
        # lnew = lnew.replace('"]"', " ")
        lnew = lnew.replace("]", " ")
        lnew = lnew.replace("|", " ")
        # lnew = lnew.replace('","', " ")
        lnew = lnew.replace(",", " ")
        lnew = lnew.replace(".", " ")
        # lnew = lnew.replace(":", " ")
        lnew = lnew.replace(";", " ")
        lnew = lnew.rstrip()
        # fixme: remove any words in quotes

        if "//" not in lnew:
            if ": " in lnew:
                find_child_rules = False
                rule = lnew.split(": ")
                if "KW_" in rule[0]:
                    kw_lst.append(rule[0])
                elif rule[0].isupper():
                    ident_lst.append(rule[0])
                else:
                    find_child_rules = True
                    rule_new = relation_def(
                        nname=rule[0],
                        is_supported=is_supported,
                        line_number=line_number + 1,
                        generate_python=generate_python,
                    )
                    is_supported = False
                    generate_python = "no"
                    rule_lst.append(rule_new)
                    child_rules = rule[1].split(" ")
            elif find_child_rules:
                child_rules = lnew.split(" ")

            if find_child_rules:
                for child in child_rules:
                    if child != "":
                        rule_lst[-1].add_child(child)
        else:
            if "SUPPORT: YES" in lnew:
                is_supported = True
            if "GENERATE_PYTHON" in lnew:
                if "YES" in lnew:
                    generate_python = "yes"
                elif "NO" in lnew:
                    generate_python = "no"
                elif "FORCE" in lnew:
                    generate_python = "force"
                else:
                    raise Exception('Found "GENERATE_PYTHON=" string without corresponding "YES,NO,FORCE"')

            find_child_rules = False

    # start at top of tree
    # indent with each level traversed
    if not args.quiet:
        for rule in rule_lst:
            if len(rule.clst) == 0:
                print(f"{rule.nname}")

        # for child in child_rules_lst[i]:
        #     if not child:
        #         print(f"{rule}")
        #     else:
        #         print(f"{rule}")
        #     else:

    verbose = False

    for rule in rule_lst:
        if verbose:
            print(f"{rule.nname} - \n children: - ", end="")
            # list all children
            for child in rule.clst:
                print(f"{child},", end="")

        # list all parents
        if verbose:
            print("\n parents: - ", end="")
        for srule in rule_lst:
            for schild in srule.clst:
                if schild == rule.nname:
                    if verbose:
                        print(f"{srule.nname},", end="")
                    rule.add_parent(srule.nname)
        if verbose:
            print("\n")

    # start at top
    # find all children
    # do this recursively

    tree_top = args.root
    rtree = Tree(label=tree_top, guide_style="bold bright_blue")
    print_tree(
        node_lst=rule_lst,
        cur_node_name=tree_top,
        cur_depth=0,
        max_depth=args.depth,
        rtree=rtree,
        show_all=args.all,
        quiet=args.quiet,
    )

    print(rtree)
