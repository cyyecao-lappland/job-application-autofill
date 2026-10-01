"""Sequential real HTTP CPU precision comparison, with isolated databases/processes."""
import argparse
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import numpy as np
import psutil

from scripts.evaluate_bilingual_keys import evaluate

ROOT = Path(__file__).resolve().parents[1]


def run(precision, output):
    model = ROOT / 'models' / ('v1-base' if precision == 'int8' else 'v1-fp16')
    library = json.loads((ROOT / 'library/profile_fields.json').read_text(encoding='utf-8'))['fields']
    queries = []
    with (ROOT / 'benchmarks/profile_synonyms.tsv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            queries.extend(dict(key=row['key'], language=lang, label=row[lang]) for lang in ('zh', 'en'))
    for name, data in (('library', library), ('queries', queries)):
        (output / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    ids = {f['field_id']: f['source_key'] for f in library}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='e5-precision-') as temp:
        stop = Path(temp) / 'stop'
        env = dict(os.environ, E5_MODEL_DIR=str(model), E5_MODEL_VERSION=f'precision-{precision}',
                   E5_DATA_DIR=temp, E5_CPU_THREADS='2')
        with (output / f'{precision}-server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'scripts.benchmark_host', str(port), str(stop)],
                                       cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            done = threading.Event()
            sampler = None
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=120) as client:
                    deadline = time.monotonic() + 120
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError(f'Server exited; see {precision}-server.log')
                        try:
                            response = client.get('/health')
                            response.raise_for_status()
                            health = response.json()
                            if health['status'] == 'ready':
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise TimeoutError('Model startup timeout')
                        time.sleep(.1)
                    server = psutil.Process(health['pid'])
                    rss = [server.memory_info().rss]
                    def sample():
                        while not done.wait(.05):
                            rss.append(server.memory_info().rss)
                    sampler = threading.Thread(target=sample)
                    sampler.start()
                    for field in library:
                        client.post('/v1/library/upsert', json=field).raise_for_status()
                    result = dict(precision=precision, health=health, model_bytes=(model / 'model.onnx').stat().st_size,
                                  ready_rss_mib=rss[0] / 2**20, runs={})
                    for batch in (1, 32, 40):
                        result['runs'][str(batch)] = evaluate(client, queries, ids, 'bilingual_routed', 'with_section', batch)
                        print(precision, batch, result['runs'][str(batch)]['overall'], flush=True)
                    result['stability'] = {}
                    single = result['runs']['1']['rows']
                    for batch in ('32', '40'):
                        other = result['runs'][batch]['rows']
                        deltas = []
                        for a, b in zip(single, other, strict=True):
                            scores = {c['key']: c['cosine'] for c in a['top50']}
                            deltas.extend(abs(c['cosine'] - scores[c['key']]) for c in b['top50'] if c['key'] in scores)
                        result['stability'][batch] = dict(
                            top1_changes=sum(a['top50'][0]['key'] != b['top50'][0]['key'] for a, b in zip(single, other)),
                            target_rank_changes=sum(a['rank'] != b['rank'] for a, b in zip(single, other)),
                            max_common_top50_score_delta=max(deltas))
                    result['latency'] = {}
                    for batch, repeats in ((1, 40), (40, 10)):
                        payload = dict(texts=[q['query'] for q in single[:batch]], top_k=20, language='auto')
                        client.post('/v1/search', json=payload).raise_for_status()
                        durations = []
                        cpu0 = server.cpu_times()
                        start = time.perf_counter()
                        for _ in range(repeats):
                            t = time.perf_counter()
                            client.post('/v1/search', json=payload).raise_for_status()
                            durations.append((time.perf_counter() - t) * 1000)
                        elapsed = time.perf_counter() - start
                        cpu1 = server.cpu_times()
                        result['latency'][str(batch)] = dict(repeats=repeats, p50_ms=float(np.percentile(durations, 50)),
                            p95_ms=float(np.percentile(durations, 95)), mean_ms=float(np.mean(durations)),
                            average_cpu_cores=(cpu1.user + cpu1.system - cpu0.user - cpu0.system) / elapsed)
                    result['warm_rss_mib'] = server.memory_info().rss / 2**20
                    result['sampled_peak_rss_mib'] = max(rss) / 2**20
                    result['memory_note'] = '50ms sampling after readiness; excludes startup peak.'
                    (output / f'{precision}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(precision, result['latency'], result['warm_rss_mib'], flush=True)
                    return result
            finally:
                done.set()
                if sampler:
                    sampler.join()
                stop.touch()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        child.kill()
                    process.kill()
                    process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--precision', choices=('int8', 'fp16'), required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmark-results/precision')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run(args.precision, args.output)
    write_report(args.output)


def write_report(output):
    if all((output / f'{p}.json').exists() for p in ('int8', 'fp16')):
        results = {p: json.loads((output / f'{p}.json').read_text(encoding='utf-8')) for p in ('int8', 'fp16')}
        lines = ['# E5 CPU precision comparison', '',
                 '279 bilingual fields, 160 previously seen synthetic development queries. Two CPU threads, one FIFO worker.',
                 'Each precision runs in its own process and fresh database; fields are upserted individually. No model training.',
                 'Recall is diagnostic, not held-out or production accuracy. FP16 denotes artifact precision, not necessarily CPU arithmetic.', '',
                 '| Precision | Query batch | R@1 | R@10 | R@15 | R@20 | R@50 | MRR |',
                 '|---|---:|---:|---:|---:|---:|---:|---:|']
        for precision, result in results.items():
            for batch, run_result in result['runs'].items():
                m = run_result['overall']
                lines.append(f'| {precision} | {batch} | ' + ' | '.join(f'{m[f"recall@{k}"]:.3%}' for k in (1, 10, 15, 20, 50)) + f' | {m["mrr"]:.5f} |')
        lines += ['', '| Precision | Batch | HTTP P50 ms | HTTP P95 ms |', '|---|---:|---:|---:|']
        for precision, result in results.items():
            for batch, m in result['latency'].items():
                lines.append(f'| {precision} | {batch} | {m["p50_ms"]:.2f} | {m["p95_ms"]:.2f} |')
        for precision, result in results.items():
            lines += ['', f'{precision}: warm RSS {result["warm_rss_mib"]:.2f} MiB; sampled peak {result["sampled_peak_rss_mib"]:.2f} MiB (50ms sampling after readiness, excludes startup).',
                      f'Batch sensitivity versus single: `{json.dumps(result["stability"])}`', '']
        old, new = (results[p]['runs']['1']['rows'] for p in ('int8', 'fp16'))
        changes = [dict(query=a['query'], key=a['key'], int8_rank=a['rank'], fp16_rank=b['rank'])
                   for a, b in zip(old, new, strict=True) if (a['rank'] == 1) != (b['rank'] == 1)]
        (output / 'top1_changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding='utf-8')
        lines += [f'Single-query Top1: {sum(c["fp16_rank"] == 1 for c in changes)} corrected, {sum(c["int8_rank"] == 1 for c in changes)} regressed.', '']
        (output / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
