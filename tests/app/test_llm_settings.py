"""Tests for the LLM provider settings module and API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from domain.llm_settings import (
    load_secret_settings,
    load_settings,
    mask_api_key,
    save_settings,
)
from web.dashboard import DashboardService
from web.server import create_app


class MaskConventionTests(unittest.TestCase):
    def test_long_key_uses_first_three_last_four(self) -> None:
        self.assertEqual(mask_api_key("sk-anthropic-abcdefghij"), "sk-***ghij")

    def test_short_key_collapses_to_fixed_mask(self) -> None:
        self.assertEqual(mask_api_key("short"), "*****")
        self.assertEqual(mask_api_key("exact8ch"), "*****")

    def test_empty_key_returns_empty(self) -> None:
        self.assertEqual(mask_api_key(""), "")
        self.assertEqual(mask_api_key(None), "")


class _Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.paths.hermes_home.mkdir(parents=True, exist_ok=True)


class LoadSettingsTests(_Sandbox):
    def test_load_masks_anthropic_key_from_auth_pool(self) -> None:
        (self.paths.hermes_home / "config.yaml").write_text(
            "model:\n  provider: anthropic\n  base_url: https://api.anthropic.com\n"
            "  model: claude-3-5-sonnet-20241022\n",
            encoding="utf-8",
        )
        (self.paths.hermes_home / "auth.json").write_text(
            json.dumps({"credential_pool": {"anthropic": "sk-anthropic-abcdefghij"}}),
            encoding="utf-8",
        )
        loaded = load_settings(self.paths)
        self.assertEqual(loaded["provider"], "anthropic")
        self.assertEqual(loaded["api_key_masked"], "sk-***ghij")
        self.assertNotIn("api_key", loaded)
        self.assertNotIn("sk-anthropic-abcdefghij", json.dumps(loaded))

    def test_load_returns_custom_key_from_config_block(self) -> None:
        (self.paths.hermes_home / "config.yaml").write_text(
            "model:\n  provider: custom\n  base_url: https://example.test\n"
            "  model: my-model\n  api_key: super-secret-key-here\n",
            encoding="utf-8",
        )
        loaded = load_settings(self.paths)
        self.assertEqual(loaded["provider"], "custom")
        self.assertEqual(loaded["api_key_masked"], "sup***here")


class SaveSettingsTests(_Sandbox):
    def test_unrelated_keys_preserved_across_writes(self) -> None:
        # Pre-populate both files with extra keys outside our schema.
        (self.paths.hermes_home / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "telemetry": {"enabled": False},
                    "model": {"provider": "anthropic", "model": "old"},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (self.paths.hermes_home / "auth.json").write_text(
            json.dumps(
                {
                    "unrelated": {"keep": "me"},
                    "credential_pool": {"openai": "sk-openai-xyzwvu1234"},
                },
            ),
            encoding="utf-8",
        )
        save_settings(
            self.paths,
            {
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet-20241022",
                "api_key": "sk-anthropic-abcdefghij",
            },
        )
        config = yaml.safe_load(
            (self.paths.hermes_home / "config.yaml").read_text(encoding="utf-8")
        )
        auth = json.loads(
            (self.paths.hermes_home / "auth.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["telemetry"], {"enabled": False})
        self.assertEqual(config["model"]["provider"], "anthropic")
        self.assertEqual(config["model"]["model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(auth["unrelated"], {"keep": "me"})
        self.assertEqual(auth["credential_pool"]["openai"], "sk-openai-xyzwvu1234")
        self.assertEqual(
            auth["credential_pool"]["anthropic"], "sk-anthropic-abcdefghij"
        )

    def test_mask_placeholder_preserves_stored_key(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o-mini",
                "api_key": "sk-openai-original123",
            },
        )
        current = load_settings(self.paths)
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o",
                "api_key": current["api_key_masked"],
            },
        )
        secret = load_secret_settings(self.paths)
        self.assertEqual(secret["api_key"], "sk-openai-original123")
        self.assertEqual(secret["model"], "gpt-4o")

    def test_new_key_overwrites_stored_key(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o-mini",
                "api_key": "sk-openai-original123",
            },
        )
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o-mini",
                "api_key": "sk-openai-replaced987",
            },
        )
        secret = load_secret_settings(self.paths)
        self.assertEqual(secret["api_key"], "sk-openai-replaced987")

    def test_plain_text_key_never_in_serialized_response(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet-20241022",
                "api_key": "sk-anthropic-abcdefghij",
            },
        )
        serialized = json.dumps(load_settings(self.paths))
        self.assertNotIn("sk-anthropic-abcdefghij", serialized)

    def test_unsupported_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            save_settings(
                self.paths,
                {"provider": "bogus", "base_url": "", "model": ""},
            )


class TestConnectionMockTests(_Sandbox):
    def test_test_connection_mocked_success(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o-mini",
                "api_key": "sk-openai-original123",
            },
        )
        with patch(
            "domain.llm_settings._http_request", return_value=(200, b"{}")
        ) as patched:
            from domain.llm_settings import test_connection

            result = test_connection(self.paths)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["model"], "gpt-4o-mini")
        self.assertGreaterEqual(result["latency_ms"], 0)
        called_url = patched.call_args[0][0]
        self.assertIn("/v1/models", called_url)

    def test_test_connection_scrubs_api_key_in_error(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-4o-mini",
                "api_key": "sk-openai-original123",
            },
        )
        with patch(
            "domain.llm_settings._http_request",
            side_effect=OSError("upstream sk-openai-original123 unreachable"),
        ):
            from domain.llm_settings import test_connection

            result = test_connection(self.paths)
        self.assertFalse(result["ok"])
        self.assertNotIn("sk-openai-original123", json.dumps(result))


class LLMRouterTests(_Sandbox):
    def _client(self) -> TestClient:
        service = DashboardService(self.paths)
        return TestClient(create_app(service))

    def test_get_then_put_with_mask_preserves_key(self) -> None:
        save_settings(
            self.paths,
            {
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet-20241022",
                "api_key": "sk-anthropic-abcdefghij",
            },
        )
        with self._client() as client:
            initial = client.get("/api/settings/llm")
            self.assertEqual(initial.status_code, 200)
            payload = initial.json()
            self.assertEqual(payload["api_key_masked"], "sk-***ghij")
            put = client.put(
                "/api/settings/llm",
                json={
                    "provider": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model": "claude-3-haiku-20240307",
                    "api_key": payload["api_key_masked"],
                },
            )
            self.assertEqual(put.status_code, 200)
        self.assertNotIn(
            "sk-anthropic-abcdefghij", put.text
        )
        secret = load_secret_settings(self.paths)
        self.assertEqual(secret["api_key"], "sk-anthropic-abcdefghij")
        self.assertEqual(secret["model"], "claude-3-haiku-20240307")

    def test_put_rejects_unknown_provider(self) -> None:
        with self._client() as client:
            response = client.put(
                "/api/settings/llm",
                json={"provider": "nope", "base_url": "", "model": ""},
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
