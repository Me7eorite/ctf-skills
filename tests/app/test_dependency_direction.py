import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
INTERNAL_ROOTS = {
    "cli", "core", "domain", "packing", "hermes", "persistence", "services", "web",
}
ALLOWED_IMPORTS = {
    "cli": {"services", "web", "hermes", "packing", "persistence", "domain", "core"},
    "web": {"services", "persistence", "domain", "core"},
    "services": {"persistence", "hermes", "domain", "core"},
    "hermes": {"domain", "core"},
    "packing": {"core"},
    "persistence": {"domain", "core"},
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


def find_violations(owner: str, tree: ast.AST, file_label: str) -> list[str]:
    """Collect dependency-direction violations from an AST.

    Owner must be an INTERNAL_ROOTS entry; returns one diagnostic per
    forbidden import statement.
    """
    violations: list[str] = []
    for node in ast.walk(tree):
        for target, line, statement in imported_roots(node):
            if target not in INTERNAL_ROOTS or target == owner:
                continue
            if target not in ALLOWED_IMPORTS[owner]:
                violations.append(
                    f"{file_label}:{line}: {statement} violates {owner} -> {target}"
                )
    return violations


class DependencyDirectionTests(unittest.TestCase):
    def test_src_packages_follow_dependency_direction(self):
        violations = []
        for path in sorted(SRC.rglob("*.py")):
            owner = owning_package(path)
            if owner not in INTERNAL_ROOTS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(
                find_violations(owner, tree, str(path.relative_to(ROOT)))
            )

        self.assertEqual([], violations)

    def test_rejects_hermes_importing_persistence(self):
        tree = ast.parse("from persistence import transaction")
        violations = find_violations("hermes", tree, "src/hermes/runner.py")
        self.assertEqual(
            ["src/hermes/runner.py:1: from persistence import ... "
             "violates hermes -> persistence"],
            violations,
        )

    def test_rejects_persistence_importing_web(self):
        tree = ast.parse("from web.server import serve")
        violations = find_violations("persistence", tree, "src/persistence/session.py")
        self.assertEqual(
            ["src/persistence/session.py:1: from web.server import ... "
             "violates persistence -> web"],
            violations,
        )

    def test_rejects_persistence_importing_hermes(self):
        tree = ast.parse("import hermes.runner")
        violations = find_violations(
            "persistence", tree, "src/persistence/engine.py"
        )
        self.assertEqual(
            ["src/persistence/engine.py:1: import hermes.runner "
             "violates persistence -> hermes"],
            violations,
        )

    def test_rejects_hermes_importing_services(self):
        # 中文注释：hermes 必须独立于 services；executor 反向 import hermes 才符合方向。
        tree = ast.parse("from services import ResearchWorker")
        violations = find_violations("hermes", tree, "src/hermes/runner.py")
        self.assertEqual(
            ["src/hermes/runner.py:1: from services import ... "
             "violates hermes -> services"],
            violations,
        )

    def test_rejects_services_importing_web(self):
        # 中文注释：services 不能依赖 web，否则把队列层和 HTTP 层耦合在一起。
        tree = ast.parse("from web.server import serve")
        violations = find_violations(
            "services", tree, "src/services/research_worker.py"
        )
        self.assertEqual(
            ["src/services/research_worker.py:1: from web.server import ... "
             "violates services -> web"],
            violations,
        )

    def test_rejects_domain_importing_services(self):
        # 中文注释：domain 是纯数据 + 校验，不允许反向引用 services 编排层。
        tree = ast.parse("import services.research_job_service")
        violations = find_violations("domain", tree, "src/domain/research.py")
        self.assertEqual(
            ["src/domain/research.py:1: import services.research_job_service "
             "violates domain -> services"],
            violations,
        )

    def test_hermes_does_not_import_progress_repository(self):
        """Spec drop-sqlite-progress-store 9.1: hermes/ must reach the progress
        store only through the injected `ProgressStore` protocol from
        `core.state`, never by importing `persistence.repositories.progress`
        or any other `persistence.*` module directly."""
        violations: list[str] = []
        for path in sorted((SRC / "hermes").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for target, line, statement in all_imported_modules(node):
                    if target == "persistence":
                        violations.append(
                            f"{path.relative_to(ROOT)}:{line}: {statement} "
                            f"breaks hermes -> persistence ban"
                        )
        self.assertEqual([], violations)

    def test_core_state_module_surface(self):
        """Spec drop-sqlite-progress-store 9.2: `core.state` exposes the new
        `ProgressStore` protocol and its in-memory test double, and no longer
        exposes the legacy `StateStore` class. The private `_percent` helper
        stays underscore-prefixed so callers don't depend on the formula
        outside the two store implementations."""
        import core.state as state_module

        for name in (
            "ProgressStore",
            "ProgressEventInput",
            "InMemoryProgressStore",
            "STAGES",
            "STATUSES",
        ):
            self.assertTrue(
                hasattr(state_module, name),
                f"core.state must expose {name}",
            )
        self.assertFalse(
            hasattr(state_module, "StateStore"),
            "core.state must no longer expose the legacy StateStore class",
        )
        # `_percent` is intentionally private but must still exist for the
        # PostgresProgressStore import — guard against accidental rename.
        self.assertTrue(callable(getattr(state_module, "_percent", None)))

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
