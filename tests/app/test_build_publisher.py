from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import hermes.build_publisher as build_publisher
from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from hermes.build_publisher import (
    prepare_publication_contract,
    publish_workspace_output,
)
from hermes.workspace import (
    WorkspacePromotionError,
    prepare_workspace,
    promote_claimed_outputs,
)


def _paths(root: Path) -> ProjectPaths:
    paths = ProjectPaths(root=root, repository=root)
    paths.initialize()
    paths.generation_profile.write_text("{}", encoding="utf-8")
    paths.design_skill.parent.mkdir(parents=True, exist_ok=True)
    paths.design_skill.write_text("# skill\n", encoding="utf-8")
    paths.design_references.mkdir(parents=True, exist_ok=True)
    for filename in ("design-core.md", "category-tactics.md"):
        (paths.design_references / filename).write_text(
            f"# {filename}\n",
            encoding="utf-8",
        )
    return paths


def _workspace(paths: ProjectPaths, payload: dict):
    shard = paths.shards / "running" / "web.worker.json"
    write_json(shard, payload)
    return prepare_workspace(
        paths,
        shard=shard,
        original_shard_name="web.json",
        worker="worker-1",
    )


def _artifact(root: Path, *, challenge_id: str = "web-0001", slug: str = "demo") -> Path:
    directory = root / "web" / f"{challenge_id}-{slug}"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "metadata.json", {"id": challenge_id, "category": "web"})
    (directory / "artifact.txt").write_text("body\n", encoding="utf-8")
    return directory


def _base_artifact(workspace, *, relpath: str = "base") -> Path:
    directory = workspace.input / "base-artifact" / relpath
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "metadata.json", {"id": "web-0001", "category": "web"})
    (directory / "artifact.txt").write_text("body\n", encoding="utf-8")
    return directory


def _change_policy(workspace, *, preserve=None, forbid=None, relpath: str = "base") -> None:
    write_json(
        workspace.input / "change-policy.json",
        {
            "base_artifact_relpath": relpath,
            "preserve": preserve or [],
            "forbid": forbid or [],
        },
    )


def test_publish_records_manifest_hash_and_high_water() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")

        contract = prepare_publication_contract(paths, workspace, payload)
        result = publish_workspace_output(paths, workspace, contract=contract)

        assert result.outcome == "succeeded"
        assert result.published_paths[0].is_dir()
        assert result.output_manifest_hash
        manifest = read_json(workspace.manifest)
        high_water = read_json(workspace.state / "highest-committed-generation.json")
        assert manifest["publish_generation"] == 1
        assert high_water["publish_generation"] == 1
        assert manifest["output_manifest_hash"] == result.output_manifest_hash
        assert high_water["output_manifest_hash"] == result.output_manifest_hash
        assert not (workspace.state / "publish-journal.json").exists()


def test_publish_noop_when_output_hash_matches_high_water() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")

        contract = prepare_publication_contract(paths, workspace, payload)
        first = publish_workspace_output(paths, workspace, contract=contract)
        second = publish_workspace_output(paths, workspace, contract=contract)

        assert first.outcome == "succeeded"
        assert second.outcome == "noop"
        assert second.published_paths == []
        assert not (workspace.state / "publish-journal.json").exists()
        assert read_json(workspace.state / "highest-committed-generation.json")[
            "publish_generation"
        ] == 1


def test_publish_hash_changes_when_output_bytes_change() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        first = publish_workspace_output(paths, workspace, contract=contract)
        (artifact / "artifact.txt").write_text("changed\n", encoding="utf-8")

        second = publish_workspace_output(paths, workspace, contract=contract)

        assert second.output_manifest_hash != first.output_manifest_hash
        assert second.outcome == "succeeded"


def test_manifest_write_failure_rolls_back_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        existing = _artifact(paths.challenges, slug="old")
        (existing / "artifact.txt").write_text("old\n", encoding="utf-8")
        workspace = _workspace(paths, payload)
        replacement = _artifact(workspace.output / "challenges", slug="new")
        (replacement / "artifact.txt").write_text("new\n", encoding="utf-8")
        contract = prepare_publication_contract(paths, workspace, payload)
        original_writer = build_publisher._write_atomic_json

        def fail_manifest(path: Path, payload, **kwargs):
            if path.name == "manifest.json":
                raise OSError("manifest blocked")
            original_writer(path, payload, **kwargs)

        monkeypatch.setattr(build_publisher, "_write_atomic_json", fail_manifest)

        with pytest.raises(OSError, match="manifest blocked"):
            publish_workspace_output(paths, workspace, contract=contract)

        restored = paths.challenges / "web" / "web-0001-old"
        assert restored.is_dir()
        assert (restored / "artifact.txt").read_text(encoding="utf-8") == "old\n"
        assert not (paths.challenges / "web" / "web-0001-new").exists()


