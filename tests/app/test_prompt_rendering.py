"""Tests for the rendered shard prompt."""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from domain.resume import ChallengeResumePlan, ShardResumePlan
from hermes.prompt import render_prompt

PROGRESS_SHARD_RE = re.compile(r'progress --shard "?([^"\s]+)"? --worker')


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def prompt_template(self) -> Path:
        return self.root / "prompts" / "shard_prompt.md"

    @property
    def challenges(self) -> Path:
        return self.root / "work" / "challenges"

    @property
    def generation_profile(self) -> Path:
        return self.root / "generation-profiles.json"

    @property
    def design_skill(self) -> Path:
        return self.root / "skills" / "design-challenges" / "SKILL.md"

    @property
    def design_references(self) -> Path:
        return self.root / "skills" / "design-challenges" / "references"


def _copy_real_prompt_into(target_root: Path) -> Path:
    """Copy the in-tree prompt template into the temp project."""
    real_prompt = Path(__file__).resolve().parents[2] / "prompts" / "shard_prompt.md"
    target_prompt = target_root / "prompts" / "shard_prompt.md"
    target_prompt.parent.mkdir(parents=True, exist_ok=True)
    target_prompt.write_text(real_prompt.read_text(encoding="utf-8"), encoding="utf-8")
    return target_prompt


def _seed_paths(tmp: Path) -> _Paths:
    paths = _Paths(root=tmp)
    _copy_real_prompt_into(tmp)
    (paths.challenges).mkdir(parents=True, exist_ok=True)
    paths.generation_profile.write_text("{}", encoding="utf-8")
    paths.design_skill.parent.mkdir(parents=True, exist_ok=True)
    paths.design_skill.write_text("# skill\n", encoding="utf-8")
    paths.design_references.mkdir(parents=True, exist_ok=True)
    return paths


