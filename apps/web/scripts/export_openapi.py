"""Write apps/web/openapi.json from the FastAPI app (no server needed).

The API's document already carries the SPEC 17.2 `RunEvent` union (orchestrix_api.main).
Run from the repo root: `uv run python apps/web/scripts/export_openapi.py`.
"""

import json
import sys
from pathlib import Path

from orchestrix_api.main import create_app

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("apps/web/openapi.json")
spec = create_app().openapi()
out.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
print(f"wrote {out} ({len(spec['paths'])} paths, {len(spec['components']['schemas'])} schemas)")
