"""AST visibility checker: every module under the source root sits in exactly one legal state.

Legal states: a facade ``__init__`` (declares ``__all__``), a structural ``__init__`` (empty,
namespace only), a module inside an ``_internal/`` directory, or a leaf whose ``__all__`` names are
all re-exported by ancestor facades. On top of the four-state rule it enforces: one facade home per
exported ``(defining-module, name)`` pair (dual homes require an allowlist entry), zero
underscore-prefixed filenames outside ``_internal/``, and zero underscore import targets
(dunders exempt).
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    'DEFAULT_DUAL_HOME_ALLOWLIST',
    'Violation',
    'check_visibility',
    'main',
]

DEFAULT_DUAL_HOME_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    # R7/decision 3 (dex, 2026-07-08): PollingConfig is deliberately dual-homed (waku.messaging +
    # waku.eventsourcing.projection) via waku._internal.polling to avoid an ES -> messaging edge.
    # Append-only; every addition requires dex sign-off.
    ('waku._internal.polling', 'PollingConfig'),
})

_DEFINED_HERE = ('', '')


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str
    module: str
    name: str = ''

    def render(self) -> str:
        suffix = f' :: {self.name}' if self.name else ''
        return f'{self.kind}: {self.module}{suffix}'


@dataclass(slots=True)
class _Module:
    name: str
    is_init: bool
    is_internal: bool
    filename: str
    all_names: tuple[str, ...] | None
    bindings: dict[str, tuple[str, str]]
    has_body: bool
    import_targets: tuple[str, ...]


def _is_dunder(name: str) -> bool:
    return name.startswith('__') and name.endswith('__')


def _extract_all(node: ast.stmt) -> tuple[str, ...] | None:
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = node.targets
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
    else:
        return None
    for target in targets:
        if isinstance(target, ast.Name) and target.id == '__all__':
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)):
                return tuple(
                    el.value for el in value.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)
                )
            return ()
    return None


def _collect_defined_names(node: ast.stmt, bindings: dict[str, tuple[str, str]]) -> None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        bindings[node.name] = _DEFINED_HERE
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            for name_node in ast.walk(target):
                if isinstance(name_node, ast.Name):
                    bindings[name_node.id] = _DEFINED_HERE
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        bindings[node.target.id] = _DEFINED_HERE


def _collect_bindings(body: list[ast.stmt], bindings: dict[str, tuple[str, str]]) -> None:
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                bindings[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split('.')[0]] = _DEFINED_HERE
        elif isinstance(node, ast.If):
            _collect_bindings(node.body, bindings)
            _collect_bindings(node.orelse, bindings)
        else:
            _collect_defined_names(node, bindings)


def _collect_import_targets(tree: ast.Module) -> tuple[str, ...]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.extend(alias.asname or alias.name.split('.')[0] for alias in node.names)
    return tuple(targets)


def _module_body_is_structural(tree: ast.Module) -> bool:
    return all(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for node in tree.body
    )


def _parse_module(root: Path, path: Path) -> _Module:
    rel = path.relative_to(root)
    parts = (root.name, *rel.parts[:-1])
    is_init = path.name == '__init__.py'
    dotted = '.'.join(parts) if is_init else '.'.join((*parts, path.stem))
    tree = ast.parse(path.read_text(encoding='utf-8'))

    all_names: tuple[str, ...] | None = None
    for node in tree.body:
        extracted = _extract_all(node)
        if extracted is not None:
            all_names = extracted
    bindings: dict[str, tuple[str, str]] = {}
    _collect_bindings(tree.body, bindings)

    return _Module(
        name=dotted,
        is_init=is_init,
        is_internal='_internal' in parts,
        filename=path.name,
        all_names=all_names,
        bindings=bindings,
        has_body=not _module_body_is_structural(tree),
        import_targets=_collect_import_targets(tree),
    )


def _resolve(
    modules: dict[str, _Module],
    module_name: str,
    symbol: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[str, str]:
    key = (module_name, symbol)
    if key in seen:
        return key
    mod = modules.get(module_name)
    if mod is None:
        return key
    binding = mod.bindings.get(symbol)
    if binding is None or binding == _DEFINED_HERE:
        return key
    source_module, source_name = binding
    return _resolve(modules, source_module, source_name, seen | {key})


def _ancestor_facades(modules: dict[str, _Module], module_name: str) -> list[str]:
    ancestors: list[str] = []
    parts = module_name.split('.')
    for depth in range(len(parts) - 1, 0, -1):
        candidate = '.'.join(parts[:depth])
        mod = modules.get(candidate)
        if mod is not None and mod.is_init and mod.all_names is not None:
            ancestors.append(candidate)
    return ancestors


def _underscore_import_violations(mod: _Module) -> list[Violation]:
    return [
        Violation(kind='underscore_import', module=mod.name, name=target)
        for target in mod.import_targets
        if target.startswith('_') and not _is_dunder(target)
    ]


def _state_violations(
    mod: _Module,
    modules: dict[str, _Module],
    facade_exports: dict[str, set[tuple[str, str]]],
) -> list[Violation]:
    if mod.filename.startswith('_') and not _is_dunder(mod.filename.removesuffix('.py')):
        return [Violation(kind='underscore_file', module=mod.name)]
    if mod.is_init:
        if mod.all_names is None and mod.has_body:
            return [Violation(kind='missing_all', module=mod.name)]
        return []
    if mod.all_names is None:
        return [Violation(kind='missing_all', module=mod.name)]
    ancestors = _ancestor_facades(modules, mod.name)
    return [
        Violation(kind='ghost', module=mod.name, name=symbol)
        for symbol in mod.all_names
        if not any(_resolve(modules, mod.name, symbol) in facade_exports[ancestor] for ancestor in ancestors)
    ]


def check_visibility(
    src_root: Path,
    *,
    dual_home_allowlist: frozenset[tuple[str, str]] = DEFAULT_DUAL_HOME_ALLOWLIST,
) -> list[Violation]:
    """Return every visibility violation under ``src_root`` (the top package directory)."""
    modules: dict[str, _Module] = {}
    for path in sorted(src_root.rglob('*.py')):
        mod = _parse_module(src_root, path)
        modules[mod.name] = mod

    facade_exports: dict[str, set[tuple[str, str]]] = {
        mod.name: {_resolve(modules, mod.name, symbol) for symbol in mod.all_names}
        for mod in modules.values()
        if mod.is_init and not mod.is_internal and mod.all_names is not None
    }

    violations: list[Violation] = []
    homes: dict[tuple[str, str], list[str]] = {}
    for facade_name, exports in facade_exports.items():
        for pair in exports:
            homes.setdefault(pair, []).append(facade_name)
    violations.extend(
        Violation(kind='multi_home', module=pair[0], name=pair[1])
        for pair, facades in homes.items()
        if len(facades) > 1 and pair not in dual_home_allowlist
    )

    for mod in modules.values():
        violations.extend(_underscore_import_violations(mod))
        if not mod.is_internal:
            violations.extend(_state_violations(mod, modules, facade_exports))

    return sorted(violations, key=lambda v: (v.kind, v.module, v.name))


def main(argv: list[str]) -> int:
    """CLI entry: print one line per violation, exit 1 when any exist."""
    src_root = Path(argv[0]) if argv else Path('src/waku')
    violations = check_visibility(src_root)
    if violations:
        sys.stdout.write('\n'.join(violation.render() for violation in violations) + '\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
