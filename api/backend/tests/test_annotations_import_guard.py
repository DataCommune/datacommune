"""Guard FastAPI signatures against function-local annotation imports.

``scripts/serve_golden_preview.py`` used to import ``Annotated`` and ``Header``
inside a dependency factory. Under ``from __future__ import annotations`` the
nested signature annotation stays a string that FastAPI cannot resolve from the
module globals, so the header parameter silently degraded to a query parameter
and every preview request answered 401 (fixed by ``68a39460``). This module
walks the AST of ``app/`` and ``scripts/`` and fails on that shape.

Known limitations, left uncovered on purpose -- the guard targets the shape that
actually broke, not every way an annotation can fail to resolve:

* a function-local bare ``import fastapi`` followed by ``fastapi.Header`` in an
  annotation is NOT reported: only ``from ... import ...`` statements are
  inspected;
* attribute annotations (``x: fastapi.Header``, ``x: mod.Annotated[...]``) are
  NOT reported either, since annotation names are collected from ``ast.Name``
  nodes only;
* on the ``typing`` / ``typing_extensions`` side only ``Annotated`` is guarded;
  any other name imported locally from those modules is allowed.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "scripts")

# Pre-68a39460 shape: both annotation names are imported in the factory body.
_FAULTY_LABEL = "scripts/faulty_preview.py"
_FAULTY_SOURCE = '''"""Reproduction of the pre-68a39460 faulty shape."""

from __future__ import annotations


def _preview_auth_dependency(expected_key: str):
    from typing import Annotated
    from fastapi import Header

    async def preview_auth(
        x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    ) -> None:
        if x_api_key != expected_key:
            raise RuntimeError("unauthorized")

    return preview_auth
'''

# Same shape, with the annotation imports hoisted to the module level.
_FIXED_SOURCE = '''"""Reproduction of the 68a39460 fix."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header


def _preview_auth_dependency(expected_key: str):
    async def preview_auth(
        x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    ) -> None:
        if x_api_key != expected_key:
            raise RuntimeError("unauthorized")

    return preview_auth
'''


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _signature_annotations(function: ast.FunctionDef | ast.AsyncFunctionDef):
    arguments = (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    )
    yield from (argument.annotation for argument in arguments if argument.annotation)
    if function.args.vararg and function.args.vararg.annotation:
        yield function.args.vararg.annotation
    if function.args.kwarg and function.args.kwarg.annotation:
        yield function.args.kwarg.annotation
    if function.returns:
        yield function.returns


def _annotation_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for annotation in _signature_annotations(node):
                names.update(
                    child.id
                    for child in ast.walk(annotation)
                    if isinstance(child, ast.Name)
                )
    return names


def _guarded_import_names(node: ast.ImportFrom) -> set[str]:
    module = node.module or ""
    if module == "fastapi" or module.startswith("fastapi."):
        return {alias.asname or alias.name for alias in node.names}
    if module in {"typing", "typing_extensions"}:
        return {
            alias.asname or alias.name
            for alias in node.names
            if alias.name == "Annotated"
        }
    return set()


class _LocalImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.violations: list[tuple[int, str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.function_stack:
            used_names = _annotation_names(self.function_stack[-1])
            for name in sorted(_guarded_import_names(node) & used_names):
                self.violations.append(
                    (node.lineno, name, self.function_stack[-1].name)
                )


def _collect_violations(source: str, path_label: str) -> list[str]:
    """Report guarded imports made inside a function but used in a signature."""
    tree = ast.parse(source, filename=path_label)
    if not _has_future_annotations(tree):
        return []

    visitor = _LocalImportVisitor()
    visitor.visit(tree)
    return [
        f"{path_label}:{line}: {name!r} is imported inside {function}() "
        "but used in a signature annotation"
        for line, name, function in visitor.violations
    ]


def _find_violations(path: Path) -> list[str]:
    return _collect_violations(
        path.read_text(encoding="utf-8"),
        path.relative_to(_BACKEND_ROOT).as_posix(),
    )


def test_guard_reports_the_pre_fix_faulty_shape() -> None:
    """Control the control: the detector must fire on the shape it exists for."""
    violations = _collect_violations(_FAULTY_SOURCE, _FAULTY_LABEL)

    assert len(violations) == 2, violations
    assert all(item.startswith(f"{_FAULTY_LABEL}:") for item in violations), violations
    assert all("_preview_auth_dependency()" in item for item in violations), violations
    assert "'Annotated' is imported inside" in violations[0], violations
    assert "'Header' is imported inside" in violations[1], violations


def test_guard_accepts_module_level_annotation_imports() -> None:
    """The same shape, fixed, must stay silent -- no false positive."""
    assert _collect_violations(_FIXED_SOURCE, "scripts/fixed_preview.py") == []


def test_fastapi_annotation_imports_are_module_level() -> None:
    paths = sorted(path for root in _SOURCE_ROOTS for path in root.rglob("*.py"))
    future_paths = []
    violations = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _has_future_annotations(tree):
            future_paths.append(path)
            violations.extend(_find_violations(path))

    print(
        f"FastAPI annotation import audit: {len(future_paths)} future-annotations "
        f"modules scanned, {len(violations)} violation(s)"
    )
    assert not violations, "Function-local annotation imports:\n" + "\n".join(
        violations
    )
