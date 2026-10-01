from dataclasses import asdict, dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    model_dir: Path
    data_dir: Path
    model_version: str = "v1-fp16"
    max_queue_size: int = 256
    max_batch_size: int = 64
    max_text_length: int = 8192
    max_tokens: int = 512
    intra_op_threads: int = 2

    def __post_init__(self):
        if not self.model_version.strip():
            raise ValueError("model_version must be nonempty")
        for key in ("max_queue_size", "max_batch_size", "max_text_length", "intra_op_threads"):
            if getattr(self, key) < 1:
                raise ValueError(f"{key} must be positive")
        if not 1 <= self.max_tokens <= 512:
            raise ValueError("max_tokens must be between 1 and 512")

    def identity(self):
        result = asdict(self)
        for key in ("model_dir", "data_dir"):
            result[key] = os.path.normcase(str(Path(result[key]).resolve()))
        return result

    @classmethod
    def from_env(cls):
        root = Path(__file__).resolve().parents[1]
        return cls(
            model_dir=Path(os.getenv("E5_MODEL_DIR", str(root / "models" / "v1-fp16"))),
            data_dir=Path(os.getenv("E5_DATA_DIR", str(root / "data"))),
            model_version=os.getenv("E5_MODEL_VERSION", "v1-fp16"),
            max_queue_size=int(os.getenv("E5_MAX_QUEUE_SIZE", "256")),
            max_batch_size=int(os.getenv("E5_MAX_BATCH_SIZE", "64")),
            intra_op_threads=int(os.getenv("E5_CPU_THREADS", "2")),
        )
