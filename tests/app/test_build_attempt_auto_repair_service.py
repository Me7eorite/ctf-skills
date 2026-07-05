from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
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


def test_auto_repair_normalizes_tuna_apt_mirror_fallback_order(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:20.04\n"
        "RUN set -eux; \\\n"
        "    cp /etc/apt/sources.list /etc/apt/sources.list.orig; \\\n"
        "    for mirror in \\\n"
        "        http://mirror.tuna.tsinghua.edu.cn/ \\\n"
        "        http://mirrors.aliyun.com/ubuntu/ \\\n"
        "        http://mirrors.zju.edu.cn/ \\\n"
        "        http://mirrors.ustc.edu.cn/ubuntu/; do \\\n"
        "        sed -E \\\n"
        "            -e \"s#http://archive.ubuntu.com/ubuntu/?#${mirror}#g\" \\\n"
        "            -e \"s#http://security.ubuntu.com/ubuntu/?#${mirror}#g\" \\\n"
        "            -e \"s#http://mirrors.tuna.tsinghua.edu.cn/ubuntu/?#${mirror}#g\" \\\n"
        "            /etc/apt/sources.list.orig > /etc/apt/sources.list; \\\n"
        "        apt-get update && apt-get install -y gcc xinetd && break; \\\n"
        "    done\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "http://mirrors.aliyun.com/ubuntu/" in repaired
    assert "http://mirrors.ustc.edu.cn/ubuntu/" in repaired
    assert "http://mirrors.zju.edu.cn/ubuntu/" in repaired
    assert "http://archive.ubuntu.com/ubuntu/" in repaired
    assert repaired.index("http://mirrors.aliyun.com/ubuntu/") < repaired.index("http://archive.ubuntu.com/ubuntu/")
    assert "http://mirror.tuna.tsinghua.edu.cn/ \\" not in repaired
    assert "http://mirror.tuna.tsinghua.edu.cn/ubuntu/ubuntu" not in repaired


def test_auto_repair_replaces_legacy_single_tuna_apt_mirror(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "_files").mkdir(parents=True)
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:20.04\n"
        "RUN sed -i 's#http://archive.ubuntu.com/ubuntu#http://mirrors.tuna.tsinghua.edu.cn/ubuntu#g' "
        "/etc/apt/sources.list && apt-get update && apt-get install -y gcc xinetd\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "mirrors.tuna.tsinghua.edu.cn" not in repaired
    assert "http://mirrors.aliyun.com/ubuntu/" in repaired


def test_auto_repair_adds_i386_multilib_packages(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "src" / "Makefile").write_text(
        "all:\n\tgcc -m32 -o vuln vuln.c\n",
        encoding="utf-8",
    )
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:20.04\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends "
        "gcc make xinetd && rm -rf /var/lib/apt/lists/*\n"
        "RUN cd /tmp/src && make\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "gcc-multilib" in repaired
    assert "libc6-dev-i386" in repaired
    assert "lib32z1" in repaired


def test_auto_repair_aligns_dockerfile_binary_copy_target(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "deploy" / "src").mkdir(parents=True)
    (challenge_dir / "deploy" / "src" / "Makefile").write_text(
        "TARGET = aslr_chal\nall:\n\tgcc vuln.c -o $(TARGET)\n",
        encoding="utf-8",
    )
    _write_metadata(challenge_dir)
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:20.04\n"
        "RUN cd /tmp/src && make clean && make && cp pwn /home/ctf/pwn && rm -rf /tmp/src\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = dockerfile.read_text(encoding="utf-8")
    assert result.changed is True
    assert "cp aslr_chal /home/ctf/aslr_chal" in repaired
    assert "cp pwn /home/ctf/pwn" not in repaired


def test_auto_repair_rewrites_validate_hardcoded_workspace_path(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_ROOT=\"/workspace/executions/attempt/current/output/challenges/pwn/pwn-0001-demo\"\n"
        "cd \"$CHAL_ROOT\" || exit 1\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "/workspace/executions/" not in repaired
    assert 'CHAL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"' in repaired


def test_auto_repair_removes_unsupported_docker_logs_no_color(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "docker logs --no-color --tail=120 pwn-demo\n"
        "docker-compose logs --no-color --tail=120\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "docker logs --tail=120 pwn-demo" in repaired
    assert "$COMPOSE logs --no-color --tail=120" in repaired
    assert "COMPOSE_PROJECT_NAME" in repaired


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
    start_sh = (challenge_dir / "deploy" / "_files" / "start.sh").read_text(encoding="utf-8")
    xinetd = (challenge_dir / "deploy" / "_files" / "ctf.xinetd").read_text(encoding="utf-8")
    assert result.changed is True
    assert "FROM ubuntu:20.04" in dockerfile
    assert "lib32z1" in dockerfile
    assert "COPY deploy/src/ /tmp/src/" in dockerfile
    assert "cp vuln /home/ctf/vuln" in dockerfile
    assert "cp -R /lib* /home/ctf/" not in dockerfile
    assert "image: pwn-demo:latest" in compose
    assert "container_name: pwn-demo" in compose
    assert "build:" not in compose
    assert '- "31337:31337"' in compose
    assert "- FLAG=flag{demo}" in compose
    assert "DASFLAG" in start_sh
    assert "GZCTF_FLAG" in start_sh
    assert "chmod 711 /home/ctf/vuln" in start_sh
    assert "server_args = --userspec=1000:1000 /home/ctf ./vuln" in xinetd


def test_auto_repair_makes_validate_sh_compose_compatible(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir)
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "docker compose up -d\n"
        "docker compose ps\n"
        "docker compose logs --no-color --tail=120\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "compose() {" not in repaired
    assert 'export COMPOSE_PROJECT_NAME="cf_${PROJECT_HASH}"' in repaired
    assert 'COMPOSE="docker-compose -p $COMPOSE_PROJECT_NAME -f $CHAL_ROOT/deploy/docker-compose.yml"' in repaired
    assert "$COMPOSE up -d" in repaired
    assert "$COMPOSE ps" in repaired
    assert "$COMPOSE logs --no-color --tail=120" in repaired
    assert "docker-compose version" not in repaired
    assert not re.search(r"(?m)^\s*compose\s+up\b", repaired)
    assert "docker compose up -d" not in repaired

    second = auto_repair_challenge(challenge_dir)
    assert second.changed is False


def test_auto_repair_repairs_legacy_recursive_compose_helper(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir)
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "compose() {\n"
        "    if compose version >/dev/null 2>&1; then\n"
        "        compose \"$@\"\n"
        "    elif command -v docker-compose >/dev/null 2>&1; then\n"
        "        docker-compose \"$@\"\n"
        "    else\n"
        "        echo \"validate.sh: neither compose nor docker-compose is available\" >&2\n"
        "        return 127\n"
        "    fi\n"
        "}\n"
        "compose up -d\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "compose() {" not in repaired
    assert "$COMPOSE up -d" in repaired
    assert "compose version" not in repaired
    assert "neither compose nor docker-compose" not in repaired


def test_auto_repair_adds_compose_project_to_legacy_docker_compose_validate(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="web")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "docker-compose -f deploy/docker-compose.yml up -d\n"
        "docker-compose -f deploy/docker-compose.yml ps\n"
        "docker-compose -f deploy/docker-compose.yml logs --tail=120\n"
        "docker-compose -f deploy/docker-compose.yml down --remove-orphans\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert 'COMPOSE_PROJECT_NAME="cf_${PROJECT_HASH}"' in repaired
    assert "$COMPOSE up -d" in repaired
    assert "$COMPOSE ps" in repaired
    assert "$COMPOSE logs --tail=120" in repaired
    assert "$COMPOSE down --remove-orphans" in repaired
    assert "docker-compose -f deploy/docker-compose.yml" not in repaired

    second = auto_repair_challenge(challenge_dir)
    assert second.changed is False


def test_auto_repair_fixes_compose_file_variable_used_as_filename(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -eu\n"
        "CHALLENGE_ROOT=\"$PWD\"\n"
        "COMPOSE_FILE=\"$CHALLENGE_ROOT/deploy/$COMPOSE.yml\"\n"
        "docker-compose -f \"$COMPOSE_FILE\" up -d\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "$COMPOSE.yml" not in repaired
    assert 'COMPOSE_FILE="$CHALLENGE_ROOT/deploy/docker-compose.yml"' in repaired
    assert "$COMPOSE up -d" in repaired


def test_auto_repair_preserves_exp_failure_diagnostics(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir)
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "trap cleanup EXIT ERR\n"
        "EXPLOIT_OUTPUT=$(python3 writenup/exp.py 2>&1)\n"
        "EXPLOIT_EXIT=$?\n"
        "echo \"$EXPLOIT_OUTPUT\"\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "set +e\nEXPLOIT_OUTPUT=$(python3 writenup/exp.py 2>&1)" in repaired
    assert "EXPLOIT_EXIT=$?\nset -e" in repaired
    assert "trap cleanup EXIT ERR" not in repaired
    assert "trap cleanup EXIT" in repaired

    second = auto_repair_challenge(challenge_dir)
    repaired_again = validate.read_text(encoding="utf-8")
    assert second.changed is False
    assert repaired_again.count("set +e") == 1


def test_auto_repair_fixes_pwn_unexported_bash_nc_probe(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_HOST=localhost\n"
        "CHAL_PORT=9004\n"
        "if timeout 3 bash -c ': | nc \"$CHAL_HOST\" \"$CHAL_PORT\"' | grep -q \"Choice:\"; then\n"
        "    echo ready\n"
        "fi\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "pwn_readiness_probe() {" in repaired
    assert "bash -c" not in repaired
    assert 'pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 3' in repaired


def test_auto_repair_fixes_pwn_echo_bash_nc_probe_variant(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_HOST=localhost\n"
        "CHAL_PORT=9004\n"
        "if timeout 5 bash -c 'echo \"\" | nc \"$CHAL_HOST\" \"$CHAL_PORT\"' "
        "2>/dev/null | grep -qE \"(Choice:|Welcome)\"; then\n"
        "    echo ready\n"
        "fi\n"
        "if timeout 3 bash -c 'nc \"$CHAL_HOST\" \"$CHAL_PORT\" < /dev/null' "
        "2>/dev/null | head -c 100 | grep -q .; then\n"
        "    echo data\n"
        "fi\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "bash -c" not in repaired
    assert 'pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 5' in repaired
    assert 'pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 3' in repaired


def test_auto_repair_replaces_pwn_nc_z_readiness_even_with_banner_probe(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_HOST=127.0.0.1\n"
        "CHAL_PORT=31337\n"
        "until timeout 2 nc -z \"$CHAL_HOST\" \"$CHAL_PORT\"; do sleep 1; done\n"
        "BANNER=$(timeout 3 nc \"$CHAL_HOST\" \"$CHAL_PORT\" || true)\n"
        "echo \"$BANNER\" | grep -qE '(Choice:|Welcome)'\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "nc -z" not in repaired
    assert 'pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 2' in repaired
    assert 'BANNER=$(pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 3 || true)' in repaired


def test_auto_repair_replaces_timeout_nc_banner_capture_without_discarding_output(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -eu\n"
        "CHAL_HOST=127.0.0.1\n"
        "CHAL_PORT=31337\n"
        "BANNER=$(timeout 3 nc \"$CHAL_HOST\" \"$CHAL_PORT\" 2>/dev/null) || BANNER=\"\"\n"
        "if [ -n \"$BANNER\" ] && echo \"$BANNER\" | grep -q \"Welcome\"; then\n"
        "    echo ready\n"
        "fi\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "pwn_readiness_probe() {" in repaired
    assert 'BANNER=$(pwn_readiness_probe "$CHAL_HOST" "$CHAL_PORT" 3) || BANNER=""' in repaired
    assert "timeout 3 nc" not in repaired


def test_pwn_readiness_probe_handles_empty_port_without_valueerror(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_HOST=127.0.0.1\n"
        "CHAL_PORT=\n"
        "timeout 2 nc -z \"$CHAL_HOST\" \"$CHAL_PORT\"\n",
        encoding="utf-8",
    )

    auto_repair_challenge(challenge_dir)
    repaired = validate.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^pwn_readiness_probe\(\) \{.*?^\}", repaired)
    assert match is not None
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/bash\n" + match.group(0) + "\npwn_readiness_probe 127.0.0.1 '' 0.1\n",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(probe)], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert "invalid port" in result.stderr
    assert "ValueError" not in result.stderr


def test_pwn_readiness_probe_does_not_wait_for_tcp_eof(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "CHAL_HOST=127.0.0.1\n"
        "CHAL_PORT=31337\n"
        "timeout 3 bash -c 'head -c 200 < /dev/tcp/$CHAL_HOST/$CHAL_PORT | grep -q SecureVault'\n",
        encoding="utf-8",
    )

    auto_repair_challenge(challenge_dir)
    repaired = validate.read_text(encoding="utf-8")
    assert "head -c 200" not in repaired
    assert "pwn_readiness_probe" in repaired
    match = re.search(r"(?ms)^pwn_readiness_probe\(\) \{.*?^\}", repaired)
    assert match is not None

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    done = threading.Event()

    def serve() -> None:
        try:
            conn, _addr = server.accept()
            with conn:
                conn.sendall(b"SecureVault\nChoice: ")
                done.wait(2)
        finally:
            server.close()

    threading.Thread(target=serve, daemon=True).start()
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/bash\n"
        + match.group(0)
        + f"\npwn_readiness_probe 127.0.0.1 {port} 1 SecureVault\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    result = subprocess.run(["bash", str(probe)], text=True, capture_output=True, timeout=3, check=False)
    elapsed = time.monotonic() - started
    done.set()

    assert result.returncode == 0
    assert elapsed < 1.5
    assert "SecureVault" in result.stdout

def test_auto_repair_wraps_timeout_solver_capture_under_set_e(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_metadata(challenge_dir, category="pwn")
    validate = challenge_dir / "validate.sh"
    validate.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "echo '[validate] Running exploit script'\n"
        "EXPLOIT_OUTPUT=$(timeout 60 python3 writenup/exp.py 2>&1)\n"
        "echo done\n",
        encoding="utf-8",
    )

    result = auto_repair_challenge(challenge_dir)

    repaired = validate.read_text(encoding="utf-8")
    assert result.changed is True
    assert "set +e\nEXPLOIT_OUTPUT=$(timeout 60 python3 writenup/exp.py 2>&1)" in repaired
    assert "EXPLOIT_EXIT=$?\nset -e" in repaired
    assert 'echo "$EXPLOIT_OUTPUT"' in repaired
    assert "Exploit exited nonzero" in repaired
