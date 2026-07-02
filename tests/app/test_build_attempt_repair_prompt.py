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
    assert "If `pwd` prints `/`, immediately `cd \"$CHAL_ROOT\"`" in prompt
    assert "may contain the required literal `FLAG=<metadata.flag>`" in prompt
    assert "under `environment:` (singular)" in prompt
    normalized = " ".join(prompt.split())
    assert "Do not replace it with `${FLAG}`" in normalized
    assert "forbidden in player-facing `attachments/`" in normalized
