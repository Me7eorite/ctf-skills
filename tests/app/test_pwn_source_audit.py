from __future__ import annotations

import json
import os
from pathlib import Path

from domain.pwn_source_audit import audit_pwn_c_sources
from hermes.validation import pre_build_contract_gate


def test_pwn_source_audit_passes_ret2win_overflow(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "ret2win")
    _write_c(
        challenge,
        """
        #include <stdio.h>
        #include <unistd.h>

        void win(void) {
            FILE *f = fopen("/flag", "r");
            char flag[64];
            fgets(flag, sizeof(flag), f);
            puts(flag);
        }

        void vuln(void) {
            char buf[32];
            read(0, buf, 128);
        }

        int main(void) {
            vuln();
            return 0;
        }
        """,
    )

    assert audit_pwn_c_sources(challenge, _metadata("ret2win")) is None


def test_pre_build_gate_fails_ret2win_with_bounded_read(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "ret2win")
    _write_c(
        challenge,
        """
        #include <stdio.h>

        void win(void) { puts("shell later"); }

        int main(void) {
            char buf[32];
            fgets(buf, sizeof(buf), stdin);
            return 0;
        }
        """,
    )

    detail = pre_build_contract_gate(challenge, "pwn")

    assert detail is not None
    assert detail["phase"] == "source_audit"
    assert detail["code"] == "pwn_source_audit_bounded_read_disqualifies_overflow"
    assert detail["path"] == "deploy/src/chall.c"
    assert detail["line"]
    assert "fgets" in detail["evidence"]


def test_pwn_source_audit_fails_format_string_safe_printf(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "format string")
    _write_c(
        challenge,
        """
        #include <stdio.h>

        int main(void) {
            char name[64];
            fgets(name, sizeof(name), stdin);
            printf("%s", name);
            return 0;
        }
        """,
    )

    finding = audit_pwn_c_sources(challenge, _metadata("format string"))

    assert finding is not None
    assert finding.code == "pwn_source_audit_format_string_not_realized"
    assert finding.path == "deploy/src/chall.c"


def test_pwn_source_audit_passes_format_string_sink(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "format string")
    _write_c(
        challenge,
        """
        #include <stdio.h>

        int main(void) {
            char name[64];
            fgets(name, sizeof(name), stdin);
            printf(name);
            return 0;
        }
        """,
    )

    assert audit_pwn_c_sources(challenge, _metadata("format string")) is None


def test_pwn_source_audit_fails_fixed_secret_escape(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "stack overflow")
    _write_c(
        challenge,
        """
        #include <stdio.h>
        #include <string.h>

        void print_flag(void) {
            FILE *f = fopen("/flag", "r");
            char flag[64];
            fgets(flag, sizeof(flag), f);
            puts(flag);
        }

        int main(void) {
            char password[32];
            fgets(password, sizeof(password), stdin);
            if (strcmp(password, "letmein") == 0) {
                print_flag();
            }
            return 0;
        }
        """,
    )

    finding = audit_pwn_c_sources(challenge, _metadata("stack overflow"))

    assert finding is not None
    assert finding.code == "pwn_source_audit_fixed_secret_escape"
    assert finding.priority == "challenge_escape"
    assert finding.evidence["secret"] == "letmein"


def test_pre_build_gate_runs_source_audit_before_artifact_check(tmp_path: Path) -> None:
    challenge = _make_pwn_challenge(tmp_path, "stack overflow")
    _write_c(
        challenge,
        """
        #include <stdio.h>

        int main(void) {
            char buf[16];
            fgets(buf, sizeof(buf), stdin);
            return 0;
        }
        """,
    )

    detail = pre_build_contract_gate(challenge, "pwn")

    assert detail is not None
    assert detail["phase"] == "source_audit"
    assert detail["code"] == "pwn_source_audit_bounded_read_disqualifies_overflow"


def test_pre_build_gate_reaches_existing_artifact_check_after_source_audit_passes(
    tmp_path: Path,
) -> None:
    challenge = _make_pwn_challenge(tmp_path, "stack overflow")
    _write_c(
        challenge,
        """
        #include <unistd.h>

        int main(void) {
            char buf[16];
            read(0, buf, 128);
            return 0;
        }
        """,
    )

    detail = pre_build_contract_gate(challenge, "pwn")

    assert detail is not None
    assert detail["phase"] == "contract"
    assert detail["code"] == "missing_artifact"


def _make_pwn_challenge(tmp_path: Path, technique: str) -> Path:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "deploy" / "src").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    (challenge / "writenup" / "exp.py").write_text("print('placeholder')\n", encoding="utf-8")
    (challenge / "writenup" / "wp.md").write_text("# wp\n", encoding="utf-8")
    validate = challenge / "validate.sh"
    validate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(validate, 0o755)
    (challenge / "metadata.json").write_text(
        json.dumps(_metadata(technique)),
        encoding="utf-8",
    )
    return challenge


def _write_c(challenge: Path, source: str) -> None:
    (challenge / "deploy" / "src" / "chall.c").write_text(source, encoding="utf-8")


def _metadata(technique: str) -> dict[str, object]:
    return {
        "id": "pwn-0001-demo",
        "title": "Demo",
        "category": "pwn",
        "difficulty": "easy",
        "primary_technique": technique,
        "artifact": "attachments/chall",
    }
