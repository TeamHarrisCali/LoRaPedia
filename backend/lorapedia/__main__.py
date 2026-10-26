"""`python -m lorapedia` (or the `lorapedia` command): run the server."""

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="lorapedia", description="LoRaPedia: offline Wikipedia Q&A for Meshtastic.")
    parser.add_argument("--host", default=os.environ.get("LORAPEDIA_HOST", "127.0.0.1"),
                        help="Address to listen on. Use 0.0.0.0 to reach it from other devices on your network.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LORAPEDIA_PORT", "8000")))
    parser.add_argument("--data", default=None, help="Data directory (default: $LORAPEDIA_DATA or ./data).")
    args = parser.parse_args()
    if args.data:
        os.environ["LORAPEDIA_DATA"] = args.data

    import uvicorn
    uvicorn.run("lorapedia.api:create_app", factory=True, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
