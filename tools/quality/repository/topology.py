"""Validate product and repository-tool ownership and dependency direction."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Iterator
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / ".config/checks/architecture/policy.toml"
_REQUIREMENT_NAME_END = re.compile(r"[\s\[(<>=!~;@]")

_POLICY_FIELDS = frozenset(
    {
        "owner",
        "risk_model",
        "measurement",
        "false_positive_cost",
        "remediation",
        "review_condition",
        "source_roots",
        "test_roots",
        "package_root",
        "package_root_modules",
        "root_configuration_modules",
        "package_initializers",
        "product_concepts",
        "external_responsibilities",
        "allowed_package_edges",
        "control_nesting",
    }
)


def _absolute_imports(path: Path) -> Iterator[str]:
    """Yield every absolute module name that one Python file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def _package_edges(package: Path, package_name: str) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    prefix = f"{package_name}."
    for path in sorted(package.rglob("*.py")) if package.is_dir() else ():
        relative = path.relative_to(package)
        if len(relative.parts) < 2:
            continue
        owner = relative.parts[0]
        targets = edges.setdefault(owner, set())
        for module in _absolute_imports(path):
            if module.startswith(prefix):
                target = module.removeprefix(prefix).split(".", 1)[0]
                if target != owner:
                    targets.add(target)
    return edges


