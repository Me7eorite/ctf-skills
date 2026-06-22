from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
        assert read_json(workspace.state / "highest-committed-generation.json")["publish_generation"] == 1


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


def test_repository_has_no_legacy_promotion_call_symbol() -> None:
    repository = Path(__file__).resolve().parents[2]
    matches: list[str] = []
    for path in (repository / "src").rglob("*.py"):
        if "promote_claimed_outputs" + "(" in path.read_text(encoding="utf-8"):
            matches.append(str(path.relative_to(repository)))
    assert matches == []


def test_no_change_policy_publishes_normally() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)

        result = publish_workspace_output(paths, workspace, contract=contract)

        assert result.outcome == "succeeded"
        assert result.output_manifest_hash


def test_contract_rejects_post_preparation_input_mutation() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        workspace.input.joinpath("shard.json").write_text("{}\n", encoding="utf-8")

        with pytest.raises(WorkspacePromotionError, match="shard.json changed"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_change_policy_rejects_symlink_and_missing_json_field() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        base = _base_artifact(workspace)
        (base / "linked").symlink_to(base / "artifact.txt")
        _change_policy(workspace, preserve=["linked"])
        with pytest.raises(WorkspacePromotionError, match="symlink"):
            prepare_publication_contract(paths, workspace, payload)

        (workspace.input / "change-policy.json").unlink()
        (base / "linked").unlink()
        _change_policy(workspace, preserve=["metadata.json#missing"])
        _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        with pytest.raises(WorkspacePromotionError, match="missing JSON field"):
            publish_workspace_output(paths, workspace, contract=contract)


def test_resume_target_duplicate_fails_before_canonical_mutation() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {
            "execution_mode": "resume",
            "resume_from_shard_basename": "prior.json",
            "challenges": [{"id": "web-0001", "category": "web"}],
        }
        workspace = _workspace(paths, payload)
        old = _artifact(paths.challenges, slug="old")
        _artifact(workspace.output / "challenges", slug="old")
        _artifact(workspace.output / "challenges", slug="other")
        contract = prepare_publication_contract(
            paths,
            workspace,
            payload,
            resume_output_targets={"web-0001": "output/challenges/web/web-0001-old"},
        )

        with pytest.raises(WorkspacePromotionError, match="multiple output directories"):
            publish_workspace_output(paths, workspace, contract=contract)
        assert old.is_dir()


def test_manifest_hash_includes_mode_empty_directories_and_ambiguous_names() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        candidate = _artifact(root)
        baseline = build_publisher._output_manifest_hash({"web-0001": candidate})
        os.chmod(candidate / "artifact.txt", 0o755)
        executable = build_publisher._output_manifest_hash({"web-0001": candidate})
        assert executable != baseline

        (candidate / "empty").mkdir()
        with_empty = build_publisher._output_manifest_hash({"web-0001": candidate})
        assert with_empty != executable

        (candidate / "a|b").write_text("c", encoding="utf-8")
        delimiter_name = build_publisher._output_manifest_hash({"web-0001": candidate})
        (candidate / "a|b").unlink()
        (candidate / "a").write_text("b|c", encoding="utf-8")
        assert build_publisher._output_manifest_hash({"web-0001": candidate}) != delimiter_name


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected"),
    [
        ("BUILD_PUBLISH_MAX_BYTES", "1", "byte limit"),
        ("BUILD_PUBLISH_MAX_DEPTH", "1", "path-depth limit"),
    ],
)
def test_byte_and_depth_limits_fail_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
    expected: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        (artifact / "nested").mkdir()
        (artifact / "nested" / "file.txt").write_text("payload", encoding="utf-8")
        contract = prepare_publication_contract(paths, workspace, payload)
        monkeypatch.setenv(env_name, env_value)

        with pytest.raises(WorkspacePromotionError, match=expected):
            publish_workspace_output(paths, workspace, contract=contract)
        assert not list((paths.challenges / "web").glob("web-0001-*"))


def test_committed_journal_recovery_advances_high_water_idempotently() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        write_json(
            workspace.state / "publish-journal.json",
            {
                "phase": "committed",
                "publish_generation": 4,
                "output_manifest_hash": "abc",
                "category": "web",
                "entries": [],
            },
        )

        build_publisher._bootstrap_recover_journal(paths, workspace)
        build_publisher._bootstrap_recover_journal(paths, workspace)

        high_water = read_json(workspace.state / "highest-committed-generation.json")
        assert high_water == {"publish_generation": 4, "output_manifest_hash": "abc"}
        assert not (workspace.state / "publish-journal.json").exists()


def test_incomplete_journal_recovery_removes_new_and_restores_predecessor() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        predecessor = _artifact(paths.challenges, slug="old")
        quarantine = workspace.root / "quarantine" / "web" / predecessor.name
        quarantine.parent.mkdir(parents=True)
        predecessor.replace(quarantine)
        new = _artifact(paths.challenges, slug="new")
        write_json(
            workspace.state / "publish-journal.json",
            {
                "phase": "manifest",
                "publish_generation": 1,
                "output_manifest_hash": "abc",
                "category": "web",
                "entries": [
                    {
                        "claimed_id": "web-0001",
                        "canonical": str(new),
                        "temp": str(paths.challenges / "web" / ".missing-temp"),
                    }
                ],
            },
        )

        build_publisher._bootstrap_recover_journal(paths, workspace)

        assert not new.exists()
        assert predecessor.is_dir()
        assert not (workspace.state / "publish-journal.json").exists()


