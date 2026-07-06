from domain.generation_profile import generation_profile


def test_pwn_default_profile_declares_xinetd_chroot_deployment_abi() -> None:
    profile = generation_profile("pwn")

    assert profile.capabilities.requires_container is True
    assert profile.capabilities.requires_network_service is True
    assert profile.capabilities.requires_solver is True
    assert profile.capabilities.requires_player_artifact is True
    assert profile.capabilities.launcher == "xinetd_chroot"


def test_profile_capabilities_do_not_encode_pwn_exploit_mechanism() -> None:
    profile = generation_profile("pwn")
    encoded = " ".join(str(value) for value in profile.capabilities.__dict__.values()).lower()

    for token in ("ret2libc", "srop", "orw", "got", "canary", "ret2win"):
        assert token not in encoded
