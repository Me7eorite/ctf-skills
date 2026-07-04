"""Tests for ChallengeValidator.validate_challenge and report merging."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.jsonio import write_json
from domain.validation import ChallengeValidator
from hermes.runner import merge_validation_into_report


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def challenges(self) -> Path:
        return self.root / "work" / "challenges"

    @property
    def reports(self) -> Path:
        return self.root / "work" / "reports"


def _seed_paths(tmp: Path) -> _Paths:
    paths = _Paths(root=tmp)
    paths.challenges.mkdir(parents=True, exist_ok=True)
    paths.reports.mkdir(parents=True, exist_ok=True)
    return paths


def _make_challenge_dir(paths: _Paths, challenge_id: str, slug: str = "demo") -> Path:
    directory = paths.challenges / "web" / f"{challenge_id}-{slug}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class ValidateChallengeLookupTests(unittest.TestCase):
    def test_missing_challenge_id_returns_missing_status(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            result = validator.validate_challenge("web-0001")
            self.assertEqual(result["status"], "missing_challenge")
            self.assertIn("error", result)
            self.assertEqual(result["challenge_id"], "web-0001")

    def test_ambiguous_directories_return_ambiguous_status(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            _make_challenge_dir(paths, "web-0001", slug="alpha")
            _make_challenge_dir(paths, "web-0001", slug="beta")
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            result = validator.validate_challenge("web-0001")
            self.assertEqual(result["status"], "ambiguous_challenge")
            self.assertEqual(result["challenge_id"], "web-0001")

    def test_exact_match_validates_through_validate_one(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            directory = _make_challenge_dir(paths, "web-0001")
            # No metadata.json -> contract failure path.
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            result = validator.validate_challenge("web-0001")
            self.assertEqual(result["challenge_id"], "web-0001")
            # validate_one returns invalid_metadata when metadata.json missing.
            self.assertIn(
                result["status"],
                {"invalid_metadata", "contract_failed", "generation_empty_output"},
            )
            self.assertEqual(result.get("path"), str(directory))

    def test_pwn_stale_debug_report_fails_before_validate_script(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            challenge = paths.challenges / "pwn" / "pwn-0001-demo"
            (challenge / "writenup").mkdir(parents=True)
            (challenge / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": "pwn-0001",
                        "category": "pwn",
                        "artifact_sha256": "current-sha",
                    }
                ),
                encoding="utf-8",
            )
            (challenge / "writenup" / "pwn_debug_report.json").write_text(
                json.dumps({"binary": {"sha256": "old-sha"}, "offset": 64}),
                encoding="utf-8",
            )
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            result = validator.validate_challenge("pwn-0001")

            self.assertEqual(result["status"], "solver_evidence_stale")
            self.assertIn("pwn_debug_report.json.binary.sha256", result["error"])
            self.assertEqual(
                result["failure_details"][0]["code"],
                "solver_evidence_stale",
            )

    def test_pwn_hardcoded_win_offset_conflict_fails_before_validate_script(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            challenge = paths.challenges / "pwn" / "pwn-0001-demo"
            (challenge / "attachments").mkdir(parents=True)
            (challenge / "writenup").mkdir()
            (challenge / "attachments" / "vuln").write_bytes(b"\x7fELFfake")
            (challenge / "writenup" / "exp.py").write_text(
                "WIN_OFFSET = 0xdead\n",
                encoding="utf-8",
            )
            (challenge / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": "pwn-0001",
                        "category": "pwn",
                        "artifact": "attachments/vuln",
                        "artifact_sha256": "current-sha",
                    }
                ),
                encoding="utf-8",
            )
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            def fake_run(command, **kwargs):
                if command[:2] == ["readelf", "-sW"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            "Symbol table '.symtab' contains 1 entry:\n"
                            "  12: 00000000000011a9    42 FUNC    GLOBAL DEFAULT   15 win\n"
                        ),
                        stderr="",
                    )
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

            with patch("domain.validation.subprocess.run", side_effect=fake_run):
                result = validator.validate_challenge("pwn-0001")

            self.assertEqual(result["status"], "solver_evidence_stale")
            self.assertIn("WIN_OFFSET=0xdead", result["error"])
            self.assertIn("symbol win=0x11a9", result["error"])

    def test_pwn_exp_recorded_sha_mismatch_fails_before_validate_script(self):
        with TemporaryDirectory() as tmp:
            paths = _seed_paths(Path(tmp))
            challenge = paths.challenges / "pwn" / "pwn-0001-demo"
            (challenge / "writenup").mkdir(parents=True)
            (challenge / "writenup" / "exp.py").write_text(
                'BINARY_SHA256 = "old-sha"\nprint("should not run")\n',
                encoding="utf-8",
            )
            (challenge / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": "pwn-0001",
                        "category": "pwn",
                        "artifact_sha256": "current-sha",
                    }
                ),
                encoding="utf-8",
            )
            validator = ChallengeValidator(paths)  # type: ignore[arg-type]

            result = validator.validate_challenge("pwn-0001")

            self.assertEqual(result["status"], "solver_evidence_stale")
            self.assertIn("exp.py recorded binary sha256", result["error"])
            self.assertEqual(result["failure_details"][0]["path"], "writenup/exp.py")


class ValidateChallengeFlagExtractionTests(unittest.TestCase):
    def _validate_stdout(self, stdout: str, expected: str = "flag{expected-value}"):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        paths = _seed_paths(Path(temp.name))
        directory = _make_challenge_dir(paths, "web-0001")
        write_json(directory / "metadata.json", {"flag": expected})
        (directory / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        validator = ChallengeValidator(paths)  # type: ignore[arg-type]
        validator.contract_errors = lambda *_: []  # type: ignore[method-assign]
        with patch(
            "domain.validation.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout, ""),
        ):
            return validator.validate_one(directory)

    def test_cleanup_after_flag_does_not_mask_success(self):
        result = self._validate_stdout(
            "flag{expected-value}\n[*] Cleaning up...\n"
        )
        self.assertEqual(result["status"], "passed")

    def test_last_independent_flag_token_wins(self):
        result = self._validate_stdout(
            "flag{wrong}\nprefixflag{ignored}\nflag{expected-value}\n"
        )
        self.assertEqual(result["printed_flag"], "flag{expected-value}")

    def test_no_flag_token_is_mismatch(self):
        result = self._validate_stdout("validation completed\n")
        self.assertEqual(result["status"], "flag_mismatch")
        self.assertEqual(result["printed_flag"], "")

    def test_nonzero_exit_preserves_validation_diagnostic_envelope(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        paths = _seed_paths(Path(temp.name))
        directory = _make_challenge_dir(paths, "web-0001")
        write_json(directory / "metadata.json", {"flag": "flag{expected-value}"})
        validation_script = directory / "validate.sh"
        validation_script.write_text("#!/bin/sh\npython3 writenup/exp.py\n", encoding="utf-8")
        validator = ChallengeValidator(paths)  # type: ignore[arg-type]
        validator.contract_errors = lambda *_: []  # type: ignore[method-assign]

        with patch(
            "domain.validation.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["bash", str(validation_script)],
                3,
                "banner\nflag{candidate-value}\n",
                "Traceback: solver failed\n",
            ),
        ):
            result = validator.validate_one(directory)

        self.assertEqual(result["status"], "nonzero_exit")
        self.assertEqual(result["command"], ["bash", str(validation_script)])
        self.assertEqual(result["returncode"], 3)
        self.assertEqual(result["stdout_tail"], "banner\nflag{candidate-value}\n")
        self.assertEqual(result["stderr_tail"], "Traceback: solver failed\n")
        self.assertEqual(result["final_flag_candidate"], "flag{candidate-value}")
        self.assertIn("service state unavailable", result["diagnostic_unavailable"])
        self.assertIn("recent service logs unavailable", result["diagnostic_unavailable"])
        self.assertIn("readiness probe result unavailable", result["diagnostic_unavailable"])
        self.assertNotIn("solver stdout tail unavailable", result["diagnostic_unavailable"])
        self.assertNotIn("solver stderr tail unavailable", result["diagnostic_unavailable"])
        self.assertNotIn("solver exit code unavailable", result["diagnostic_unavailable"])
        self.assertNotIn("final stdout flag candidate unavailable", result["diagnostic_unavailable"])

    def test_validate_path_is_identity_bound_and_does_not_mutate_metadata(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        paths = _seed_paths(Path(temp.name))
        directory = _make_challenge_dir(paths, "web-0001")
        metadata = {"id": "web-0001", "flag": "flag{expected-value}"}
        write_json(directory / "metadata.json", metadata)
        (directory / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        validator = ChallengeValidator(paths)  # type: ignore[arg-type]
        validator.contract_errors = lambda *_: []  # type: ignore[method-assign]

        with patch(
            "domain.validation.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, "flag{expected-value}\n", ""
            ),
        ):
            result = validator.validate_path(
                directory,
                expected_challenge_id="web-0001",
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            json.loads((directory / "metadata.json").read_text()), metadata
        )

        mismatch = validator.validate_path(
            directory,
            expected_challenge_id="web-9999",
        )
        self.assertEqual(mismatch["status"], "identity_mismatch")

    def test_validate_one_prefers_project_venv_python_on_path(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        paths = _seed_paths(Path(temp.name))
        (paths.root / ".venv" / "bin").mkdir(parents=True)
        directory = _make_challenge_dir(paths, "web-0001")
        write_json(
            directory / "metadata.json",
            {"id": "web-0001", "flag": "flag{expected-value}"},
        )
        (directory / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        validator = ChallengeValidator(paths)  # type: ignore[arg-type]
        validator.contract_errors = lambda *_: []  # type: ignore[method-assign]

        with patch(
            "domain.validation.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, "flag{expected-value}\n", ""
            ),
        ) as runner:
            result = validator.validate_one(directory)

        self.assertEqual(result["status"], "passed")
        env = runner.call_args.kwargs["env"]
        self.assertEqual(env["VIRTUAL_ENV"], str(paths.root / ".venv"))
        self.assertEqual(
            env["PATH"].split(os.pathsep)[0],
            str(paths.root / ".venv" / "bin"),
        )


class MergeValidationIntoReportTests(unittest.TestCase):
    def test_creates_report_when_missing(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            merge_validation_into_report(
                report,
                [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "passed",
                        "validation_status": "passed",
                        "validation_elapsed": 1.2,
                    }
                ],
                shard=Path("/x/web-0001-0001.json"),
                worker="dry-01",
                runner_status="passed",
            )
            raw = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(raw["runner_status"], "passed")
            self.assertEqual(len(raw["challenges"]), 1)
            self.assertEqual(raw["challenges"][0]["solve_status"], "passed")
            self.assertEqual(raw["challenges"][0]["validation_status"], "passed")
            self.assertEqual(raw["challenges"][0]["validation_elapsed"], 1.2)

    def test_repairs_malformed_challenges_field(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            report.write_text(
                json.dumps({"shard": "s", "challenges": "not-a-list"}),
                encoding="utf-8",
            )
            merge_validation_into_report(
                report,
                [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "failed",
                        "validation_status": "flag_mismatch",
                    }
                ],
                runner_status="failed",
            )
            raw = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(raw["runner_status"], "failed")
            self.assertIsInstance(raw["challenges"], list)
            self.assertEqual(raw["challenges"][0]["solve_status"], "failed")

    def test_preserves_failure_details_and_normalized_class(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            merge_validation_into_report(
                report,
                [
                    {
                        "challenge_id": "pwn-0001",
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_failure_details": [
                            {
                                "phase": "contract",
                                "code": "pwn_bad_readiness_probe",
                                "message": "bad readiness probe",
                                "path": "validate.sh",
                            }
                        ],
                    }
                ],
                runner_status="failed",
            )

            raw = json.loads(report.read_text(encoding="utf-8"))
            challenge = raw["challenges"][0]
            self.assertEqual(challenge["validation_failure_class"], "service-readiness")
            self.assertIn("pwn_bad_readiness_probe", challenge["validation_failure_signature"])
            self.assertEqual(
                challenge["validation_failure_details"][0]["code"],
                "pwn_bad_readiness_probe",
            )

    def test_preserves_existing_challenge_entries(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "challenges": [
                            {
                                "id": "web-0001",
                                "title": "Existing",
                                "category": "web",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            merge_validation_into_report(
                report,
                [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "passed",
                        "validation_status": "passed",
                    }
                ],
                runner_status="passed",
            )
            raw = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(len(raw["challenges"]), 1)
            entry = raw["challenges"][0]
            self.assertEqual(entry["title"], "Existing")
            self.assertEqual(entry["solve_status"], "passed")

    def test_updates_execution_summary_counts(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "challenges": [
                            {
                                "id": "web-0001",
                                "solve_status": "pending",
                            }
                        ],
                        "execution_summary": {
                            "total_challenges": 1,
                            "passed": 0,
                            "failed": 0,
                            "pending_validation": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            merge_validation_into_report(
                report,
                [
                    {
                        "challenge_id": "web-0001",
                        "solve_status": "passed",
                        "validation_status": "passed",
                    }
                ],
            )

            raw = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(raw["execution_summary"]["passed"], 1)
            self.assertEqual(raw["execution_summary"]["failed"], 0)
            self.assertEqual(raw["execution_summary"]["pending_validation"], 0)

    def test_any_failure_flips_runner_status(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            merge_validation_into_report(
                report,
                [
                    {"challenge_id": "web-0001", "solve_status": "passed"},
                    {"challenge_id": "web-0002", "solve_status": "failed"},
                ],
                runner_status="passed",
            )
            raw = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(raw["runner_status"], "failed")


if __name__ == "__main__":
    unittest.main()
