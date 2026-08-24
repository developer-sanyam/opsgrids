from __future__ import annotations

import sys
from pathlib import Path

# The service directory is the import root: every service exposes a top-level `app`,
# so only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
