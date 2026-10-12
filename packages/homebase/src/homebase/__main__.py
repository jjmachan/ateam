import argparse
from pathlib import Path

import uvicorn

from homebase.app import create_app
from homebase.identity import HeaderIdentity, TailscaleIdentity


def main() -> None:
    parser = argparse.ArgumentParser(prog="homebase", description="Run the A-Team HomeBase server.")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/homebase")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use the machine's tailnet IP")
    parser.add_argument("--port", type=int, default=7420)
    parser.add_argument(
        "--trust-header", action="store_true", help="identify callers by X-ATeam-Node header (development only)"
    )
    args = parser.parse_args()
    identity = HeaderIdentity() if args.trust_header else TailscaleIdentity()
    uvicorn.run(create_app(args.data_dir, identity), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
