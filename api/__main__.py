"""python -m api [--host 127.0.0.1] [--port 8000] [--release TAG] [--mode internal_research|redistribution]"""
import argparse
import os

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--release")
    ap.add_argument("--mode")
    ap.add_argument("--drift-check", choices=["fast", "full"])
    ap.add_argument("--workers", type=int, default=4)  # §6 targets were met with 4
    a = ap.parse_args()
    for env, val in (("QUANTERA_RELEASE", a.release), ("QUANTERA_API_MODE", a.mode),
                     ("QUANTERA_DRIFT_CHECK", a.drift_check)):
        if val:
            os.environ[env] = val
    uvicorn.run("api.main:app", host=a.host, port=a.port, workers=a.workers, log_level="info")


if __name__ == "__main__":
    main()
