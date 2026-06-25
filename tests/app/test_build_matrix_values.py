from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from domain.design_tasks import DesignTask
from services.build_orchestration_service import _matrix_values


def _task(**overrides) -> DesignTask:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "id": uuid4(),
        "generation_request_id": uuid4(),
        "research_run_id": uuid4(),
        "task_no": 1,
        "challenge_id": "re-0001",
        "title": "Branch Maze",
        "category": "re",
        "difficulty": "medium",
        "primary_technique": "branch constraint solving",
        "learning_objective": "Recover the accepted input from a compiled checker.",
        "points": 250,
        "port": None,
        "scenario": "Compiled checker with misleading branches.",
        "constraints": {},
        "evidence_summary": "",
        "finding_ids": [],
        "status": "designed",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return DesignTask(**values)


@pytest.mark.parametrize(
    ("language", "compiler", "target_format"),
    [
        ("cpp", "g++", "elf"),
        ("rust", "rustc", "elf"),
        ("go", "go build", "elf"),
        ("java", "javac", "jar"),
        ("kotlin", "kotlinc", "jar"),
    ],
)
def test_re_matrix_defaults_follow_declared_language(
    language: str,
    compiler: str,
    target_format: str,
) -> None:
    values = _matrix_values(_task(), {"language": language})

    assert values["language"] == language
    assert values["compiler"] == compiler
    assert values["target_format"] == target_format


def test_re_matrix_keeps_explicit_compiler_and_target_format() -> None:
    values = _matrix_values(
        _task(),
        {
            "language": "rust",
            "compiler": "cargo build --release",
            "target_format": "wasm",
        },
    )

    assert values["compiler"] == "cargo build --release"
    assert values["target_format"] == "wasm"


@pytest.mark.parametrize(
    ("language", "compiler", "target_format"),
    [
        ("cpp", "g++", "elf"),
        ("rust", "rustc", "elf"),
        ("go", "go build", "elf"),
        ("asm", "nasm + ld", "elf"),
    ],
)
def test_pwn_matrix_defaults_follow_declared_language(
    language: str,
    compiler: str,
    target_format: str,
) -> None:
    values = _matrix_values(
        _task(
            category="pwn",
            challenge_id="pwn-0001",
            title="Heap Ledger",
            port=9001,
            primary_technique="heap overflow",
            learning_objective="Exploit a memory corruption bug in a service.",
        ),
        {"language": language},
    )

    assert values["language"] == language
    assert values["compiler"] == compiler
    assert values["target_format"] == target_format


def test_pwn_matrix_keeps_explicit_compiler() -> None:
    values = _matrix_values(
        _task(
            category="pwn",
            challenge_id="pwn-0001",
            title="Heap Ledger",
            port=9001,
            primary_technique="heap overflow",
            learning_objective="Exploit a memory corruption bug in a service.",
        ),
        {
            "language": "rust",
            "compiler": "cargo build --release",
        },
    )

    assert values["compiler"] == "cargo build --release"
    assert values["target_format"] == "elf"