def _dependency_cycles(edges: Mapping[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    cycles: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for target in sorted(edges.get(node, ())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in active:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            target = stack.pop()
            active.remove(target)
            component.append(target)
            if target == node:
                break
        if len(component) > 1:
            cycles.append(tuple(sorted(component)))

    for node in sorted(set(edges) | {target for targets in edges.values() for target in targets}):
        if node not in indices:
            visit(node)
    return sorted(cycles)


def _runtime_import_roots(root: Path) -> frozenset[str]:
    """Return the import roots that declared runtime dependencies provide."""
    metadata = root / "pyproject.toml"
    if not metadata.is_file():
        return frozenset[str]()
    project = tomllib.loads(metadata.read_text(encoding="utf-8")).get("project", {})
    names = (
        _REQUIREMENT_NAME_END.split(requirement.strip(), maxsplit=1)[0]
        for requirement in project.get("dependencies", [])
    )
    return frozenset(re.sub(r"[-.]", "_", name).lower() for name in names)


def _undeclared_import_gaps(root: Path, package: Path) -> list[str]:
    """Report product imports outside the standard library, itself and runtime dependencies."""
    admitted = frozenset({package.name, *sys.stdlib_module_names, *_runtime_import_roots(root)})
    return sorted(
        {
            f"architecture_undeclared_import:{top}:{path.relative_to(root).as_posix()}"
            for path in package.rglob("*.py")
            for top in (module.split(".", 1)[0] for module in _absolute_imports(path))
            if top not in admitted
        }
    )


def _source_root_gaps(root: Path, policy: Mapping[str, object]) -> list[str]:
    """Keep the domains of each non-product source root acyclic and independent of tests."""
    raw_sources = policy.get("source_roots")
    raw_tests = policy.get("test_roots")
    sources = (
        {item for item in raw_sources if isinstance(item, str)}
        if isinstance(raw_sources, list)
        else set()
    )
    test_modules = (
        {part for item in raw_tests if isinstance(item, str) for part in Path(item).parts[:1]}
        if isinstance(raw_tests, list)
        else set()
    )
    gaps: set[str] = set()
    for source in sources - {str(policy["package_root"])}:
        tree = root / source
        gaps.update(
            "architecture_cycle:" + ",".join(f"{source}/{domain}" for domain in cycle)
            for cycle in _dependency_cycles(_package_edges(tree, tree.name))
        )
        gaps.update(
            f"architecture_test_import:{top}:{path.relative_to(root).as_posix()}"
            for path in (tree.rglob("*.py") if tree.is_dir() else ())
            for top in (module.split(".", 1)[0] for module in _absolute_imports(path))
            if top in test_modules
        )
    return sorted(gaps)


def _package_declaration_gaps(root: Path, package: Path) -> list[str]:
    gaps: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name == "__init__.py" and ast.get_docstring(tree, clean=False) is None:
            gaps.append(f"architecture_package_declaration_missing:{relative}")
    return gaps


def _policy_gaps(policy: Mapping[str, object]) -> list[str]:
    gaps = [f"architecture_policy_schema:{field}" for field in sorted(set(policy) ^ _POLICY_FIELDS)]
    for field in (
        "owner",
        "risk_model",
        "measurement",
        "false_positive_cost",
        "remediation",
        "review_condition",
        "package_root",
        "package_initializers",
    ):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip():
            gaps.append(f"architecture_policy_value:{field}")
    if policy.get("package_initializers") not in {
        "declarations-only",
        "ordinary-modules",
    }:
        gaps.append("architecture_policy_value:package_initializers")
    return gaps


def architecture_gaps(root: Path = ROOT, policy: Mapping[str, object] | None = None) -> list[str]:
    """Enforce the declared semantic package topology."""
    policy = tomllib.loads(POLICY.read_text(encoding="utf-8")) if policy is None else policy
    policy_gaps = _policy_gaps(policy)
    if policy_gaps:
        return sorted(policy_gaps)
    package_root = str(policy["package_root"])
    package = root / package_root
    package_name = package.name
    package_initializers = str(policy["package_initializers"])
    raw_root_modules = policy.get("root_configuration_modules")
    raw_package_root_modules = policy.get("package_root_modules")
    raw_allowed_edges = policy.get("allowed_package_edges")
    root_modules = (
        frozenset(item for item in raw_root_modules if isinstance(item, str))
        if isinstance(raw_root_modules, list)
        else frozenset()
    )
    package_root_modules = (
        frozenset(item for item in raw_package_root_modules if isinstance(item, str))
        if isinstance(raw_package_root_modules, list)
        else frozenset()
    )
    allowed_edges = (
        {
            str(owner): frozenset(target for target in targets if isinstance(target, str))
            for owner, targets in raw_allowed_edges.items()
            if isinstance(owner, str) and isinstance(targets, list)
        }
        if isinstance(raw_allowed_edges, dict)
        else {}
    )
    raw_concepts = policy.get("product_concepts")
    concepts = (
        {
            str(concept): str(owner)
            for concept, owner in raw_concepts.items()
            if isinstance(concept, str) and isinstance(owner, str)
        }
        if isinstance(raw_concepts, dict)
        else {}
    )
    raw_external = policy.get("external_responsibilities")
    external = (
        {
            str(responsibility): str(owner)
            for responsibility, owner in raw_external.items()
            if isinstance(responsibility, str) and isinstance(owner, str)
        }
        if isinstance(raw_external, dict)
        else {}
    )
    gaps = [
        f"architecture_root_implementation:{path.name}"
        for path in sorted(root.glob("*.py"))
        if path.name not in root_modules and (path.is_file() or path.is_symlink())
    ]
    if not package.is_dir():
        return sorted([*gaps, f"architecture_package_missing:{package_root}"])
    if not concepts:
        gaps.append("architecture_product_concepts_empty")
    concept_owners = list(concepts.values())
    gaps.extend(
        f"architecture_concept_owner_reused:{owner}"
        for owner in sorted({owner for owner in concept_owners if concept_owners.count(owner) > 1})
    )
    for concept, owner in sorted(concepts.items()):
        owner_path = root / owner
        if not owner_path.is_file():
            gaps.append(f"architecture_concept_owner_missing:{concept}")
        elif not owner_path.is_relative_to(package):
            gaps.append(f"architecture_concept_owner_outside_product:{concept}")
    if not external:
        gaps.append("architecture_external_responsibilities_empty")
    actual_packages = {
        child.name
        for child in package.iterdir()
        if child.is_dir() and not child.name.startswith("__")
    }
    actual_root_modules = {path.stem for path in package.glob("*.py") if path.name != "__init__.py"}
    gaps.extend(
        f"architecture_undeclared_root_module:{name}"
        for name in sorted(actual_root_modules - package_root_modules)
    )
    gaps.extend(
        f"architecture_root_module_missing:{name}"
        for name in sorted(package_root_modules - actual_root_modules)
    )
    declared_packages = allowed_edges.keys() - package_root_modules
    gaps.extend(
        f"architecture_undeclared_package:{name}"
        for name in sorted(actual_packages - declared_packages)
    )
    gaps.extend(
        f"architecture_package_missing:{name}"
        for name in sorted(declared_packages - actual_packages)
    )
    if package_initializers == "declarations-only":
        for path in sorted(package.rglob("__init__.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            body = list(tree.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
            if body:
                gaps.append(f"architecture_init_behavior:{path.relative_to(root).as_posix()}")
    edges = _package_edges(package, package_name)
    for owner, targets in sorted(edges.items()):
        gaps.extend(
            f"architecture_disallowed_edge:{owner}->{target}"
            for target in sorted(targets - allowed_edges.get(owner, frozenset()))
        )
    gaps.extend(f"architecture_cycle:{','.join(cycle)}" for cycle in _dependency_cycles(edges))
    gaps.extend(_undeclared_import_gaps(root, package))
    gaps.extend(_source_root_gaps(root, policy))
    gaps.extend(_package_declaration_gaps(root, package))
    return sorted(gaps)
