"""Resident server with a dedicated Windows console; closing it ends the service."""
import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
import sys

import uvicorn

from .config import Settings


def attach_console():
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("Local Field Retrieval Service - Close window to stop")
        # The launcher can itself have redirected output. Explicitly route this
        # child's output to its new console rather than the parent's pipes.
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    print("Local Field Retrieval Service", flush=True)
    print("Close this window to stop the service. No automatic restart.", flush=True)


async def serve(port, stop_file):
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.data_dir / "service.log", maxBytes=2_000_000,
        backupCount=3, encoding="utf-8",
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[handler, logging.StreamHandler(sys.stderr)], force=True)
    config = uvicorn.Config(
        "app.main:create_app", factory=True, host="127.0.0.1", port=port,
        workers=1, access_log=False, log_config=None,
    )
    server = uvicorn.Server(config)
    # Reserve the port before loading the model, so conflicts fail immediately.
    sock = config.bind_socket()

    async def watch_stop():
        while not stop_file.exists():
            await asyncio.sleep(0.1)
        server.should_exit = True

    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve(sockets=[sock])
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        sock.close()
        stop_file.unlink(missing_ok=True)
        handler.close()


if __name__ == "__main__":
    attach_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.stop_file))
