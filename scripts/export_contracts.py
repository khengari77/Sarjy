"""Export public API schemas without booting services or making provider calls."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps/api"))
from app.main import app
from app.domain.contracts import contract_components

target = root / "packages/contracts/openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
schema = app.openapi()
components = schema.setdefault("components", {}).setdefault("schemas", {})
components.update(contract_components())
target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
