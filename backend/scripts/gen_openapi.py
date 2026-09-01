"""Regenerate ../openapi.json (committed; CI diffs it — spec A5.2)."""

import json
import os
from pathlib import Path

# create_app() reads Settings, and AUTH_MODE=local refuses a weak JWT_SECRET
# (config.py). Nothing here signs a token — the app is built only to dump its
# schema — so supply a throwaway value rather than making schema generation
# depend on a real deployment secret. setdefault, so a configured environment
# still wins.
os.environ.setdefault("JWT_SECRET", "openapi-generation-only-not-a-real-secret")

from graphrag_ui.main import create_app  # noqa: E402  (must follow the env setup above)

out = Path(__file__).resolve().parents[2] / "openapi.json"
spec = create_app().openapi()
out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
