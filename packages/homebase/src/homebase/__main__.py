import argparse
import threading
import time
from pathlib import Path

import uvicorn

from homebase import gc
from homebase.app import create_app, is_stale
from homebase.identity import HeaderIdentity, TailscaleIdentity


def main() -> None:
    parser = argparse.ArgumentParser(prog="homebase", description="Run the A-Team HomeBase server.")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/homebase")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use the machine's tailnet IP")
    parser.add_argument("--port", type=int, default=7420)
    parser.add_argument(
        "--trust-header", action="store_true", help="identify callers by X-ATeam-Node header (development only)"
    )
    parser.add_argument("--gc-every-hours", type=float, default=24, help="0 disables scheduled GC")
    args = parser.parse_args()
    identity = HeaderIdentity() if args.trust_header else TailscaleIdentity()
    app = create_app(args.data_dir, identity)
    if args.gc_every_hours > 0:
        threading.Thread(target=_gc_loop, args=(app, args.gc_every_hours * 3600), daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port)


def _gc_loop(app, interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            collected = [v for v in gc.run(app.state.db, app.state.files_dir, is_stale, dry_run=False) if v["collect"]]
            print(f"gc: collected {len(collected)} session(s)", flush=True)
        except Exception as e:  # keep the loop alive; the next run retries
            print(f"gc failed: {e}", flush=True)


if __name__ == "__main__":
    main()
