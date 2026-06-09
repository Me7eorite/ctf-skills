"""Project path configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    """All filesystem locations used by the application."""

    root: Path
    repository: Path

    @classmethod
    def discover(cls) -> "ProjectPaths":
        root = Path(__file__).resolve().parents[1]
        return cls(root=root, repository=root.parent)

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def shards(self) -> Path:
        return self.work / "shards"

    @property
    def challenges(self) -> Path:
        return self.work / "challenges"

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    @property
    def delivery_bundle(self) -> Path:
        return self.work / "资源包"

    @property
    def logs(self) -> Path:
        return self.work / "logs"

    @property
    def state_database(self) -> Path:
        return self.work / "state.sqlite3"

    @property
    def static(self) -> Path:
        return Path(__file__).resolve().parent / "static"

    @property
    def prompt_template(self) -> Path:
        return self.root / "prompts" / "shard_prompt.md"

    @property
    def generation_profile(self) -> Path:
        return self.root / "generation-profiles.json"

    @property
    def design_skill(self) -> Path:
        return self.repository / "skills" / "design-challenges" / "SKILL.md"

    @property
    def design_references(self) -> Path:
        return self.repository / "skills" / "design-challenges" / "references"

    @property
    def hermes_home(self) -> Path:
        return self.root / ".hermes"

    def initialize(self) -> list[Path]:
        directories = [
            *(
                self.shards / state
                for state in ("pending", "running", "done", "failed")
            ),
            *(self.challenges / category for category in ("web", "pwn", "re")),
            self.reports,
            self.logs,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return directories
