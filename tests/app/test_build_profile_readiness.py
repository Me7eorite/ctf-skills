"""Build-profile startup readiness contracts."""

from services.build_profile_readiness import (
    check_build_profile_readiness,
    unavailable_build_profiles,
)


def test_readiness_reports_missing_profiles_and_commands() -> None:
    readiness = check_build_profile_readiness(lambda name: name == "cf-web")

    assert readiness["ready"] is False
    assert readiness["missing_profiles"] == ["cf-pwn", "cf-re"]
    assert readiness["categories"]["web"]["ready"] is True
    assert readiness["categories"]["pwn"]["create_command"] == (
        "hermes profile create cf-pwn"
    )


def test_unavailable_profiles_are_limited_to_requested_categories() -> None:
    readiness = check_build_profile_readiness(lambda _name: False)

    unavailable = unavailable_build_profiles(readiness, ["pwn", "pwn"])

    assert unavailable == [
        {
            "category": "pwn",
            "profile": "cf-pwn",
            "create_command": "hermes profile create cf-pwn",
        }
    ]
