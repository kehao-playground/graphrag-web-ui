"""Regenerate ../openapi.json (committed; CI diffs it — spec A5.2)."""
import json
from pathlib import Path

from graphrag_ui.main import create_app

out = Path(__file__).resolve().parents[2] / "openapi.json"
spec = create_app().openapi()
out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
