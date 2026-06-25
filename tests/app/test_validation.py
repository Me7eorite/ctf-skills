import tempfile
import unittest
from pathlib import Path

from core.paths import ProjectPaths
from domain.validation import (
    ChallengeValidator,
    elf_machine,
    is_elf,
    is_pe,
    pe_machine,
)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.validator = ChallengeValidator(self.paths)

    def test_elf_detection_uses_magic_bytes(self):
        binary = self.paths.root / "sample"
        binary.write_bytes(b"\x7fELFrest")
        self.assertTrue(is_elf(binary))

    def test_elf_machine_reads_architecture(self):
        binary = self.paths.root / "sample"
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0x3E).to_bytes(2, "little")
        binary.write_bytes(header)

        self.assertEqual(elf_machine(binary), "x86_64")

    def test_pe_detection_reads_architecture(self):
        binary = self.paths.root / "sample.exe"
        header = bytearray(b"MZ" + b"\x00" * 0x7E)
        header[0x3C:0x40] = (0x80).to_bytes(4, "little")
        header.extend(b"PE\x00\x00")
        header.extend((0x8664).to_bytes(2, "little"))
        binary.write_bytes(header)

        self.assertTrue(is_pe(binary))
        self.assertEqual(pe_machine(binary), "x86_64")

    def test_web_contract_requires_deploy_files(self):
        challenge = self.paths.challenges / "web" / "web-0001-demo"
        challenge.mkdir(parents=True)
        metadata = {
            "id": "web-0001",
            "title": "Demo",
            "category": "web",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "runtime": "node",
            "framework": "Express",
        }

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertIn("missing deploy/Dockerfile", errors)
        self.assertIn("missing deploy/docker-compose.yml", errors)

    def test_reverse_contract_accepts_real_elf(self):
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "dist").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0x3E).to_bytes(2, "little")
        (challenge / "dist" / "checker").write_bytes(header)
        metadata = {
            "id": "re-0001",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_reverse_contract_accepts_elf_in_attachments(self):
        """Primary delivery directory is attachments/ (dist/ is legacy)."""
        challenge = self.paths.challenges / "re" / "re-0001-attach"
        (challenge / "attachments").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0x3E).to_bytes(2, "little")
        (challenge / "attachments" / "binary").write_bytes(header)
        metadata = {
            "id": "re-0001-attach",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_reverse_contract_accepts_windows_amd64_exe(self):
        challenge = self.paths.challenges / "re" / "re-0001-exe"
        (challenge / "attachments").mkdir(parents=True)
        header = bytearray(b"MZ" + b"\x00" * 0x7E)
        header[0x3C:0x40] = (0x80).to_bytes(4, "little")
        header.extend(b"PE\x00\x00")
        header.extend((0x8664).to_bytes(2, "little"))
        (challenge / "attachments" / "checker.exe").write_bytes(header)
        metadata = {
            "id": "re-0001-exe",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "exe",
            "target_platform": "windows/amd64",
        }

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_pwn_contract_accepts_elf_in_attachments_with_libc(self):
        """Pwn typically ships challenge ELF + libc + ld together under attachments/."""
        challenge = self.paths.challenges / "pwn" / "pwn-attach-001"
        (challenge / "attachments").mkdir(parents=True)
        x86_64 = (0x3E).to_bytes(2, "little")
        for name in ("pwn_task", "libc.so.6", "ld-linux-x86-64.so.2"):
            header = bytearray(b"\x7fELF" + b"\x00" * 16)
            header[18:20] = x86_64
            (challenge / "attachments" / name).write_bytes(header)
        deploy = challenge / "deploy"
        (deploy / "src").mkdir(parents=True)
        (deploy / "src" / "challenge.c").write_text("int main(void) { return 0; }\n")
        (deploy / "Dockerfile").write_text("FROM scratch\n")
        (deploy / "docker-compose.yml").write_text(
            "services:\n  challenge:\n    environment:\n      - FLAG=flag{demo}\n"
        )
        metadata = {
            "id": "pwn-attach-001",
            "title": "Demo",
            "category": "pwn",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_web_contract_requires_literal_compose_flag_matching_metadata(self):
        challenge = self.paths.challenges / "web" / "web-flag-001"
        deploy = challenge / "deploy"
        (deploy / "src").mkdir(parents=True)
        (deploy / "src" / "app.js").write_text("console.log(process.env.FLAG)\n")
        (deploy / "Dockerfile").write_text("FROM scratch\n")
        (deploy / "docker-compose.yml").write_text(
            "services:\n  challenge:\n    environment:\n      - FLAG=${FLAG}\n"
        )
        metadata = {
            "id": "web-flag-001",
            "title": "Demo",
            "category": "web",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "runtime": "node",
            "framework": "Express",
        }

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("FLAG=<metadata.flag>" in error for error in errors))

        (deploy / "docker-compose.yml").write_text(
            "services:\n  challenge:\n    environment:\n      - FLAG=flag{demo}\n"
        )
        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_reverse_contract_missing_elf_reports_attachments(self):
        """Error message should direct authors to the current delivery directory."""
        challenge = self.paths.challenges / "re" / "re-0001-nowhere"
        (challenge / "src").mkdir(parents=True)
        metadata = {
            "id": "re-0001-nowhere",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("attachments" in e for e in errors),
                        f"expected error to mention attachments; got {errors}")
        self.assertFalse(any("dist" in e for e in errors),
                         f"new-authoring error should not mention dist; got {errors}")

    def test_reverse_contract_rejects_wrong_elf_architecture(self):
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "dist").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0xB7).to_bytes(2, "little")
        (challenge / "dist" / "checker").write_bytes(header)
        metadata = {
            "id": "re-0001",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertIn("ELF artifact architecture is not x86_64: dist/checker", errors)

    def _write_re_elf(self, machine: int) -> Path:
        challenge = self.paths.challenges / "re" / "re-0003-demo"
        (challenge / "dist").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = machine.to_bytes(2, "little")
        (challenge / "dist" / "checker").write_bytes(header)
        return challenge

    def _re_metadata(self, target_platform: str) -> dict:
        return {
            "id": "re-0003",
            "title": "Demo",
            "category": "re",
            "difficulty": "medium",
            "build_status": "passed",
            "flag": "flag{demo}",
            "target_format": "elf",
            "target_platform": target_platform,
        }

    def test_reverse_contract_accepts_arm64_for_linux_arm64(self):
        challenge = self._write_re_elf(0xB7)  # aarch64
        self.assertEqual(
            self.validator.contract_errors(
                challenge, self._re_metadata("linux/arm64")
            ),
            [],
        )

    def test_reverse_contract_rejects_amd64_when_arm64_expected(self):
        challenge = self._write_re_elf(0x3E)  # x86_64
        errors = self.validator.contract_errors(
            challenge, self._re_metadata("linux/arm64")
        )
        self.assertIn(
            "ELF artifact architecture is not aarch64: dist/checker", errors
        )

    def test_reverse_contract_rejects_arm64_when_arm_expected(self):
        challenge = self._write_re_elf(0xB7)  # aarch64 but arm (32-bit) wanted
        errors = self.validator.contract_errors(
            challenge, self._re_metadata("linux/arm")
        )
        self.assertIn(
            "ELF artifact architecture is not arm: dist/checker", errors
        )

    def test_reverse_contract_skips_gate_for_unknown_platform(self):
        challenge = self._write_re_elf(0x08)  # mips, not in ARCH_ACCEPTS
        errors = self.validator.contract_errors(
            challenge, self._re_metadata("linux/mips")
        )
        for error in errors:
            self.assertNotIn("ELF artifact architecture", error)

    def test_filter_includes_challenge_directory_without_metadata(self):
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        challenge.mkdir(parents=True)

        summary = self.validator.validate(["re-0001"])

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["results"][0]["status"], "invalid_metadata")


class SolverIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.validator = ChallengeValidator(self.paths)

    def _re_challenge(self, *, validate_sh: str, exp_py: str | None = None) -> tuple:
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "attachments").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0x3E).to_bytes(2, "little")
        (challenge / "attachments" / "checker").write_bytes(header)
        (challenge / "validate.sh").write_text(validate_sh, encoding="utf-8")
        if exp_py is not None:
            (challenge / "writenup").mkdir(parents=True, exist_ok=True)
            (challenge / "writenup" / "exp.py").write_text(exp_py, encoding="utf-8")
        metadata = {
            "id": "re-0001",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{the_secret}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
        }
        return challenge, metadata

    def test_hardcoded_flag_in_validate_sh_is_rejected(self):
        challenge, metadata = self._re_challenge(
            validate_sh="#!/bin/sh\necho flag{the_secret}\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("embeds the literal metadata.flag" in e for e in errors))

    def test_re_solver_not_referencing_artifact_is_rejected(self):
        challenge, metadata = self._re_challenge(
            validate_sh="#!/bin/sh\npython3 solve.py\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(
            any("does not reference the distributed artifact" in e for e in errors)
        )

    def test_re_solver_reading_metadata_is_rejected(self):
        challenge, metadata = self._re_challenge(
            validate_sh="#!/bin/sh\njq -r .flag metadata.json\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("metadata.json" in e for e in errors))

    def test_genuine_re_solver_passes_integrity(self):
        challenge, metadata = self._re_challenge(
            validate_sh="#!/bin/sh\npython3 writenup/exp.py ./attachments/checker\n",
            exp_py="import sys\nbinary=open(sys.argv[1],'rb').read()\nprint(recover(binary))\n",
        )
        errors = self.validator.contract_errors(challenge, metadata)
        # No solver-integrity error (other contract checks already pass for re).
        self.assertEqual(errors, [])

    def _web_challenge(self, *, exp_py: str) -> tuple:
        challenge = self.paths.challenges / "web" / "web-0001-demo"
        deploy = challenge / "deploy"
        (deploy / "src").mkdir(parents=True)
        (deploy / "Dockerfile").write_text("FROM nginx\n", encoding="utf-8")
        (deploy / "docker-compose.yml").write_text(
            "services:\n  app:\n    environment:\n      - FLAG=flag{the_secret}\n",
            encoding="utf-8",
        )
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\ndocker compose up -d\npython3 writenup/exp.py\n",
            encoding="utf-8",
        )
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(exp_py, encoding="utf-8")
        metadata = {
            "id": "web-0001",
            "title": "Demo",
            "category": "web",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{the_secret}",
            "runtime": "node",
            "framework": "Express",
        }
        return challenge, metadata

    def test_web_exp_reading_compose_for_flag_is_rejected(self):
        challenge, metadata = self._web_challenge(
            exp_py="import yaml\nprint(open('deploy/docker-compose.yml').read())\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("docker-compose" in e for e in errors))

    def test_web_exp_hardcoding_flag_is_rejected(self):
        challenge, metadata = self._web_challenge(
            exp_py="print('flag{the_secret}')\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("embeds the literal metadata.flag" in e for e in errors))

    def test_genuine_web_exp_passes_integrity(self):
        challenge, metadata = self._web_challenge(
            exp_py="import os,requests\nr=requests.get(f\"http://{os.environ['CHAL_HOST']}:{os.environ['CHAL_PORT']}/\")\nprint(r.text)\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertEqual(errors, [])

    def test_validate_sh_volume_removal_is_rejected(self):
        challenge, metadata = self._web_challenge(
            exp_py="import os,requests\nprint(requests.get('http://target/').text)\n"
        )
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\ndocker volume rm challenge_postgres_data\n",
            encoding="utf-8",
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("destructive Docker cleanup" in e for e in errors))

    def test_validate_sh_compose_down_volumes_is_rejected(self):
        challenge, metadata = self._web_challenge(
            exp_py="import os,requests\nprint(requests.get('http://target/').text)\n"
        )
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\ndocker compose down --volumes --remove-orphans\n",
            encoding="utf-8",
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("destructive Docker cleanup" in e for e in errors))


class StringsExposureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.validator = ChallengeValidator(self.paths)

    def _re_with_artifact(
        self, *, embed_flag: bool, technique: str
    ) -> tuple:
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "attachments").mkdir(parents=True)
        header = bytearray(b"\x7fELF" + b"\x00" * 16)
        header[18:20] = (0x3E).to_bytes(2, "little")
        body = bytes(header) + b"\x00padding\x00"
        if embed_flag:
            body += b"flag{the_secret}\x00"
        (challenge / "attachments" / "chal").write_bytes(body)
        # genuine solver that touches the artifact
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\npython3 writenup/exp.py ./attachments/chal\n",
            encoding="utf-8",
        )
        (challenge / "writenup").mkdir(parents=True)
        (challenge / "writenup" / "exp.py").write_text(
            "import sys\nopen(sys.argv[1],'rb').read()\nprint(recover())\n",
            encoding="utf-8",
        )
        metadata = {
            "id": "re-0001",
            "title": "Demo",
            "category": "re",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{the_secret}",
            "target_format": "elf",
            "target_platform": "linux/amd64",
            "primary_technique": technique,
            "learning_objective": "recover the flag",
        }
        return challenge, metadata

    def test_plaintext_flag_in_artifact_is_rejected(self):
        challenge, metadata = self._re_with_artifact(
            embed_flag=True, technique="control-flow deobfuscation"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(
            any("exposes the plaintext flag via strings" in e for e in errors)
        )

    def test_plaintext_flag_allowed_when_strings_is_the_technique(self):
        challenge, metadata = self._re_with_artifact(
            embed_flag=True, technique="strings on the binary"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertFalse(
            any("exposes the plaintext flag" in e for e in errors)
        )

    def test_obfuscated_flag_not_in_strings_passes(self):
        challenge, metadata = self._re_with_artifact(
            embed_flag=False, technique="xor decode"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertEqual(errors, [])


class IntendedPathNecessityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.validator = ChallengeValidator(self.paths)

    def _re_challenge(self, *, difficulty="medium"):
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "src").mkdir(parents=True)
        metadata = {
            "id": "re-0001",
            "title": "Demo",
            "category": "re",
            "difficulty": difficulty,
            "build_status": "passed",
            "flag": "flag{the_secret}",
        }
        return challenge, metadata

    def test_plaintext_flag_in_local_source_is_allowed(self):
        challenge, metadata = self._re_challenge()
        (challenge / "src" / "crackme.c").write_text(
            'const char *f = "flag{the_secret}";\n', encoding="utf-8"
        )
        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, "flag{the_secret}"
        )
        self.assertIsNone(reason)

    def test_plaintext_flag_in_delivered_file_is_unnecessary(self):
        challenge, metadata = self._re_challenge()
        (challenge / "attachments").mkdir()
        (challenge / "attachments" / "checker").write_bytes(b"flag{the_secret}")
        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, "flag{the_secret}"
        )
        self.assertIsNotNone(reason)
        self.assertIn("plaintext", reason)

    def test_clean_source_is_not_unnecessary(self):
        challenge, metadata = self._re_challenge()
        (challenge / "src" / "crackme.c").write_text(
            "int main(){return 0;}\n", encoding="utf-8"
        )
        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, "flag{the_secret}"
        )
        self.assertIsNone(reason)

    def test_strings_intended_skips_necessity(self):
        challenge, metadata = self._re_challenge()
        metadata["primary_technique"] = "strings on the binary"
        (challenge / "src" / "crackme.c").write_text(
            'puts("flag{the_secret}");\n', encoding="utf-8"
        )
        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, "flag{the_secret}"
        )
        self.assertIsNone(reason)

    def test_bare_run_reveals_flag_true_and_false(self):
        from domain.validation import _bare_run_reveals_flag

        prints = self.paths.root / "prints"
        prints.write_text("#!/bin/sh\necho flag{the_secret}\n", encoding="utf-8")
        prints.chmod(0o755)
        self.assertTrue(
            _bare_run_reveals_flag(prints, "flag{the_secret}", timeout=10)
        )

        silent = self.paths.root / "silent"
        silent.write_text("#!/bin/sh\necho nothing\n", encoding="utf-8")
        silent.chmod(0o755)
        self.assertFalse(
            _bare_run_reveals_flag(silent, "flag{the_secret}", timeout=10)
        )
