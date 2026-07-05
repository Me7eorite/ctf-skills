import tempfile
import unittest
from pathlib import Path

from core.paths import ProjectPaths
from domain.validation import (
    ChallengeValidator,
    classify_validation_failure,
    elf_machine,
    is_elf,
    is_pe,
    pe_machine,
)


def _write_root_start_contract(deploy: Path) -> None:
    (deploy / "_files").mkdir(parents=True, exist_ok=True)
    (deploy / "_files" / "start.sh").write_text("#!/bin/sh\nexec \"$@\"\n")
    (deploy / "Dockerfile").write_text(
        "FROM scratch\nCOPY deploy/_files/start.sh /root/start.sh\n"
    )


def _write_minimal_pwn_contract(challenge: Path) -> dict:
    (challenge / "attachments").mkdir(parents=True, exist_ok=True)
    header = bytearray(b"\x7fELF" + b"\x00" * 16)
    header[18:20] = (0x3E).to_bytes(2, "little")
    (challenge / "attachments" / "pwn_task").write_bytes(header)
    deploy = challenge / "deploy"
    (deploy / "src").mkdir(parents=True, exist_ok=True)
    (deploy / "src" / "challenge.c").write_text("int main(void) { return 0; }\n")
    _write_root_start_contract(deploy)
    (deploy / "docker-compose.yml").write_text(
        "services:\n  challenge:\n    environment:\n      - FLAG=flag{demo}\n"
    )
    return {
        "id": "pwn-attach-001",
        "title": "Demo",
        "category": "pwn",
        "difficulty": "easy",
        "build_status": "passed",
        "flag": "flag{demo}",
        "target_format": "elf",
        "target_platform": "linux/amd64",
    }


