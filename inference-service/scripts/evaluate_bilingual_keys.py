"""Paired bilingual retrieval evaluation through the real ASGI service and E5."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from scripts.evaluate_profile_keys import KS, SECTIONS, readable, summarize


def evaluate(client, queries, id_to_key, mode, context, batch_size=32):
    texts = []
    for q in queries:
        if context == "label_only":
            texts.append(q["label"])
        elif context == "field_only":
            texts.append(f"field: {q['label']}")
        else:
            section = SECTIONS[q["key"].split("/")[0]][0 if q["language"] == "zh" else 1]
            texts.append(f"section: {section}\nfield: {q['label']}")
    rows = []
    for offset in range(0, len(texts), batch_size):
        response = client.post("/v1/similarity", json={
            "texts": texts[offset:offset + batch_size], "top_k": len(id_to_key),
            "language": "en" if mode == "english_names" else "auto",
        })
        response.raise_for_status()
        results = response.json()["results"]
        for i, result in enumerate(results):
            q = queries[offset + i]
            candidates = [{"key": id_to_key[c["field_id"]], "cosine": c["cosine"]} for c in result["candidates"]]
            rank = next(j + 1 for j, c in enumerate(candidates) if c["key"] == q["key"])
            rows.append({
                **q, "query": texts[offset + i], "route": result["language"], "rank": rank,
                "gold_cosine": candidates[rank - 1]["cosine"],
                "top1_margin": candidates[0]["cosine"] - candidates[1]["cosine"],
                "top50": candidates[:50],
            })
    return {"overall": summarize(rows),
            "zh": summarize([r for r in rows if r["language"] == "zh"]),
            "en": summarize([r for r in rows if r["language"] == "en"]), "rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/bilingual-profile"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    library = json.loads((root / "library/profile_fields.json").read_text(encoding="utf-8"))["fields"]
    id_to_key = {f["field_id"]: f["source_key"] for f in library}
    queries = []
    with (root / "benchmarks/profile_synonyms.tsv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            for lang in ("zh", "en"):
                queries.append({"key": row["key"], "language": lang, "label": row[lang]})
    assert len(id_to_key) == len(library)
    assert all(q["key"] in id_to_key.values() for q in queries)
    previous = root / "benchmark-results/profile-synonyms"
    if (previous / "corpus.json").exists():
        assert sorted(id_to_key.values()) == json.loads((previous / "corpus.json").read_text(encoding="utf-8"))
        assert queries == json.loads((previous / "queries.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    # Save the exact unmodified queries and all bilingual names before inference.
    (args.output / "queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "library.json").write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    settings = Settings.from_env()
    runs = {}
    with tempfile.TemporaryDirectory(prefix="e5-bilingual-eval-") as temp:
        test_settings = Settings(settings.model_dir, Path(temp), model_version=settings.model_version,
                                 intra_op_threads=settings.intra_op_threads)
        # Exactly one real Encoder / ORT session; every inference is queued.
        with TestClient(create_app(test_settings)) as client:
            for mode in ("readable_keys", "english_names", "bilingual_routed"):
                if mode != "bilingual_routed":
                    for f in library:
                        definition = ({"field_id": f["field_id"], "canonical_text": readable(f["source_key"]),
                                       "source_key": f["source_key"], "field_type": f["field_type"]}
                                      if mode == "readable_keys" else f)
                        response = client.post("/v1/library/upsert", json=definition)
                        response.raise_for_status()
                for context in ("label_only", "with_section"):
                    runs[mode + "__" + context] = evaluate(client, queries, id_to_key, mode, context)
                    print(mode, context, runs[mode + "__" + context]["overall"], flush=True)
            assert client.get("/health").json()["library_size"] == len(library)
    # Routing must leave English queries exactly unchanged versus English-only names.
    for context in ("label_only", "with_section"):
        en_control = [r for r in runs["english_names__" + context]["rows"] if r["language"] == "en"]
        en_routed = [r for r in runs["bilingual_routed__" + context]["rows"] if r["language"] == "en"]
        assert [r["top50"] for r in en_control] == [r["top50"] for r in en_routed]
    paired = {}
    for context in ("label_only", "with_section"):
        old = runs["readable_keys__" + context]["rows"]
        new = runs["bilingual_routed__" + context]["rows"]
        paired[context] = {
            f"recall@{k}": {
                "recovered": sum(a["rank"] > k and b["rank"] <= k for a, b in zip(old, new, strict=True)),
                "regressed": sum(a["rank"] <= k and b["rank"] > k for a, b in zip(old, new, strict=True)),
            } for k in KS
        }
    overlaps = {}
    by_key = {f["source_key"]: f for f in library}
    for lang in ("zh", "en"):
        exact = [q for q in queries if q["language"] == lang
                 and q["label"].strip().casefold() == by_key[q["key"]]["key_" + lang].split(" / ")[-1].strip().casefold()]
        overlaps[lang] = len(exact)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "model": "multilingual-e5-small-int8",
        "model_version": settings.model_version, "candidates": len(library), "queries_per_run": len(queries),
        "k": KS, "exact_label_name_overlaps": overlaps, "paired": paired, "runs": runs,
        "method": "Real E5 via FastAPI TestClient, one session/FIFO worker; per-field upsert, query batch=32; same 279 fields and 160 frozen labels. No alias lookup or query-based training.",
        "limitations": "Development diagnostic only. Prior test labels/results were known before authoring translations. Not an independent held-out dataset. No claim of production accuracy or statistical generalization. Array records collapsed to *.",
        "baseline_note": "Readable-key baseline rerun through identical service/upsert path. Original notebook-style baseline encoded corpus in batches of 32; INT8 batching can alter scores.",
    }
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 双语 key 与语言路由评估", "",
        f"同一 {len(library)} 个候选 key、{len(queries)} 条查询（中英文各半），模型权重不变。",
        "三组：可读英文 key 基线；改写英文名称（所有查询用英文库）；中英文名称按 query 语言路由。",
        "所有组均通过真实 E5 + FastAPI 应用 + FIFO 工作线程执行。每字段 upsert，查询 batch=32。",
        "旧报告采用候选 batch=32 编码，本报告按实际 upsert 路径重跑基线；INT8 数值和排名可能略变。",
        "未读取或修改信息表答案；单独维护 source_key → key_zh/key_en 的字段定义表。",
        "无别名捷径，无模型训练，无阈值调整。该查询集此前已用于诊断，翻译作者见过旧结果，因此属于开发集而非独立盲测。",
        f"标签与对应末级名称自然完全相同：中文 {overlaps['zh']} 条，英文 {overlaps['en']} 条；未把测试标签导入 aliases。",
        "", "| 配置 | 语言 | " + " | ".join(f"R@{k}" for k in KS) + " | MRR | 正例平均 cosine |",
        "|---|---|" + "---:|" * (len(KS) + 2),
    ]
    for name, run in runs.items():
        for lang in ("overall", "zh", "en"):
            m = run[lang]
            lines.append(f"| {name} | {lang} | " + " | ".join(f"{m[f'recall@{k}']:.2%}" for k in KS) + f" | {m['mrr']:.4f} | {m['gold_cosine_mean']:.4f} |")
    lines += ["", "## 同查询配对变化", "", "| 查询方式 | K | 新增命中 | 原命中变漏检 |", "|---|---:|---:|---:|"]
    for context, data in paired.items():
        for k, counts in data.items():
            lines.append(f"| {context} | {k} | {counts['recovered']} | {counts['regressed']} |")
    lines += ["", "## 双语路由仍失败的案例（裸标签）", "",
              "| 查询 | 正确 key | 排名 | 正例 cosine | Top-1 key |", "|---|---|---:|---:|---|"]
    for r in sorted(runs["bilingual_routed__label_only"]["rows"], key=lambda r: -r["rank"])[:20]:
        lines.append(f"| {r['label']} | {r['key']} | {r['rank']} | {r['gold_cosine']:.4f} | {r['top50'][0]['key']} |")
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"paired": paired, "exact_overlaps": overlaps}, ensure_ascii=False))


if __name__ == "__main__":
    main()
