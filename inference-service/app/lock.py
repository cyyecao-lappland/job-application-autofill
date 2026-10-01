import os


class InstanceLock:
    """OS lock survives stale lock files and is released on process exit."""

    def __init__(self, data_dir, filename="service.lock"):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.file = open(data_dir / filename, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, 2)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another E5 service owns this data directory") from None

    def close(self):
        self.file.close()
