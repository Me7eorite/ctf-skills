"""Deduplicate research_sources rows before applying migration 0009."""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from persistence.session import SessionFactory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="rewrite references and delete duplicates")
    args = parser.parse_args()

    session = SessionFactory()()
    try:
        groups = session.execute(
            sa.text(
                """
                SELECT research_run_id, content_hash, array_agg(id ORDER BY id) AS ids
                FROM research_sources
                GROUP BY research_run_id, content_hash
                HAVING COUNT(*) > 1
                ORDER BY research_run_id, content_hash
                """
            )
        ).all()
        if not groups:
            print("no duplicate research_sources rows found")
            return 0
        for run_id, content_hash, ids in groups:
            keep = ids[0]
            delete_ids = ids[1:]
            print(f"run={run_id} content_hash={content_hash} keep={keep} delete={delete_ids}")
            if args.apply:
                session.execute(
                    sa.text(
                        "UPDATE research_finding_sources SET source_id=:keep WHERE source_id = ANY(:delete_ids)"
                    ),
                    {"keep": keep, "delete_ids": delete_ids},
                )
                session.execute(
                    sa.text("DELETE FROM research_sources WHERE id = ANY(:delete_ids)"),
                    {"delete_ids": delete_ids},
                )
        if args.apply:
            session.commit()
            print(f"deduplicated {len(groups)} duplicate groups")
        else:
            session.rollback()
            print("dry-run only; rerun with --apply to modify rows")
        return 0
    except Exception as exc:
        session.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
