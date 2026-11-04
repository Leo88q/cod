"""Bounded, read-only local load smoke; not a production capacity benchmark."""
import argparse
import concurrent.futures
import statistics
import time
import urllib.request
from urllib.parse import urlparse

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8000")
parser.add_argument("--requests", type=int, default=100)
parser.add_argument("--workers", type=int, default=5)
args = parser.parse_args()
if urlparse(args.base).hostname not in ("127.0.0.1", "localhost"):
    parser.error("Local sandbox targets only")
if not 1 <= args.requests <= 1000 or not 1 <= args.workers <= 20:
    parser.error("Bounds: requests 1..1000, workers 1..20")


def request(_):
    started = time.monotonic()
    with urllib.request.urlopen(args.base.rstrip("/") + "/ready", timeout=10) as response:
        assert response.status == 200
    return (time.monotonic() - started) * 1000


with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
    durations = sorted(pool.map(request, range(args.requests)))
print({"requests": len(durations), "workers": args.workers,
       "median_ms": round(statistics.median(durations), 2),
       "p95_ms": round(durations[min(len(durations)-1, int(len(durations)*.95))], 2)})