def test_publish_rejects_in_flight_journal_before_commit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        write_json(workspace.state / "publish-journal.json", {"phase": "stage"})

        with pytest.raises(WorkspacePromotionError, match="in-flight publish journal"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_contract_rejects_unenumerated_state_input_hash() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        manifest = read_json(workspace.manifest)
        manifest["input_hashes"]["state/agent-note.json"] = "sha256:bad"
        write_json(workspace.manifest, manifest)

        with pytest.raises(WorkspacePromotionError, match="unexpected state input hash"):
            prepare_publication_contract(paths, workspace, payload)


def test_publish_rejects_invalid_limit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        monkeypatch.setenv("BUILD_PUBLISH_MAX_FILES", "0")

        with pytest.raises(WorkspacePromotionError, match="positive integer"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_publish_file_count_limit_fails_before_canonical_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        monkeypatch.setenv("BUILD_PUBLISH_MAX_FILES", "1")

        with pytest.raises(WorkspacePromotionError, match="file-count limit"):
            publish_workspace_output(paths, workspace, contract=contract)

        assert not list((paths.challenges / "web").glob("web-0001-*"))


def test_publish_component_length_limit_fails_before_canonical_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        (artifact / "long-name.txt").write_text("extra\n", encoding="utf-8")
        contract = prepare_publication_contract(paths, workspace, payload)
        monkeypatch.setenv("BUILD_PUBLISH_MAX_COMPONENT_BYTES", "8")

        with pytest.raises(WorkspacePromotionError, match="path-component length"):
            publish_workspace_output(paths, workspace, contract=contract)

        assert not list((paths.challenges / "web").glob("web-0001-*"))


def test_publish_rejects_missing_metadata() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        (artifact / "metadata.json").unlink()
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="missing metadata"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_publish_rejects_metadata_identity_mismatch() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        write_json(artifact / "metadata.json", {"id": "web-0002", "category": "web"})
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="metadata mismatch"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_publish_rejects_duplicate_claimed_output_directories() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges", slug="a")
        _artifact(workspace.output / "challenges", slug="b")
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="multiple output directories"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_publish_rejects_claimed_id_under_wrong_category_layout() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        wrong = workspace.output / "challenges" / "pwn" / "web-0001-demo"
        wrong.mkdir(parents=True, exist_ok=True)
        write_json(wrong / "metadata.json", {"id": "web-0001", "category": "web"})
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="non-conforming layout"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_change_policy_without_base_artifact_fails_contract() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _change_policy(workspace)

        with pytest.raises(WorkspacePromotionError, match="requires base-artifact"):
            prepare_publication_contract(paths, workspace, payload)


def test_change_policy_rejects_unknown_key() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _base_artifact(workspace)
        write_json(
            workspace.input / "change-policy.json",
            {
                "base_artifact_relpath": "base",
                "preserve": [],
                "forbid": [],
                "extra": True,
            },
        )

        with pytest.raises(WorkspacePromotionError, match="unknown change-policy key"):
            prepare_publication_contract(paths, workspace, payload)


def test_change_policy_rejects_traversal_path() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _base_artifact(workspace)
        _change_policy(workspace, preserve=["../artifact.txt"])

        with pytest.raises(WorkspacePromotionError, match="invalid preserve path"):
            prepare_publication_contract(paths, workspace, payload)


def test_change_policy_preserve_byte_mismatch_rejects_publish() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _base_artifact(workspace)
        _change_policy(workspace, preserve=["artifact.txt"])
        artifact = _artifact(workspace.output / "challenges")
        (artifact / "artifact.txt").write_text("changed\n", encoding="utf-8")
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="preserve mismatch"):
            publish_workspace_output(paths, workspace, contract=contract)

        assert not list((paths.challenges / "web").glob("web-0001-*"))


def test_change_policy_preserve_json_field_mismatch_rejects_publish() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        base = _base_artifact(workspace)
        write_json(base / "metadata.json", {"id": "web-0001", "category": "web", "flag": "old"})
        _change_policy(workspace, preserve=["metadata.json#flag"])
        artifact = _artifact(workspace.output / "challenges")
        write_json(
            artifact / "metadata.json",
            {"id": "web-0001", "category": "web", "flag": "new"},
        )
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="preserve mismatch"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_change_policy_forbid_new_descendant_rejects_publish() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        base = _base_artifact(workspace)
        (base / "secrets").mkdir()
        _change_policy(workspace, forbid=["secrets"])
        artifact = _artifact(workspace.output / "challenges")
        (artifact / "secrets").mkdir()
        (artifact / "secrets" / "new-key.pem").write_text("secret\n", encoding="utf-8")
        contract = prepare_publication_contract(paths, workspace, payload)

        with pytest.raises(WorkspacePromotionError, match="forbid newly added path"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_change_policy_all_clear_publishes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _base_artifact(workspace)
        _change_policy(workspace, preserve=["artifact.txt"], forbid=["secrets"])
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)

        result = publish_workspace_output(paths, workspace, contract=contract)

        assert result.outcome == "succeeded"


def test_legacy_promotion_symbol_is_not_forwarded() -> None:
    with pytest.raises(WorkspacePromotionError, match="promote_claimed_outputs removed"):
        promote_claimed_outputs(None, None, None)  # type: ignore[arg-type]
