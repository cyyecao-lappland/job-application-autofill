import json
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
from tokenizers import Tokenizer
import onnxruntime as ort

DIMENSION = 384


def normalize(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != DIMENSION or not np.isfinite(vectors).all():
        raise ValueError("Invalid embedding shape or nonfinite values")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("Zero embedding")
    return np.ascontiguousarray(vectors / norms, dtype=np.float32)


class Encoder:
    """One offline CPU session. Called exclusively on the service worker thread."""

    def __init__(self, settings):
        source = settings.model_dir / "source.json"
        self.precision = json.loads(source.read_text(encoding="utf-8")).get("precision", "unknown") if source.exists() else "unknown"
        self.tokenizer = Tokenizer.from_file(str(settings.model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=settings.max_tokens)
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise ValueError("E5 tokenizer must define <pad>")
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.intra_op_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(
            str(settings.model_dir / "model.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"],
        )
        self.inputs = [item.name for item in self.session.get_inputs()]
        if not set(self.inputs) <= {"input_ids", "attention_mask", "token_type_ids"}:
            raise ValueError("Unsupported model inputs")
        outputs = {item.name for item in self.session.get_outputs()}
        if "last_hidden_state" not in outputs:
            raise ValueError("Expected E5 last_hidden_state output")

    def encode(self, texts, role="query"):
        if role not in ("query", "passage"):
            raise ValueError("Unsupported E5 role")
        # Callers supply unprefixed text; service owns the E5 prefix.
        encoded = self.tokenizer.encode_batch([f"{role}: {text}" for text in texts])
        arrays = {
            "input_ids": np.asarray([x.ids for x in encoded], dtype=np.int64),
            "attention_mask": np.asarray([x.attention_mask for x in encoded], dtype=np.int64),
            "token_type_ids": np.asarray([x.type_ids for x in encoded], dtype=np.int64),
        }
        hidden = self.session.run(["last_hidden_state"], {k: arrays[k] for k in self.inputs})[0]
        mask = arrays["attention_mask"][..., None]
        pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1)
        return normalize(pooled)
