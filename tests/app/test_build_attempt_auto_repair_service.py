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
            "docker_image": "pwn-demo:latest",
            "port": 31337,
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


def test_auto_repair_replaces_multiline_conflicting_chroot_copy(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:22.04\n"
        "RUN mkdir -p /home/ctf/bin \\\n"
        "    && mkdir -p /home/ctf/dev\n"
        "RUN cp -R /lib* /home/ctf/ \\\n"
        "    && cp -R /usr/lib* /home/ctf/\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "cp -R /lib* /home/ctf/" not in repaired
    assert "cp -R /usr/lib* /home/ctf/" not in repaired
    assert "cp -a /lib/x86_64-linux-gnu/*.so*" in repaired


def test_auto_repair_normalizes_pwn_xinetd_deploy_from_scaffold(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    (challenge_dir / "deploy" / "src" / "vuln.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    (challenge_dir / "deploy" / "src" / "Makefile").write_text(
        "TARGET = vuln\nall:\n\tgcc vuln.c -o $(TARGET)\nclean:\n\trm -f $(TARGET)\n",
        encoding="utf-8",
    )
    _write_metadata(challenge_dir)
    (challenge_dir / "deploy" / "Dockerfile").write_text(
        "FROM ubuntu:22.04\n"
        "RUN apt-get update && apt-get install -y gcc xinetd\n"
        "COPY src/ /tmp/src/\n"
        "RUN cp -R /lib* /home/ctf/ \\\n"
        "    && cp -R /usr/lib* /home/ctf/\n",
        encoding="utf-8",
    )
    (challenge_dir / "deploy" / "_files" / "ctf.xinetd").write_text(
        "service ctf\n{\n server = /usr/sbin/chroot\n server_args = /home/ctf ./pwn\n}\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    dockerfile = (challenge_dir / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    compose = (challenge_dir / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    xinetd = (challenge_dir / "deploy" / "_files" / "ctf.xinetd").read_text(encoding="utf-8")
    assert result.changed is True
    assert "FROM ubuntu:20.04" in dockerfile
    assert "COPY deploy/src/ /tmp/src/" in dockerfile
    assert "cp vuln /home/ctf/vuln" in dockerfile
    assert "cp -R /lib* /home/ctf/" not in dockerfile
    assert "image: pwn-demo:latest" in compose
    assert '- "31337:31337"' in compose
    assert "- FLAG=flag{demo}" in compose
    assert "server_args = --userspec=1000:1000 /home/ctf ./vuln" in xinetd
