from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from services.build_attempt_auto_repair_service import auto_repair_challenge


def _write_metadata(challenge_dir: Path, *, category: str = "pwn") -> None:
    write_json(
        challenge_dir / "metadata.json",
        {
            "id": "pwn-0001",
            "title": "Demo",
            "category": category,
            "difficulty": "easy",
            "build_status": "passed",
            "flag": "flag{demo}",
        },
    )


def test_auto_repair_normalizes_dockerfile_copy_paths(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:22.04\n"
        "COPY src/vuln.c src/Makefile ./\n"
        "COPY _files/start.sh /root/start.sh\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "COPY deploy/src/vuln.c deploy/src/Makefile ./" in repaired
    assert "COPY deploy/_files/start.sh /root/start.sh" in repaired


def test_auto_repair_adds_make_and_replaces_conflicting_chroot_copy(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    (challenge_dir / "deploy" / "src" / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:22.04\n"
        "RUN apt-get update && apt-get install -y gcc xinetd && rm -rf /var/lib/apt/lists/*\n"
        "RUN cd /tmp && make clean && make\n"
        "RUN cp -R /lib* /home/ctf/ 2>/dev/null || true && cp -R /usr/lib* /home/ctf/ 2>/dev/null || true\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "apt-get install -y make" in repaired
    assert "cp -R /lib* /home/ctf/" not in repaired
    assert "cp -a /lib/x86_64-linux-gnu/*.so*" in repaired
