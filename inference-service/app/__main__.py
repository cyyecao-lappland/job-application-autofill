import logging
import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # No reload or multiple-worker switch; loopback only, no access text logs.
    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8765,
                workers=1, access_log=False)
