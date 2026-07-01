"""Build-profile startup readiness contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.paths import ProjectPaths
from services.build_profile_readiness import (
    check_build_profile_readiness,
    unavailable_build_profiles,
)


def test_readiness_reports_missing_profiles_and_commands() -> None:
    readiness = check_build_profile_readiness(
        lambda name: name == "cf-web",
        terminal_backend="docker",
    )

    assert readiness["ready"] is False
    assert readiness["missing_profiles"] == ["cf-pwn", "cf-re"]
    assert readiness["categories"]["web"]["ready"] is True
    assert readiness["categories"]["pwn"]["create_command"] == (
        "hermes profile create cf-pwn"
    )


def test_unavailable_profiles_are_limited_to_requested_categories() -> None:
    readiness = check_build_profile_readiness(
        lambda _name: False,
        terminal_backend="docker",
    )

    unavailable = unavailable_build_profiles(readiness, ["pwn", "pwn"])

    assert unavailable == [
        {
            "category": "pwn",
            "profile": "cf-pwn",
            "create_command": "hermes profile create cf-pwn",
            "message": "",
        }
    ]


def test_pwn_readiness_requires_isolated_backend_even_when_profile_exists() -> None:
    readiness = check_build_profile_readiness(
        lambda _name: True,
        terminal_backend="local",
    )

    assert readiness["ready"] is False
    assert readiness["missing_profiles"] == ["cf-pwn"]
    assert readiness["categories"]["pwn"]["ready"] is False
    assert "isolated Docker/VM" in readiness["categories"]["pwn"]["message"]


def test_pwn_readiness_allows_docker_backend() -> None:
    readiness = check_build_profile_readiness(
        lambda _name: True,
        terminal_backend="docker",
    )

    assert readiness["ready"] is True
    assert readiness["missing_profiles"] == []


def test_pwn_readiness_uses_profile_backend_before_project_backend() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        paths = ProjectPaths(root=root, repository=root)
        (paths.hermes_home / "profiles" / "cf-pwn").mkdir(parents=True)
        paths.hermes_home.mkdir(exist_ok=True)
        (paths.hermes_home / "config.yaml").write_text(
            "terminal:\n  backend: local\n",
            encoding="utf-8",
        )
        (paths.hermes_home / "profiles" / "cf-pwn" / "config.yaml").write_text(
            "terminal:\n  backend: docker\n",
            encoding="utf-8",
        )

        readiness = check_build_profile_readiness(
            lambda _name: True,
            paths=paths,
        )

    assert readiness["ready"] is True
    assert readiness["categories"]["pwn"]["ready"] is True
