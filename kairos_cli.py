from __future__ import annotations

"""Zero-install entry point for a KAIROS source release.

This wrapper deliberately adds only the bundled harness package to sys.path and then delegates to
its normal CLI. It exists so framework bootstrap search works immediately after unpacking the
release, before a virtual environment or project workspace exists.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HARNESS = ROOT / "kairos" / "kairos_harness"
if not (HARNESS / "kairos" / "__main__.py").is_file():
    raise SystemExit(f"bundled KAIROS harness is missing: {HARNESS}")
sys.path.insert(0, str(HARNESS))

from kairos.cli import main  # noqa: E402

raise SystemExit(main())
