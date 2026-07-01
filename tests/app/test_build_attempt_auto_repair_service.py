import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from core.jsonio import write_json
from core.paths import ProjectPaths
from services.build_attempt_auto_repair_service import AutoRepairResult, auto_repair_challenge
from services.build_attempt_repair_service import (
    BuildAttemptRepairService,
    _challenge_directory,
)
