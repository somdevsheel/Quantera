"""python -m quantera.api  ->  http://127.0.0.1:8002 (QUANTERA_API_PORT to change)."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("quantera.api.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("QUANTERA_API_PORT", "8002")), workers=1)
