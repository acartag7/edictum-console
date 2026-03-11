"""Generate OpenAPI spec from the FastAPI app.

Usage: python scripts/generate-openapi.py [output_path]
Default output: docs/openapi.json
"""

import json
import os
import sys
from pathlib import Path

# Set required env vars to avoid startup validation failures
os.environ.setdefault("EDICTUM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("EDICTUM_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("EDICTUM_SECRET_KEY", "a" * 64)

from edictum_server.main import app  # noqa: E402

output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/openapi.json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"OpenAPI spec written to {output}")
