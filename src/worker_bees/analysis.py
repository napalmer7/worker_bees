"""Conservative lexical call analysis, not runtime dispatch or whole-program analysis."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from worker_bees.models import Call, Document, Index, Issue, Symbol
from worker_bees.repository import read_snapshot


@dataclass
class Binding:
    target: str
    kind: str  # symbol ID or imported qualified name


@dataclass
class Scope:
    owner: Symbol
    parent: "Scope | None"
    kind: str
    bindings: dict[str, list[Binding | None]] = field(default_factory=dict)
    opaque: bool = False

    def bind(self, name: str, binding: Binding | None = None) -> None:
        self.bindings.setdefault(name, []).append(binding)

    def lookup(self, name: str) -> Binding | None:
        scope: Scope | None = self
        while scope:
            if scope.opaque:
                return None
            if name in scope.bindings:
                choices = scope.bindings[name]
                return choices[0] if len(choices) == 1 else None
            scope = scope.parent
            # Method and nested function bodies do not close over class namespaces.
            while scope and scope.kind == "class":
                scope = scope.parent
        return None


class Collector(ast.NodeVisitor):
    def __init__(self, document: Document):
        self.document = document
        module = Symbol(
            path=document.path,
            name=document.module,
            qualified_name=document.module,
            kind="module",
            line=1,
            end_line=max(1, len(document.source.splitlines())),
        )
        self.symbols = [module]
        self.scope = Scope(module, None, "module")
        self.calls: list[tuple[ast.Call, Scope]] = []
        self.conditional = 0

    def definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        is_class = isinstance(node, ast.ClassDef)
        kind: Literal["class", "method", "async_function", "function"]
        if is_class:
            kind = "class"
        elif self.scope.kind == "class":
            kind = "method"
        elif isinstance(node, ast.AsyncFunctionDef):
            kind = "async_function"
        else:
            kind = "function"
        symbol = Symbol(
            path=self.document.path,
            name=node.name,
            qualified_name=f"{self.scope.owner.qualified_name}.{node.name}",
            kind=kind,
            line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            signature=None if isinstance(node, ast.ClassDef) else ast.unparse(node.args),
            docstring=ast.get_docstring(node),
        )
        self.symbols.append(symbol)
        decorated = bool(node.decorator_list)
        binding = None if decorated or self.conditional else Binding(symbol.id, "symbol")
        self.scope.bind(node.name, binding)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, ast.ClassDef):
            for expression in [*node.bases, *(item.value for item in node.keywords)]:
                self.visit(expression)
        else:
            # Defaults and annotations belong to the enclosing scope, not the new body.
            self.visit(node.args)
            if node.returns:
                self.visit(node.returns)
        previous, conditional = self.scope, self.conditional
        self.scope = Scope(symbol, previous, "class" if is_class else "function")
        if node.type_params:
            self.scope.opaque = True
        self.conditional = 0
        if not isinstance(node, ast.ClassDef):
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                self.scope.bind(arg.arg)
            for extra in (args.vararg, args.kwarg):
                if extra:
                    self.scope.bind(extra.arg)
        for statement in node.body:
            self.visit(statement)
        self.scope, self.conditional = previous, conditional

    visit_FunctionDef = definition
    visit_AsyncFunctionDef = definition
    visit_ClassDef = definition

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scope.bind(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((node, self.scope))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else name
            self.scope.bind(name, None if self.conditional else Binding(target, "import"))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        package = self.document.module.split(".")
        if not self.document.is_package:
            package = package[:-1]
        valid = node.level == 0 or node.level <= len(package)
        if node.level:
            base = package[: len(package) - node.level + 1]
            module = ".".join([*base, *([node.module] if node.module else [])])
        else:
            module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.scope.opaque = True
            else:
                binding = Binding(f"{module}.{alias.name}", "import")
                self.scope.bind(
                    alias.asname or alias.name,
                    binding if valid and not self.conditional else None,
                )

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.scope.bind(name)
            module_scope = self.scope
            while module_scope.parent:
                module_scope = module_scope.parent
            module_scope.bind(name)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.scope.bind(name)
            parent = self.scope.parent
            while parent:
                if parent.kind == "function":
                    parent.bind(name)
                parent = parent.parent

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            root: ast.expr = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                # A known mutation of an imported namespace invalidates static lookup.
                scope: Scope | None = self.scope
                while scope:
                    if root.id in scope.bindings:
                        scope.bind(root.id)
                        break
                    if scope.parent is None:
                        scope.bind(root.id)
                        break
                    scope = scope.parent
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.scope.bind(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.scope.bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.scope.bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.scope.bind(node.rest)
        self.generic_visit(node)

    def conditional_block(self, node: ast.AST) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    visit_If = conditional_block
    visit_For = conditional_block
    visit_AsyncFor = conditional_block
    visit_While = conditional_block
    visit_Try = conditional_block
    visit_TryStar = conditional_block
    visit_Match = conditional_block
    visit_With = conditional_block
    visit_AsyncWith = conditional_block

    def opaque_expression(self, node: ast.AST) -> None:
        # These scopes need separate binding/flow analysis. Preserve calls as unknown.
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                self.scope.bind(child.target.id)
        previous = self.scope
        self.scope = Scope(previous.owner, previous, "expression", opaque=True)
        self.generic_visit(node)
        self.scope = previous

    visit_Lambda = opaque_expression
    visit_ListComp = opaque_expression
    visit_SetComp = opaque_expression
    visit_DictComp = opaque_expression
    visit_GeneratorExp = opaque_expression


def resolve_call(
    node: ast.Call,
    scope: Scope,
    by_name: dict[str, list[Symbol]],
    by_id: dict[str, Symbol],
) -> tuple[str | None, str]:
    if scope.kind == "class":
        return None, "class-body lookup needs execution-order analysis"
    parts: list[str] = []
    expression = node.func
    while isinstance(expression, ast.Attribute):
        parts.insert(0, expression.attr)
        expression = expression.value
    if not isinstance(expression, ast.Name):
        return None, "dynamic callable expression"
    binding = scope.lookup(expression.id)
    if binding is None:
        return None, "unbound, shadowed, conditional, or unsupported lexical binding"
    if binding.kind == "symbol":
        if parts:
            return None, "attribute dispatch requires type/runtime analysis"
        return binding.target, "single lexical definition; potential call, not runtime proof"
    qualified = ".".join([binding.target, *parts])
    matches = by_name.get(qualified, [])
    if len(matches) == 1 and matches[0].kind not in ("module", "method"):
        symbol = matches[0]
        # Imported definitions may themselves be rebound or decorated.
        # The collector's owning scope must still bind the exported name to this symbol.
        if symbol.id in by_id:
            return symbol.id, "indexed import target; potential call, not runtime proof"
    return None, "import target absent, ambiguous, rebound, or unsupported"


def build_index(repository: Path, revision: str = "HEAD") -> Index:
    snapshot, documents = read_snapshot(repository, revision)
    collectors: list[Collector] = []
    accepted: list[Document] = []
    issues = list(snapshot.issues)
    for document in documents:
        try:
            tree = ast.parse(document.source, filename=document.path)
            collector = Collector(document)
            collector.visit(tree)
        except (SyntaxError, ValueError, RecursionError) as error:
            issues.append(Issue(path=document.path, reason=f"parse/analysis failure: {error}"))
            continue
        collectors.append(collector)
        accepted.append(document)
    symbols = [symbol for collector in collectors for symbol in collector.symbols]
    by_name: dict[str, list[Symbol]] = {}
    # Only stable, single module-level definitions qualify as imported call targets.
    stable: dict[str, Symbol] = {}
    module_counts: dict[str, int] = {}
    for collector in collectors:
        module = collector.document.module
        module_counts[module] = module_counts.get(module, 0) + 1
    for collector in collectors:
        for symbol in collector.symbols:
            by_name.setdefault(symbol.qualified_name, []).append(symbol)
        if collector.scope.opaque or module_counts[collector.document.module] != 1:
            continue
        for choices in collector.scope.bindings.values():
            if len(choices) == 1 and choices[0] and choices[0].kind == "symbol":
                stable_target = choices[0].target
                stable.update(
                    {
                        symbol.id: symbol
                        for symbol in collector.symbols
                        if symbol.id == stable_target
                    }
                )
    calls: list[Call] = []
    for collector in collectors:
        for node, scope in collector.calls:
            target, reason = resolve_call(node, scope, by_name, stable)
            calls.append(
                Call(
                    path=collector.document.path,
                    line=node.lineno,
                    column=node.col_offset,
                    caller_id=scope.owner.id,
                    expression=ast.unparse(node.func)[:500],
                    target_id=target,
                    resolution="resolved" if target else "unknown",
                    reason=reason,
                )
            )
    updated = snapshot.model_copy(
        update={
            "indexed_files": len(accepted),
            "issues": issues,
            "status": "partial" if issues else "complete",
        }
    )
    return Index(snapshot=updated, documents=accepted, symbols=symbols, calls=calls)
