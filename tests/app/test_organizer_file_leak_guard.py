"""Lightweight (no-DB) regression tests for the organizer-file-leak guard.

These exercise ``_assert_no_organizer_file_leaks`` directly so the JSON
serialization path is covered without a PostgreSQL fixture. A missing
``import json`` in the service module previously made this raise ``NameError``
and turned the repair endpoint into a 500.
"""

from __future__ import annotations

import unittest

from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationService,
)


class OrganizerFileLeakGuardTests(unittest.TestCase):
    def test_clean_payload_passes(self):
        # 不含组织者文件引用的 payload 不应抛错（覆盖 json.dumps 序列化路径）。
        BuildAttemptRevalidationService._assert_no_organizer_file_leaks(
            {"challenges": [{"id": "re-0001", "category": "re"}]}
        )

    def test_generation_guidance_reference_is_allowed_for_non_repair_payload(self):
        BuildAttemptRevalidationService._assert_no_organizer_file_leaks(
            {"hint": "write metadata.json and challenge.yml as required files"}
        )

    def test_repair_context_metadata_reference_is_rejected(self):
        with self.assertRaisesRegex(
            BuildAttemptRevalidationError, "metadata.json"
        ):
            BuildAttemptRevalidationService._assert_no_organizer_file_leaks(
                {
                    "repair_requested": True,
                    "repair_context": {"hint": "read metadata.json for the flag"},
                }
            )


if __name__ == "__main__":
    unittest.main()
