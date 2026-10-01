"""Temporary benchmark server; stop file permits graceful Windows shutdown."""
import asyncio
from pathlib import Path
import sys
import uvicorn


async def main():
    port, stop_path = int(sys.argv[1]), Path(sys.argv[2])
    server = uvicorn.Server(uvicorn.Config(
        "app.main:create_app", factory=True, host="127.0.0.1",
        port=port, workers=1, access_log=False,
    ))

    async def watch():
        while not stop_path.exists():
            await asyncio.sleep(0.1)
        server.should_exit = True

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