def _write_minimal_web_contract(challenge: Path) -> dict:
    deploy = challenge / "deploy"
    (deploy / "src").mkdir(parents=True, exist_ok=True)
    (deploy / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _write_root_start_contract(deploy)
    (deploy / "docker-compose.yml").write_text(
        "services:\n  app:\n    environment:\n      - FLAG=flag{demo}\n",
        encoding="utf-8",
    )
    (challenge / "writenup").mkdir(parents=True, exist_ok=True)
    (challenge / "writenup" / "exp.py").write_text(
        "import os\nprint(os.environ.get('CHAL_HOST', ''))\n",
        encoding="utf-8",
    )
    return {
        "id": "web-compose-project-001",
        "title": "Demo",
        "category": "web",
        "difficulty": "easy",
        "build_status": "passed",
        "flag": "flag{demo}",
        "runtime": "python",
        "framework": "flask",
    }


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
        metadata = _write_minimal_pwn_contract(challenge)
        x86_64 = (0x3E).to_bytes(2, "little")
        for name in ("libc.so.6", "ld-linux-x86-64.so.2"):
            header = bytearray(b"\x7fELF" + b"\x00" * 16)
            header[18:20] = x86_64
            (challenge / "attachments" / name).write_bytes(header)

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_pwn_compose_literal_flag_does_not_make_intended_path_unnecessary(self):
        challenge = self.paths.challenges / "pwn" / "pwn-compose-flag-001"
        metadata = _write_minimal_pwn_contract(challenge)

        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, metadata["flag"]
        )

        self.assertIsNone(reason)

    def test_pwn_plaintext_flag_in_attachment_still_rejects_intended_path(self):
        challenge = self.paths.challenges / "pwn" / "pwn-attachment-flag-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "attachments" / "note.txt").write_text(
            f"debug flag: {metadata['flag']}\n",
            encoding="utf-8",
        )

        reason = self.validator._intended_path_unnecessary(
            challenge, metadata, metadata["flag"]
        )

        self.assertIsNotNone(reason)
        self.assertIn("attachments/note.txt", reason)

    def test_pwn_canary_leak_failure_hint_requires_broad_stable_scan(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="Running exploit phase\ncanary leak failed: could not find canary",
        )

        self.assertEqual(details[0]["code"], "pwn_canary_leak_failed")
        self.assertIn("broad %n$p range", details[0]["hint"])
        self.assertIn("low byte 0x00", details[0]["hint"])
        self.assertIn("stack/libc/PIE", details[0]["hint"])

    def test_timeout_after_exploit_phase_is_not_readiness(self):
        details = classify_validation_failure(
            status="timeout",
            error="validation timed out",
            stderr="readiness passed\nRunning exploit phase\nrecv timed out",
        )

        self.assertEqual(details[0]["phase"], "exploit")
        self.assertEqual(details[0]["code"], "exploit_timeout")

    def test_pwn_exp_reading_compose_for_flag_is_rejected(self):
        challenge = self.paths.challenges / "pwn" / "pwn-exp-compose-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "print(open('deploy/docker-compose.yml').read())\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("docker-compose" in error for error in errors))

    def test_pwn_dockerfile_only_chroot_setup_allowed_in_dockerfile(self):
        challenge = self.paths.challenges / "pwn" / "pwn-dockerfile-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\n"
            "COPY deploy/_files/start.sh /root/start.sh\n"
            "RUN mkdir -p /home/ctf/lib64 /home/ctf/lib/x86_64-linux-gnu\n"
            "RUN cp -L /lib64/ld-linux-x86-64.so.2 /home/ctf/lib64/\n"
            "RUN mkdir -p /home/ctf/dev && mknod /home/ctf/dev/null c 1 3\n"
            "RUN mkdir -p /home/ctf/bin && cp /bin/sh /home/ctf/bin\n",
            encoding="utf-8",
        )

        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_pwn_dockerfile_conflicting_library_copy_is_rejected(self):
        challenge = self.paths.challenges / "pwn" / "pwn-dockerfile-conflict-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\n"
            "COPY deploy/_files/start.sh /root/start.sh\n"
            "RUN cp -R /lib* /home/ctf && cp -R /usr/lib* /home/ctf\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("/lib*" in error and "/usr/lib*" in error for error in errors))

    def test_pwn_dockerfile_make_and_copy_root_context_are_validated(self):
        challenge = self.paths.challenges / "pwn" / "pwn-dockerfile-make-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "src" / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
        (challenge / "deploy" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\n"
            "COPY src/vuln.c src/Makefile ./\n"
            "RUN apt-get update && apt-get install -y gcc xinetd && rm -rf /var/lib/apt/lists/*\n"
            "RUN make clean && make\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertIn(
            "deploy/Dockerfile runs make but does not install the make package",
            errors,
        )
        self.assertTrue(any("challenge root" in error for error in errors))

    def test_pwn_chroot_setup_in_start_script_is_rejected(self):
        challenge = self.paths.challenges / "pwn" / "pwn-start-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "_files" / "start.sh").write_text(
            "#!/bin/sh\ncp -R /lib* /home/ctf\nexec /etc/init.d/xinetd start\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("Dockerfile-only /home/ctf chroot setup" in e for e in errors))

    def test_pwn_chroot_setup_in_validate_script_is_rejected(self):
        challenge = self.paths.challenges / "pwn" / "pwn-validate-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\nmkdir -p /home/ctf/dev && mknod /home/ctf/dev/null c 1 3\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("Dockerfile-only /home/ctf chroot setup" in e for e in errors))

    def test_pwn_chroot_setup_in_metadata_build_command_is_rejected(self):
        challenge = self.paths.challenges / "pwn" / "pwn-build-command-001"
        metadata = _write_minimal_pwn_contract(challenge)
        metadata["build_command"] = "cp -R /usr/lib* /home/ctf && docker build -t pwn deploy"

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("metadata.build_command contains Dockerfile-only" in e for e in errors))

    def test_pwn_chroot_source_must_read_internal_flag_path(self):
        challenge = self.paths.challenges / "pwn" / "pwn-flag-path-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "_files" / "ctf.xinetd").write_text(
            "service ctf\n{\n"
            "  server = /usr/sbin/chroot\n"
            "  server_args = --userspec=1000:1000 /home/ctf ./vuln\n"
            "}\n",
            encoding="utf-8",
        )
        (challenge / "deploy" / "src" / "challenge.c").write_text(
            'int main(void) { fopen("/home/ctf/flag.txt", "r"); }\n',
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("source must open /flag" in e for e in errors))

    def test_pwn_assembly_flag_path_must_be_nul_terminated(self):
        challenge = self.paths.challenges / "pwn" / "pwn-flag-asm-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "_files" / "ctf.xinetd").write_text(
            "service ctf\n{\n"
            "  server = /usr/sbin/chroot\n"
            "  server_args = --userspec=1000:1000 /home/ctf ./vuln\n"
            "}\n",
            encoding="utf-8",
        )
        (challenge / "deploy" / "src" / "win.S").write_text(
            '.section .rodata\nflag_path:\n  .ascii "/flag"\n',
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any(".ascii without a NUL terminator" in e for e in errors))

    def test_pwn_assembly_flag_path_allows_asciz(self):
        challenge = self.paths.challenges / "pwn" / "pwn-flag-asm-ok-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "deploy" / "_files" / "ctf.xinetd").write_text(
            "service ctf\n{\n"
            "  server = /usr/sbin/chroot\n"
            "  server_args = --userspec=1000:1000 /home/ctf ./vuln\n"
            "}\n",
            encoding="utf-8",
        )
        (challenge / "deploy" / "src" / "win.S").write_text(
            '.section .rodata\nflag_path:\n  .asciz "/flag"\n',
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertFalse(any(".ascii without a NUL terminator" in e for e in errors))

    def test_pwn_validate_requires_application_level_readiness(self):
        challenge = self.paths.challenges / "pwn" / "pwn-readiness-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "docker-compose up -d\n"
            "until nc -z 127.0.0.1 9999; do sleep 1; done\n"
            "python3 writenup/exp.py\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("nc -z readiness" in e for e in errors))

    def test_pwn_validate_allows_prompt_level_readiness(self):
        challenge = self.paths.challenges / "pwn" / "pwn-readiness-ok-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "export COMPOSE_PROJECT_NAME=cf_test\n"
            "docker-compose -p \"$COMPOSE_PROJECT_NAME\" up -d\n"
            "python3 - <<'PY'\n"
            "from pwn import remote\n"
            "io = remote('127.0.0.1', 9999)\n"
            "io.recvuntil(b'Choice:')\n"
            "PY\n"
            "python3 writenup/exp.py\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertFalse(any("nc -z readiness" in e for e in errors))
        self.assertFalse(any("without an isolated project" in e for e in errors))

    def test_web_validate_requires_isolated_compose_project(self):
        challenge = self.paths.challenges / "web" / "web-compose-project-001"
        metadata = _write_minimal_web_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "docker-compose -f deploy/docker-compose.yml up -d\n"
            "docker-compose -f deploy/docker-compose.yml ps\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("without an isolated project" in e for e in errors))

    def test_web_validate_allows_compose_project_name(self):
        challenge = self.paths.challenges / "web" / "web-compose-project-ok-001"
        metadata = _write_minimal_web_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "export COMPOSE_PROJECT_NAME=cf_test\n"
            "docker-compose -p \"$COMPOSE_PROJECT_NAME\" -f deploy/docker-compose.yml up -d\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertFalse(any("without an isolated project" in e for e in errors))

    def test_pwn_validate_rejects_unexported_bash_nc_probe(self):
        challenge = self.paths.challenges / "pwn" / "pwn-readiness-scope-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/bash\n"
            "CHAL_HOST=localhost\n"
            "CHAL_PORT=9004\n"
            "export COMPOSE_PROJECT_NAME=cf_test\n"
            "docker-compose -p \"$COMPOSE_PROJECT_NAME\" up -d\n"
            "if timeout 3 bash -c ': | nc \"$CHAL_HOST\" \"$CHAL_PORT\"' | grep -q 'Choice:'; then\n"
            "  echo ready\n"
            "fi\n"
            "python3 writenup/exp.py\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("inner shell sees empty host/port" in e for e in errors))

    def test_pwn_validate_allows_exported_bash_nc_probe(self):
        challenge = self.paths.challenges / "pwn" / "pwn-readiness-export-ok-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "validate.sh").write_text(
            "#!/bin/bash\n"
            "CHAL_HOST=localhost\n"
            "CHAL_PORT=9004\n"
            "export CHAL_HOST CHAL_PORT\n"
            "export COMPOSE_PROJECT_NAME=cf_test\n"
            "docker-compose -p \"$COMPOSE_PROJECT_NAME\" up -d\n"
            "if timeout 3 bash -c ': | nc \"$CHAL_HOST\" \"$CHAL_PORT\"' | grep -q 'Choice:'; then\n"
            "  echo ready\n"
            "fi\n"
            "python3 writenup/exp.py\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertFalse(any("inner shell sees empty host/port" in e for e in errors))

    def test_nonzero_after_running_exploit_is_not_readiness(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "Checking service readiness\n"
                "Service is ready\n"
                "Running exploit...\n"
                "readiness probe failed: exp.py exited 1\n"
            ),
            error="validate.sh exited non-zero",
        )

        self.assertEqual(details[0]["phase"], "exploit")
        self.assertNotEqual(details[0]["code"], "pwn_service_readiness_failed")

    def test_timeout_after_running_exploit_is_exploit_timeout(self):
        details = classify_validation_failure(
            status="timeout",
            stderr="Service is ready\nRunning exploit...\nrecvuntil timed out\n",
            error="validation timed out",
        )

        self.assertEqual(details[0]["phase"], "exploit")
        self.assertEqual(details[0]["code"], "exploit_timeout")

    def test_pwn_exp_rejects_canary_width_threshold(self):
        challenge = self.paths.challenges / "pwn" / "pwn-canary-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "canary = int(leak, 16)\n"
            "if canary < (1 << 48):\n"
            "    raise RuntimeError('Could not find canary')\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("canary leak filtering by 2^48" in e for e in errors))

    def test_pwn_exp_rejects_stack_address_as_canary_candidate(self):
        challenge = self.paths.challenges / "pwn" / "pwn-canary-stack-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "canary = 0x00007fffd331b600\n"
            "if canary & 0xff == 0:\n"
            "    print('accepted canary')\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("implausible canary candidate" in e for e in errors))

    def test_pwn_exp_does_not_treat_offsets_and_masks_as_canary_literals(self):
        challenge = self.paths.challenges / "pwn" / "pwn-canary-offset-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "OFFSET = 0x50\n"
            "MASK = 0xff\n"
            "index = 0x8\n"
            "candidate = int(leak, 16)\n"
            "if candidate & MASK == 0:\n"
            "    print('accepted canary after stable position checks')\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertFalse(any("implausible canary candidate" in e for e in errors))

    def test_pwn_srop_design_rejects_read_flag_shortcut(self):
        challenge = self.paths.challenges / "pwn" / "pwn-srop-001"
        metadata = _write_minimal_pwn_contract(challenge)
        metadata["primary_technique"] = "SROP"
        metadata["techniques"] = ["sigreturn oriented programming"]
        (challenge / "deploy" / "src" / "vuln.c").write_text(
            "void read_flag(){ puts(\"flag\"); }\nint main(){ return 0; }\n",
            encoding="utf-8",
        )
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "payload = b'A' * 72 + p64(elf.symbols['read_flag'])\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("technique consistency failed" in e for e in errors))
        self.assertTrue(any("read_flag()/win()/print_flag()" in e for e in errors))

    def test_pwn_strict_design_rejects_ret2win_flag_naming(self):
        challenge = self.paths.challenges / "pwn" / "pwn-ret2libc-flag-001"
        metadata = _write_minimal_pwn_contract(challenge)
        metadata["primary_technique"] = "stack_canary_leak_via_print"
        metadata["secondary_technique"] = "ret2libc_leak"
        metadata["techniques"] = [
            "stack_canary_leak_via_print",
            "ret2libc_leak",
            "rop_chain_construction",
        ]
        metadata["flag"] = "flag{stack_smashing_detected_bypass_canary_leak_rop_win_2026}"
        (challenge / "writenup").mkdir(parents=True, exist_ok=True)
        (challenge / "writenup" / "exp.py").write_text(
            "libc_base = leak - 0x29dc0\n"
            "payload = flat(pop_rdi, binsh, system)\n",
            encoding="utf-8",
        )

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("flag naming implies ret2win/rop_win" in e for e in errors))

    def test_pwn_artifact_hygiene_rejects_repair_residue(self):
        challenge = self.paths.challenges / "pwn" / "pwn-hygiene-001"
        metadata = _write_minimal_pwn_contract(challenge)
        (challenge / "vuln_new").write_text("stale\n", encoding="utf-8")
        (challenge / "__pycache__").mkdir()
        (challenge / "debug_trace.log").write_text("debug\n", encoding="utf-8")

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("artifact_hygiene failed" in e for e in errors))

    def test_web_contract_requires_literal_compose_flag_matching_metadata(self):
        challenge = self.paths.challenges / "web" / "web-flag-001"
        deploy = challenge / "deploy"
        (deploy / "src").mkdir(parents=True)
        (deploy / "src" / "app.js").write_text("console.log(process.env.FLAG)\n")
        _write_root_start_contract(deploy)
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

        self.assertTrue(any("`- FLAG=<metadata.flag>`" in error for error in errors))
        self.assertTrue(any("`environment:`" in error for error in errors))

        (deploy / "docker-compose.yml").write_text(
            "services:\n  challenge:\n    environment:\n      - FLAG=flag{demo}\n"
        )
        self.assertEqual(self.validator.contract_errors(challenge, metadata), [])

    def test_web_contract_rejects_environments_key_typo(self):
        challenge = self.paths.challenges / "web" / "web-flag-typo-001"
        deploy = challenge / "deploy"
        (deploy / "src").mkdir(parents=True)
        (deploy / "src" / "app.js").write_text("console.log(process.env.FLAG)\n")
        _write_root_start_contract(deploy)
        (deploy / "docker-compose.yml").write_text(
            "services:\n  challenge:\n    environments:\n      - FLAG=flag{demo}\n"
        )
        metadata = {
            "id": "web-flag-typo-001",
            "title": "Demo",
            "category": "web",
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
            "runtime": "node",
            "framework": "Express",
        }

        errors = self.validator.contract_errors(challenge, metadata)

        self.assertTrue(any("`environment:` (singular)" in error for error in errors))

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
        self.assertEqual(summary["results"][0]["status"], "generation_empty_output")


