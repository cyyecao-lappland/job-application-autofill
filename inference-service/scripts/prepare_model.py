"""Explicit online provisioning only; the running service never downloads."""
import argparse
import json
from pathlib import Path
import shutil
import urllib.request

REPOSITORY = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision", choices=("int8", "fp16"), default="fp16")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    args.destination = args.destination or Path("models/v1-fp16" if args.precision == "fp16" else "models/v1-base")
    artifact = f"onnx/model_{args.precision}.onnx"
    args.destination.mkdir(parents=True, exist_ok=True)
    for remote, local in [("tokenizer.json", "tokenizer.json"), (artifact, "model.onnx")]:
        target = args.destination / local
        if target.exists():
            raise SystemExit(f"Refusing to overwrite {target}; use a new model directory")
        temporary = target.with_suffix(target.suffix + ".part")
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{remote}"
        print(f"Downloading {remote}", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "local-e5-provision/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(target)
    (args.destination / "source.json").write_text(json.dumps({
        "repository": REPOSITORY, "revision": REVISION, "artifact": artifact,
        "upstream": "intfloat/multilingual-e5-small", "dimension": 384,
        "precision": args.precision.upper(), "license": "MIT",
    }, indent=2), encoding="utf-8")
    print("Provisioned. Restart with a new E5_MODEL_VERSION whenever artifacts change.")


if __name__ == "__main__":
    main()
