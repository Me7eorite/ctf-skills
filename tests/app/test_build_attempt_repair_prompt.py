from pathlib import Path

from services.build_attempt_repair_service import _repair_prompt


def test_build_attempt_repair_prompt_anchors_terminal_to_allowed_root() -> None:
    challenge_dir = Path(
        "/workspace/executions/attempt/current/output/challenges/pwn/pwn-0001-demo"
    )

    prompt = _repair_prompt(
        {
            "id": "attempt",
            "design_task_id": "task",
            "challenge_id": "pwn-0001",
            "category": "pwn",
            "challenge_dir": challenge_dir,
            "failure_summary": "validate failed",
            "failure_details": [],
            "file_context": "",
        }
    )

    assert f"CHAL_ROOT={str(challenge_dir)!r}".replace("'", '"') in prompt
    assert 'cd "$CHAL_ROOT" || exit 1' in prompt
    assert "Do not call `./bin/progress`" in prompt
    assert "do not use relative guesses" in prompt
    assert "Never prepend `output/challenges/...`" in prompt
    assert "The same rule applies to file tools" in prompt
    assert "use `deploy/Dockerfile`, not" in prompt
    assert "If `pwd` prints `/`, immediately `cd \"$CHAL_ROOT\"`" in prompt
    assert "may contain the required literal `FLAG=<metadata.flag>`" in prompt
    assert "under `environment:` (singular)" in prompt
    assert "pwn-{workspace_id[:6]}-{challenge_slug}:latest" in prompt
    assert "do not invent or restore generic image names" in prompt
    assert "pwn-canary:latest" in prompt
    assert "prefer the workspace-scoped pattern" in prompt
    assert "ctf-factory.*" in prompt
    assert "Do not run broad `docker image prune`" in prompt
    assert "apt mirror" in prompt
    assert "Do not replace it with one hardcoded mirror" in prompt
    assert "Do not run any terminal command that contains `cd ./output/challenges/...`" in prompt
    normalized = " ".join(prompt.split())
    assert "Do not replace it with `${FLAG}`" in normalized
    assert "forbidden in player-facing `attachments/`" in normalized
