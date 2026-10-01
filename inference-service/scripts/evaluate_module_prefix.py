"""Ablate outer module text only; retain E5 query:/passage: prefixes."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from scripts.evaluate_bilingual_keys import evaluate
from scripts.evaluate_profile_keys import KS


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=root / "benchmark-results/bilingual-profile")
    parser.add_argument("--output", type=Path, default=root / "benchmark-results/module-prefix-ablation")
    args = parser.parse_args()
    library = json.loads((args.source / "library.json").read_text(encoding="utf-8"))
    queries = json.loads((args.source / "queries.json").read_text(encoding="utf-8"))
    previous = json.loads((args.source / "results.json").read_text(encoding="utf-8"))
    id_to_key = {f["field_id"]: f["source_key"] for f in library}
    stripped = []
    for f in library:
        new = dict(f)
        for lang in ("zh", "en"):
            outer, sep, rest = new["key_" + lang].partition(" / ")
            if not sep or not rest:
                raise ValueError("Missing outer section: " + f["field_id"])
            new["key_" + lang] = rest
        stripped.append(new)
    duplicates = {}
    for lang in ("zh", "en"):
        groups = defaultdict(list)
        for f in stripped:
            groups[f["key_" + lang]].append(f["source_key"])
        duplicates[lang] = {text: keys for text, keys in groups.items() if len(keys) > 1}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "library-without-section.json").write_text(
        json.dumps(stripped, ensure_ascii=False, indent=2), encoding="utf-8")
    settings = Settings.from_env()
    runs = {}
    with tempfile.TemporaryDirectory(prefix="e5-prefix-eval-") as tmp:
        cfg = Settings(settings.model_dir, Path(tmp), model_version=settings.model_version,
                       intra_op_threads=settings.intra_op_threads)
        with TestClient(create_app(cfg)) as client:
            for doc_mode, definitions in (("doc_section", library), ("doc_no_section", stripped)):
                for f in definitions:
                    response = client.post("/v1/library/upsert", json=f)
                    response.raise_for_status()
                for query_mode in ("with_section", "label_only", "field_only"):
                    name = doc_mode + "__" + query_mode
                    runs[name] = evaluate(client, queries, id_to_key, "bilingual_routed", query_mode)
                    print(name, runs[name]["overall"], flush=True)
    # The unchanged cells must reproduce the previous run exactly.
    for context in ("with_section", "label_only"):
        assert runs["doc_section__" + context] == previous["runs"]["bilingual_routed__" + context]
    reference = runs["doc_section__with_section"]["rows"]
    paired = {}
    for name, run in runs.items():
        paired[name] = {f"recall@{k}": {
            "recovered": sum(a["rank"] > k and b["rank"] <= k for a, b in zip(reference, run["rows"], strict=True)),
            "regressed": sum(a["rank"] <= k and b["rank"] > k for a, b in zip(reference, run["rows"], strict=True)),
        } for k in KS}
        for k in KS:
            count = sum(any(c["key"] == r["key"] for c in r["top50"][:k]) for r in run["rows"])
            assert abs(count / len(queries) - run["overall"][f"recall@{k}"]) < 1e-12
    cases = []
    example_keys = {
        "preferences/target_positions", "preferences/accept_shift_work", "preferences/preferred_cities",
        "contact/mobile", "education/*/advisor", "campus/*/role",
    }
    for index, q in enumerate(queries):
        if q["language"] != "zh" or q["key"] not in example_keys:
            continue
        cases.append({**q, "conditions": {name: {
            "rank": run["rows"][index]["rank"], "gold_cosine": run["rows"][index]["gold_cosine"],
            "top1": run["rows"][index]["top50"][0], "margin": run["rows"][index]["top1_margin"],
        } for name, run in runs.items()}})
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidates": len(library), "queries_per_condition": len(queries),
        "runs": runs, "paired_vs_original": paired, "duplicate_docs_after_removal": duplicates,
        "examples": cases,
        "controls": "Same model, library snapshot, queries, candidate pool, language routing, batch=32. E5 query/passsage prefixes kept. Only outer doc module removed; inner paths retained. Field-only controls isolate the query section line from the field: label.",
        "limitations": "Synthetic development diagnostic, not held-out or production accuracy. Repeated bilingual keys not independent. No library/policy deployment changes.",
    }
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 模块前缀消融实验", "",
             "固定原 279 个候选、160 条查询。保持 E5 query:/passage: 前缀、语言路由和模型不变。",
             "doc 去前缀仅删除首个模块名与分隔符，保留内层含义。例如：联系方式 / 当前户口登记地 / 城市 → 当前户口登记地 / 城市。",
             "query 三种格式：带 section 与 field；裸标签；仅 field 行。第三种用于区分模块名和格式标记的影响。",
             "两组未改动条件已与上一轮逐项结果完全一致。所有 R@K 均从保存的候选列表独立复核。",
             "只修改临时测试库，没有修改默认字段库或上线策略。此为合成开发集。", "",
             "| doc / query 配置 | 语言 | " + " | ".join(f"R@{k}" for k in KS) + " | MRR |",
             "|---|---|" + "---:|" * (len(KS) + 1)]
    for name, run in runs.items():
        for lang in ("overall", "zh", "en"):
            m = run[lang]
            lines.append(f"| {name} | {lang} | " + " | ".join(f"{m[f'recall@{k}']:.2%}" for k in KS) + f" | {m['mrr']:.4f} |")
    lines += ["", "## 代表性混淆查询的正确字段排名", "",
              "| query | " + " | ".join(runs) + " |", "|---|" + "---:|" * len(runs)]
    for case in cases:
        lines.append("| " + case["label"] + " | " + " | ".join(str(case["conditions"][name]["rank"]) for name in runs) + " |")
    lines += ["", "## 移除 doc 模块后出现的完全同名字段", ""]
    for lang, groups in duplicates.items():
        lines += [f"### {lang}（{len(groups)} 组）", ""]
        for text, keys in groups.items():
            lines.append(f"- {text}：{', '.join(keys)}")
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"examples": cases, "duplicate_groups": {k: len(v) for k, v in duplicates.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
