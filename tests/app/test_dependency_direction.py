import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
INTERNAL_ROOTS = {"cli", "core", "domain", "packing", "hermes", "web"}
ALLOWED_IMPORTS = {
    "cli": {"web", "hermes", "packing", "domain", "core"},
    "web": {"domain", "core"},
    "hermes": {"domain", "core"},
    "packing": {"core"},
    "domain": {"core"},
    "core": set(),
}


def owning_package(path: Path) -> str:
    relative = path.relative_to(SRC)
    if len(relative.parts) == 1:
        return relative.stem
    return relative.parts[0]


def imported_roots(node: ast.AST) -> list[tuple[str, int, str]]:
    if isinstance(node, ast.Import):
        return [
            (alias.name.split(".", 1)[0], node.lineno, f"import {alias.name}")
            for alias in node.names
        ]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [(node.module.split(".", 1)[0], node.lineno, f"from {node.module} import ...")]
    return []


FILE_FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "domain/resume.py": {"subprocess"},
    "domain/metrics.py": {"subprocess"},
}


def all_imported_modules(node: ast.AST) -> list[tuple[str, int, str]]:
    """Return every imported top-level module name from an AST node.

    Unlike ``imported_roots``, this includes external/stdlib modules so
    file-level forbidden-import bans can match them.
    """
    if isinstance(node, ast.Import):
        return [
            (alias.name.split(".", 1)[0], node.lineno, f"import {alias.name}")
            for alias in node.names
        ]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [
            (node.module.split(".", 1)[0], node.lineno, f"from {node.module} import ...")
        ]
    return []


class DependencyDirectionTests(unittest.TestCase):
    def test_src_packages_follow_dependency_direction(self):
        violations = []
        for path in sorted(SRC.rglob("*.py")):
            owner = owning_package(path)
            if owner not in INTERNAL_ROOTS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for target, line, statement in imported_roots(node):
                    if target not in INTERNAL_ROOTS or target == owner:
                        continue
                    if target not in ALLOWED_IMPORTS[owner]:
                        violations.append(
                            f"{path.relative_to(ROOT)}:{line}: {statement} violates "
                            f"{owner} -> {target}"
                        )

        self.assertEqual([], violations)

    def test_named_files_avoid_forbidden_imports(self):
        """Specific files must not import forbidden modules.

        ``domain/resume.py`` and ``domain/metrics.py`` MUST NOT import
        ``subprocess``: the only allowed Docker subprocess call lives in
        ``core/docker.py`` and is reached through ``image_exists``.
        """
        violations = []
        for relative, forbidden in FILE_FORBIDDEN_IMPORTS.items():
            path = SRC / relative
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for target, line, statement in all_imported_modules(node):
                    if target in forbidden:
                        violations.append(
                            f"{path.relative_to(ROOT)}:{line}: {statement} "
                            f"forbidden in this file"
                        )

        self.assertEqual([], violations)
