"""Real loopback HTTP smoke/latency test, using synthetic field definitions only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import os

import numpy as np

FIELDS = [
    ("visa_sponsorship", "Employer visa sponsorship requirement. 当前或未来是否需要雇主提供工作签证担保。", "boolean"),
    ("work_authorization", "Legal authorization to work in the country. 是否拥有当地合法工作资格。", "boolean"),
    ("degree", "Highest academic degree obtained. 已获得的最高学历学位。", "select"),
    ("major", "Academic major or field of study. 所学专业或研究方向。", "text"),
    ("citizenship", "Citizenship or nationality. 国籍。", "text"),
    ("permanent_residency", "Permanent resident or green card status. 是否具有永久居留权或绿卡。", "boolean"),
]
QUERIES = [
    ("Will you require employer visa sponsorship?", "visa_sponsorship"),
    ("是否需要公司提供工作签证担保？", "visa_sponsorship"),
    ("Are you legally authorized to work in this country?", "work_authorization"),
    ("是否拥有当地合法工作资格？", "work_authorization"),
    ("What is your highest academic degree?", "degree"),
    ("您所学的专业是什么？", "major"),
    ("What is your nationality?", "citizenship"),
    ("您是否持有永久居民绿卡？", "permanent_residency"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=Path("models/v1-base"))
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/latest.json"))
    args = parser.parse_args()
    if min(args.clients, args.requests, args.batch_size) < 1 or args.batch_size > 64:
        parser.error("positive counts and batch-size <= 64 required")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="e5-benchmark-") as temp:
        env = dict(os.environ, E5_MODEL_DIR=str(args.model_dir.resolve()), E5_DATA_DIR=temp,
                   E5_MODEL_VERSION="v1-base", TOKENIZERS_PARALLELISM="false")
        log_path = Path(temp) / "server.log"
        stop_path = Path(temp) / "stop"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "scripts.benchmark_host", str(port), str(stop_path)],
                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            def request(path, payload=None):
                data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as response:
                    return json.load(response)
            try:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Server failed: " + log_path.read_text(encoding="utf-8"))
                    try:
                        request("/health")
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise TimeoutError("Service startup exceeded 90 seconds")
                        time.sleep(0.1)
                for field_id, canonical, field_type in FIELDS:
                    request("/v1/library/upsert", {"field_id": field_id, "canonical_text": canonical,
                                                 "aliases": [], "field_type": field_type})
                encoded = request("/v1/encode", {"texts": [x[0] for x in QUERIES]})
                matrix = np.asarray(encoded["embeddings"])
                assert matrix.shape == (8, 384)
                assert np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-5)
                result = request("/v1/similarity", {"texts": [x[0] for x in QUERIES], "top_k": 5})
                ranks = []
                for query, item in zip(QUERIES, result["results"], strict=True):
                    ids = [x["field_id"] for x in item["candidates"]]
                    ranks.append(ids.index(query[1]) + 1 if query[1] in ids else None)
                assert all(rank is not None and rank <= 3 for rank in ranks), ranks
                texts = [QUERIES[i % len(QUERIES)][0] for i in range(args.batch_size)]
                def timed(_):
                    started = time.perf_counter()
                    response = request("/v1/similarity", {"texts": texts, "top_k": 5})
                    assert len(response["results"]) == args.batch_size
                    return (time.perf_counter() - started) * 1000
                timed(0)  # warm-up
                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=args.clients) as pool:
                    latencies = list(pool.map(timed, range(args.requests)))
                elapsed = time.perf_counter() - started
                report = {
                    "kind": "synthetic_real_model_loopback_http",
                    "model_dir": str(args.model_dir.resolve()),
                    "clients": args.clients, "requests": args.requests, "batch_size": args.batch_size,
                    "elapsed_seconds": elapsed,
                    "http_latency_p50_ms": float(np.percentile(latencies, 50)),
                    "http_latency_p95_ms": float(np.percentile(latencies, 95)),
                    "query_ranks": ranks,
                    "recall_at_1": sum(r == 1 for r in ranks) / len(ranks),
                    "recall_at_3": sum(r is not None and r <= 3 for r in ranks) / len(ranks),
                    "recall_at_5": sum(r is not None and r <= 5 for r in ranks) / len(ranks),
                    "mrr": sum(1 / r if r else 0 for r in ranks) / len(ranks),
                    "health": request("/health"), "service_metrics": request("/metrics"),
                    "limitations": "8 synthetic bilingual queries, bilingual passages; no held-out accuracy claim, cross-language split or browser responsiveness measurement.",
                }
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                try:
                    stop_path.touch()
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
