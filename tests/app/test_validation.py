import tempfile
import unittest
from pathlib import Path

from paths import ProjectPaths
from validation import ChallengeValidator, elf_machine, is_elf


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