class ValidationFailureClassificationTests(unittest.TestCase):
    def test_classifies_pwntools_prompt_eof(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "Traceback\n"
                "  File \"writenup/exp.py\", line 14, in <module>\n"
                "    io.recvuntil(b'Choice: ')\n"
                "EOFError\n"
            ),
        )

        self.assertEqual(details[0]["code"], "pwn_prompt_eof")
        self.assertIn("application-level readiness", details[0]["hint"])

    def test_classifies_canary_scan_failure(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="RuntimeError: Could not find canary\n",
        )

        self.assertEqual(details[0]["code"], "pwn_canary_leak_failed")
        self.assertIn("stack/libc/PIE", details[0]["hint"])

    def test_classifies_service_readiness_before_canary_name(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "Container canary Started\n"
                "[ERROR] Service failed to start within 30 seconds\n"
                "canary | * Starting internet superserver xinetd\n"
                "canary |   ...done.\n"
            ),
        )

        self.assertEqual(details[0]["code"], "pwn_service_readiness_failed")
        self.assertIn("readiness", details[0]["hint"])

    def test_classifies_service_not_ready_attempt_loop(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "[validate] Waiting for service readiness...\n"
                "[validate] Service not ready after 30 attempts\n"
                " * Starting internet superserver xinetd\n"
                "   ...done.\n"
            ),
        )

        self.assertEqual(details[0]["code"], "pwn_service_readiness_failed")

    def test_classifies_compose_cross_talk(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "docker-compose ps showed wrong other challenge container; "
                "container was recreated from a different challenge"
            ),
        )

        self.assertEqual(details[0]["code"], "compose_cross_talk")
        self.assertIn("COMPOSE_PROJECT_NAME", details[0]["hint"])

    def test_classifies_bad_binary_path_before_generic_nonzero(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="chroot: failed to run command './vuln': No such file or directory\n",
        )

        self.assertEqual(details[0]["code"], "pwn_bad_binary_path")

    def test_classifies_payload_no_flag(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="service ready; Failed to extract flag from exploit output\n",
        )

        self.assertEqual(details[0]["code"], "pwn_payload_no_flag")

    def test_classifies_chroot_flag_path_failure(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "Could not read flag: /home/ctf/flag.txt: "
                "No such file or directory\n"
            ),
        )

        self.assertEqual(details[0]["code"], "pwn_chroot_flag_path")
        self.assertIn("/flag", details[0]["hint"])

    def test_classifies_rop_stack_alignment_failure(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="Program received SIGSEGV at movaps; likely stack alignment issue\n",
        )

        self.assertEqual(details[0]["code"], "pwn_rop_stack_alignment")
        self.assertIn("ret gadget", details[0]["hint"])

    def test_classifies_bad_libc_base_failure(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="invalid libc base: libc base is not page aligned\n",
        )

        self.assertEqual(details[0]["code"], "pwn_bad_libc_base")
        self.assertIn("page-aligned", details[0]["hint"])

    def test_classifies_pie_base_failure(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="PIE base failed: could not compute PIE base from leak\n",
        )

        self.assertEqual(details[0]["code"], "pwn_pie_base_failed")
        self.assertIn("PIE base", details[0]["hint"])

    def test_classifies_missing_rop_gadget(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr="pop rdi gadget not found in the shipped binary\n",
        )

        self.assertEqual(details[0]["code"], "pwn_rop_missing_gadget")
        self.assertIn("gadget", details[0]["hint"])


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
        _write_root_start_contract(deploy)
        (deploy / "docker-compose.yml").write_text(
            "services:\n  app:\n    environment:\n      - FLAG=flag{the_secret}\n",
            encoding="utf-8",
        )
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "export COMPOSE_PROJECT_NAME=cf_test\n"
            "docker compose -p \"$COMPOSE_PROJECT_NAME\" up -d\n"
            "python3 writenup/exp.py\n",
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

    def test_web_exp_importing_pwntools_is_runtime_validated(self):
        challenge, metadata = self._web_challenge(
            exp_py="from pwn import *\nprint('solve')\n"
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertEqual(errors, [])

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

    def test_validate_sh_hardcoded_execution_path_is_rejected(self):
        challenge, metadata = self._web_challenge(
            exp_py="import os,requests\nprint(requests.get('http://target/').text)\n"
        )
        (challenge / "validate.sh").write_text(
            "#!/bin/sh\n"
            "cd /workspace/executions/attempt/current/output/challenges/web/web-0001-demo\n"
            "python3 writenup/exp.py\n",
            encoding="utf-8",
        )
        errors = self.validator.contract_errors(challenge, metadata)
        self.assertTrue(any("hardcodes an execution workspace path" in e for e in errors))

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

    def test_ready_running_cleanup_without_solver_output_is_not_readiness(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "[validate] Service is ready\n"
                "[validate] Running exploit script\n"
                "[validate] cleanup: docker-compose down\n"
                "[readiness] no banner or menu prompt received\n"
            ),
            error="validate.sh exited non-zero",
        )

        self.assertEqual(details[0]["phase"], "exploit")
        self.assertEqual(details[0]["code"], "validate_capture_failed")

    def test_readiness_noise_before_success_does_not_override_ready_state(self):
        details = classify_validation_failure(
            status="nonzero_exit",
            stderr=(
                "[readiness] no banner or menu prompt received\n"
                "[validate] Service is ready\n"
                "[validate] Running exploit script\n"
                "failed to extract flag\n"
            ),
            error="validate.sh exited non-zero",
        )

        self.assertEqual(details[0]["phase"], "exploit")
        self.assertNotEqual(details[0]["code"], "pwn_service_readiness_failed")
