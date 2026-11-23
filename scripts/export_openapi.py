"""Write the platform API's OpenAPI schema to docs/openapi.json (input for the web and Android
TypeScript types: `npm run gen:api` in apps/web and apps/android)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantera.api.app import create_app  # noqa: E402
from quantera.core.config import get_settings  # noqa: E402

spec = create_app(get_settings()).openapi()
(ROOT / "docs" / "openapi.json").write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
print(f"docs/openapi.json: {len(spec['paths'])} paths")
