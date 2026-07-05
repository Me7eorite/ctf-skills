"""Load the Challenge Factory Hermes runtime bootstrap."""

from __future__ import annotations

try:
    from ctf_skills_hermes_bootstrap import install

    install()
except Exception:
    pass