class RenderPromptTests(unittest.TestCase):
    def test_progress_command_uses_original_shard_name(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "work" / "shards" / "running" / "web-0001-0005.worker-02.json"
            running_shard.parent.mkdir(parents=True, exist_ok=True)
            running_shard.write_text("{}", encoding="utf-8")
            report = tmp_path / "report.json"

            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                report,
                worker="worker-02",
                original_shard_name="web-0001-0005.json",
            )

            matches = PROGRESS_SHARD_RE.findall(rendered)
            self.assertTrue(matches, "no progress command shard arg found")
            self.assertIn("--best-effort", rendered)
            for shard_name in matches:
                self.assertNotIn(".worker-", shard_name)
                self.assertRegex(shard_name, r"^[a-z0-9_-]+\.json$")
                self.assertEqual(shard_name, "web-0001-0005.json")

    def test_resume_plan_section_appears(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            report = tmp_path / "r.json"

            plan = ShardResumePlan(
                shard="web-0001-0005.json",
                previous_claim_event_id=42,
                challenges=(
                    ChallengeResumePlan(
                        challenge_id="web-0001",
                        directory=Path("/tmp/web-0001"),
                        lookup_status="ok",
                        skipped_stages=("design", "implement"),
                        first_pending_stage="build",
                        stage_sources={"design": 40, "implement": 41},
                    ),
                ),
            )
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                report,
                worker="dry-01",
                original_shard_name="web-0001-0005.json",
                resume_plan=plan,
                resume_output_targets={"web-0001": "output/challenges/web/web-0001-old-slug"},
            )

            self.assertIn("0. Resume Check", rendered)
            self.assertIn("web-0001", rendered)
            self.assertIn("skip_stages=design, implement", rendered)
            self.assertIn("next_stage=build", rendered)
            self.assertIn("edit_exact_path=output/challenges/web/web-0001-old-slug", rendered)
            self.assertIn("do not create or rename another directory", rendered)

    def test_repair_section_appears_when_enabled(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "repair-report.json",
                worker="dry-01",
                original_shard_name="web-0001.json",
                repair_requested=True,
                repair_context={"failure_summary": "metadata.json missing field"},
            )

            self.assertIn("Repair mode is enabled.", rendered)
            self.assertIn("Do not use carry-forward instructions", rendered)
            self.assertIn("metadata.json missing field", rendered)

    def test_retry_section_appears_when_context_is_present(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "retry-report.json",
                worker="dry-01",
                original_shard_name="web-0001.json",
                retry_context={
                    "failure_summary": "build: docker build failed",
                    "first_failure": {
                        "failure_kind": "missing_dependency",
                        "failure_hint": "Install make in the Dockerfile.",
                    },
                },
            )

            self.assertIn("Retry carry-forward is enabled.", rendered)
            self.assertIn("non-regression constraints", rendered)
            self.assertIn("Install make in the Dockerfile.", rendered)

    def test_first_run_resume_plan_renders_fallback(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            report = tmp_path / "r.json"

            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                report,
                worker="dry-01",
                original_shard_name="web-0001-0005.json",
                resume_plan=None,
            )
            self.assertIn("first-time run", rendered)

    def test_reverse_prompt_forbids_plaintext_flag_shortcuts(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")

            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="re-0001-0001.json",
            )

            self.assertIn("Local build source under `src/` may contain", rendered)
            self.assertIn("windows/amd64", rendered)
            self.assertIn("MinGW-w64", rendered)
            self.assertIn("OLLVM", rendered)
            self.assertIn("Running the delivered artifact with no exploit/license/input", rendered)
            self.assertIn("the intended path must be necessary", rendered)

    def test_prompt_forbids_in_script_image_build(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="s.json",
            )
            self.assertIn(
                'docker image inspect "$IMAGE" >/dev/null 2>&1 || {',
                rendered,
            )
            self.assertIn(
                "validate.sh: required image '$IMAGE' is missing",
                rendered,
            )
            self.assertIn(
                "MUST NOT contain `docker build`",
                rendered,
            )
            self.assertNotIn(
                '|| docker build -t "$IMAGE" .',
                rendered,
            )

    def test_prompt_keeps_apache_root_master_exception(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="s.json",
            )
            self.assertIn("Apache/nginx", rendered)
            # Old hard prohibitions must be gone.
            self.assertNotIn("Never leave the service running as root", rendered)
            self.assertNotIn("Do not use root execution", rendered)

    def test_prompt_requires_host_owned_validation(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            running_shard = tmp_path / "running.json"
            running_shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                running_shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="s.json",
            )
            self.assertIn("Do not run `validate.sh`", rendered)
            self.assertIn("host runner", rendered)
            self.assertIn("- FLAG=flag{xxxx}", rendered)
            # Progress reporting must list four stages only.
            self.assertIn(
                "--stage <design|implement|build|document>",
                rendered,
            )

    def test_design_context_instruction_is_conditional(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            plain_shard = tmp_path / "plain.json"
            plain_shard.write_text(
                '{"challenges": [{"id": "web-0001", "category": "web"}]}',
                encoding="utf-8",
            )
            designed_shard = tmp_path / "designed.json"
            designed_shard.write_text(
                ('{"challenges": [{"id": "web-0001", "category": "web", "design": {"flag_location": "env"}}]}'),
                encoding="utf-8",
            )

            plain = render_prompt(
                paths,  # type: ignore[arg-type]
                plain_shard,
                tmp_path / "plain-report.json",
                worker="dry-01",
                original_shard_name="plain.json",
            )
            designed = render_prompt(
                paths,  # type: ignore[arg-type]
                designed_shard,
                tmp_path / "designed-report.json",
                worker="dry-01",
                original_shard_name="designed.json",
            )

            self.assertNotIn("authoritative for deployment", plain)
            self.assertIn("authoritative for deployment", designed)

    def test_build_contract_section_locks_governed_fields(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            shard = tmp_path / "re.json"
            shard.write_text(
                (
                    '{"challenges": [{"id": "re-0001", "category": "re",'
                    ' "difficulty": "hard", "language": "rust",'
                    ' "target_format": "wasm", "architecture": "x86_64",'
                    ' "primary_technique": "vm-devirtualization",'
                    ' "runtime": "unspecified"}]}'
                ),
                encoding="utf-8",
            )
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="re.json",
            )

            self.assertIn("# Authoritative Build Contract", rendered)
            self.assertIn("`re-0001`:", rendered)
            self.assertIn("language=rust", rendered)
            self.assertIn("target_format=wasm", rendered)
            self.assertIn("primary_technique=vm-devirtualization", rendered)
            # Matrix placeholder values are not surfaced as governed.
            self.assertNotIn("runtime=unspecified", rendered)
            # Fail-rather-than-substitute instruction is present.
            self.assertIn("report its `build_status` as `failed`", rendered)
            self.assertIn("Do not build a generic", rendered)

    def test_build_contract_section_absent_without_challenges(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _seed_paths(tmp_path)
            shard = tmp_path / "empty.json"
            shard.write_text("{}", encoding="utf-8")
            rendered = render_prompt(
                paths,  # type: ignore[arg-type]
                shard,
                tmp_path / "r.json",
                worker="dry-01",
                original_shard_name="empty.json",
            )
            self.assertNotIn("# Authoritative Build Contract", rendered)


if __name__ == "__main__":
    unittest.main()
