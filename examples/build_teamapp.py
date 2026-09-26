"""Build the standalone `teamapp` demo repository (Phase 9).

Usage:
    uv run python examples/build_teamapp.py [dest]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add tests/fixtures to import path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures.build_teamapp import build_teamapp as _build


def build_demo_teamapp(dest: Path | None = None) -> Path:
    target = dest or (ROOT / "examples" / "teamapp")
    if target.exists():
        import shutil

        shutil.rmtree(target)

    root = _build(target)

    # Write default agent-factory.yaml
    af_config = {
        "version": 1,
        "project": {
            "id": "teamapp",
            "name": "TeamApp Demo",
            "languages": ["typescript"],
        },
        "checks": {
            "test": "npm test",
            "build": "npm run build",
            "lint": "npm run lint",
        },
    }
    (root / "agent-factory.yaml").write_text(json.dumps(af_config, indent=2), encoding="utf-8")
    return root


if __name__ == "__main__":
    dest_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    built = build_demo_teamapp(dest_path)
    print(f"Built demo teamapp at: {built}")
