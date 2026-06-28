"""Regression: research models must drop NUL bytes before they reach PostgreSQL.

Agent free text (e.g. describing the PE signature ``PE\\x00\\x00``) can contain
NUL, which PostgreSQL text/jsonb columns reject. The model-level ``@validates``
hooks strip NUL on assignment, so these run without a database.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from persistence.models.research import ResearchFinding, ResearchSource


class NulSanitizationTests(unittest.TestCase):
    def test_finding_summary_strips_nul(self):
        finding = ResearchFinding(
            id=uuid4(),
            research_run_id=uuid4(),
            kind="prerequisite",
            label="PE Structure Fundamentals",
            summary="PE signature (PE\x00\x00), COFF header",
            technique_family="platform",
        )
        self.assertEqual(finding.summary, "PE signature (PE), COFF header")
        self.assertNotIn("\x00", finding.summary)

    def test_finding_nullable_field_passes_through(self):
        finding = ResearchFinding(
            id=uuid4(),
            research_run_id=uuid4(),
            kind="technique",
            label="ok",
            summary="ok",
            technique_family=None,
        )
        self.assertIsNone(finding.technique_family)

    def test_source_fields_strip_nul(self):
        source = ResearchSource(
            id=uuid4(),
            research_run_id=uuid4(),
            url="https://example.com/x",
            title="bin\x00ary page",
            summary="garbled\x00content",
            content_hash="0" * 64,
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(source.title, "binary page")
        self.assertEqual(source.summary, "garbledcontent")


if __name__ == "__main__":
    unittest.main()