def test_second_id_commit_failure_rolls_back_entire_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {
            "challenges": [
                {"id": "web-0001", "category": "web"},
                {"id": "web-0002", "category": "web"},
            ]
        }
        old_one = _artifact(paths.challenges, challenge_id="web-0001", slug="old")
        old_two = _artifact(paths.challenges, challenge_id="web-0002", slug="old")
        workspace = _workspace(paths, payload)
        _artifact(workspace.output / "challenges", challenge_id="web-0001", slug="new")
        _artifact(workspace.output / "challenges", challenge_id="web-0002", slug="new")
        contract = prepare_publication_contract(paths, workspace, payload)
        original_replace = Path.replace

        def fail_second(source: Path, target: Path):
            if source.name.startswith(".workspace-") and target.name == "web-0002-new":
                raise OSError("second destination blocked")
            return original_replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_second)
        with pytest.raises(OSError, match="second destination blocked"):
            publish_workspace_output(paths, workspace, contract=contract)

        assert old_one.is_dir()
        assert old_two.is_dir()
        assert not (paths.challenges / "web" / "web-0001-new").exists()
        assert not (paths.challenges / "web" / "web-0002-new").exists()


def test_overlapping_publications_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        first_workspace = _workspace(paths, payload)
        second_shard = paths.shards / "running" / "web.second.json"
        write_json(second_shard, payload)
        second_workspace = prepare_workspace(
            paths,
            shard=second_shard,
            original_shard_name="web-second.json",
            worker="worker-2",
        )
        _artifact(first_workspace.output / "challenges", slug="first")
        _artifact(second_workspace.output / "challenges", slug="second")
        contracts = [
            prepare_publication_contract(paths, first_workspace, payload),
            prepare_publication_contract(paths, second_workspace, payload),
        ]
        barrier = threading.Barrier(2)
        state_lock = threading.Lock()
        active = 0
        max_active = 0
        original_replace = Path.replace

        def observe_replace(source: Path, target: Path):
            nonlocal active, max_active
            if source.name.startswith(".workspace-") and target.name.startswith("web-0001-"):
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                try:
                    return original_replace(source, target)
                finally:
                    with state_lock:
                        active -= 1
            return original_replace(source, target)

        monkeypatch.setattr(Path, "replace", observe_replace)

        def publish(index: int):
            barrier.wait(timeout=5)
            workspace = (first_workspace, second_workspace)[index]
            return publish_workspace_output(paths, workspace, contract=contracts[index])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, range(2)))

        assert all(result.outcome == "succeeded" for result in results)
        assert max_active == 1


def test_cross_device_preflight_rejects_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        canonical = paths.challenges / "web"
        quarantine = workspace.root / "quarantine" / "web"
        original_stat = Path.stat

        def different_devices(path: Path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == workspace.output:
                return os.stat_result((*result[:2], result.st_dev + 1, *result[3:]))
            return result

        monkeypatch.setattr(Path, "stat", different_devices)
        with pytest.raises(WorkspacePromotionError, match="share one filesystem"):
            build_publisher._verify_same_filesystem(canonical, workspace, quarantine)


def _retained_workspace(paths: ProjectPaths, name: str, timestamp: float) -> Path:
    root = paths.executions / name
    (root / "state").mkdir(parents=True)
    (root / "quarantine" / "web" / "old").mkdir(parents=True)
    (root / "quarantine" / "web" / "old" / "artifact").write_text("x")
    write_json(
        root / "state" / "publish-status.json",
        {"status": "failed", "wall_clock_seconds": timestamp},
    )
    return root


def test_retention_sweep_removes_old_keeps_fresh_and_caps_twenty() -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        now = time.time()
        old = _retained_workspace(paths, "old", now - 8 * 86400)
        fresh = _retained_workspace(paths, "fresh", now)
        roots = [_retained_workspace(paths, f"cap-{index}", now - 100 + index) for index in range(21)]

        build_publisher._sweep_retention_roots(paths)

        assert not (old / "quarantine").exists()
        assert (fresh / "quarantine").exists()
        assert not (roots[0] / "quarantine").exists()
        assert sum((root / "quarantine").exists() for root in roots) == 19


def test_retention_sweep_error_is_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        monkeypatch.setattr(
            build_publisher,
            "_sweep_retention_roots",
            lambda _paths: (_ for _ in ()).throw(PermissionError("busy")),
        )
        monkeypatch.setattr(build_publisher.time, "monotonic", lambda: 10_000.0)
        build_publisher._LAST_SWEEP_AT.update(value=0.0, pending=0.0)

        build_publisher._run_retention_sweep(paths)


def test_retention_skips_active_journal_and_repeated_publish_keeps_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        paths = _paths(Path(temp))
        active = _retained_workspace(paths, "active", time.time() - 8 * 86400)
        write_json(active / "state" / "publish-journal.json", {"phase": "stage"})
        build_publisher._sweep_retention_roots(paths)
        assert (active / "quarantine").exists()

        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        workspace = _workspace(paths, payload)
        artifact = _artifact(workspace.output / "challenges")
        contract = prepare_publication_contract(paths, workspace, payload)
        stage_generations: list[int] = []
        original_journal = build_publisher._write_publish_journal

        def observe_journal(workspace_arg, generation, output_hash, **kwargs):
            if kwargs.get("phase") == "stage":
                stage_generations.append(generation)
            return original_journal(workspace_arg, generation, output_hash, **kwargs)

        monkeypatch.setattr(build_publisher, "_write_publish_journal", observe_journal)
        publish_workspace_output(paths, workspace, contract=contract)
        (artifact / "artifact.txt").write_text("repair\n", encoding="utf-8")
        publish_workspace_output(paths, workspace, contract=contract)

        assert stage_generations == [1, 2]
        assert workspace.input.joinpath("shard.json").exists()
