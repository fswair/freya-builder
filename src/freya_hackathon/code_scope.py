"""Give code_exec snippets their own lexical scope, independent of the host wrapper."""

import ast

STEP = "_builder_code_step"


class Bindings(ast.NodeVisitor):
    """Collect this scope's bindings without descending into child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_FunctionDef(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(self, node: ast.AST) -> None:
        pass

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)


class LocalizeGlobals(ast.NodeTransformer):
    def __init__(self, bindings: set[str]) -> None:
        self.bindings = bindings
        self.depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Global(self, node: ast.Global):
        # Preserve only the unambiguous case: a direct helper referencing a
        # name bound by the snippet itself. Deeper shadowing needs author repair.
        if self.depth != 1 or set(node.names) - self.bindings:
            raise ValueError(
                "code_exec global declaration has no unambiguous snippet-local binding; "
                "use an explicit state dict or function arguments"
            )
        return ast.copy_location(ast.Nonlocal(names=node.names), node)


def isolate_code(code: str) -> str:
    try:
        return _isolate_code(code)
    except SyntaxError as exc:
        raise ValueError(
            f"code_exec has invalid Python bindings at line {exc.lineno}: {exc.msg}"
        ) from None


def _isolate_code(code: str) -> str:
    tree = ast.parse(code)
    if (
        len(tree.body) == 2
        and isinstance(tree.body[0], ast.FunctionDef)
        and tree.body[0].name == STEP
        and isinstance(tree.body[1], ast.Expr)
        and isinstance(tree.body[1].value, ast.Call)
        and isinstance(tree.body[1].value.func, ast.Name)
        and tree.body[1].value.func.id == STEP
    ):
        if any(isinstance(node, ast.Global) for node in ast.walk(tree)):
            raise ValueError("Isolated code_exec must not depend on host globals")
        compile(tree, "<isolated-code-exec>", "exec")
        return code
    bindings = Bindings()
    bindings.visit(tree)
    tree = LocalizeGlobals(bindings.names).visit(tree)
    wrapper = ast.parse(f"def {STEP}(vars):\n    pass\n{STEP}(vars)\n")
    function = wrapper.body[0]
    assert isinstance(function, ast.FunctionDef)
    function.body = tree.body or [ast.Pass()]
    wrapper = ast.fix_missing_locations(wrapper)
    compile(wrapper, "<isolated-code-exec>", "exec")
    return ast.unparse(wrapper) + "\n"
