"""Evaluate synthetic form labels against actual profile KEY paths, never values."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.encoder import Encoder

SECTIONS = {
    "identity": ("基本信息", "Personal information"),
    "contact": ("联系方式", "Contact information"),
    "education": ("教育经历", "Education"),
    "employment": ("工作实习经历", "Employment history"),
    "projects": ("项目经历", "Projects"),
    "campus": ("校园经历", "Campus activities"),
    "awards": ("获奖情况", "Awards"),
    "languages": ("语言能力", "Language proficiency"),
    "family": ("家庭成员", "Family members"),
    "preferences": ("求职意向", "Job preferences"),
    "personal_answers": ("个人补充信息", "Personal information"),
    "research": ("科研成果", "Research publications"),
    "skills": ("专业技能", "Skills"),
    "competitions": ("竞赛经历", "Competitions"),
    "certifications": ("资格证书", "Certifications"),
    "training": ("培训经历", "Training"),
}
EXCLUDED = {
    "record_id", "source", "source_note", "date_source", "date_precision",
    "value_status", "boundaries", "directions", "related_record_ids",
    "education_record_id", "related_award_record_id", "texts",
    "date_description", "note", "answer_status", "validation_status",
    "autofill_policy", "presentation_policy", "proof", "legacy_dates",
    "historical_transcript", "transcript_usage_policy", "transfer_history",
    "training_mode_note", "excluded_elective_note", "user_provided_date",
    "user_provided_date_role", "inventor_order_note",
}
KS = [1, 3, 5, 10, 15, 20, 50]


def extract_keys(profile):
    paths = set()
    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in EXCLUDED:
                    walk(child, path + "/" + key)
        elif isinstance(value, list):
            records = [child for child in value if isinstance(child, dict)]
            if records:
                for record in records:
                    walk(record, path + "/*")
            elif path.count("/") > 0:
                paths.add(path)
        else:
            paths.add(path)
    for section in SECTIONS:
        if section in profile:
            walk(profile[section], section)
    return sorted(paths)


def readable(key):
    return " / ".join(piece.replace("_", " ") for piece in key.split("/") if piece != "*")


def summarize(rows):
    ranks = np.array([row["rank"] for row in rows])
    positive = np.array([row["gold_cosine"] for row in rows])
    return {
        "n": len(rows),
        **{f"recall@{k}": float(np.mean(ranks <= k)) for k in KS},
        "mrr": float(np.mean(1.0 / ranks)),
        "gold_cosine_mean": float(np.mean(positive)),
        "gold_cosine_p10": float(np.percentile(positive, 10)),
        "gold_cosine_p50": float(np.percentile(positive, 50)),
        "gold_cosine_p90": float(np.percentile(positive, 90)),
        "top1_cosine_mean": float(np.mean([row["top50"][0]["cosine"] for row in rows])),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/profile-synonyms"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
    keys = extract_keys(profile)
    del profile  # Only structural keys proceed to the model and output.
    dataset = root / "benchmarks/profile_synonyms.tsv"
    queries = []
    with dataset.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["key"] not in keys:
                raise ValueError("Gold key absent from candidate corpus: " + row["key"])
            for lang in ("zh", "en"):
                queries.append({"key": row["key"], "language": lang, "label": row[lang]})
    if len({q["key"] for q in queries}) * 2 != len(queries):
        raise ValueError("Expected exactly two languages per distinct key")
    args.output.mkdir(parents=True, exist_ok=True)
    # Freeze candidate schema and manually authored gold labels before inference.
    (args.output / "corpus.json").write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    encoder = Encoder(Settings.from_env())
    def encode(texts, role):
        return np.concatenate([encoder.encode(texts[i:i + 32], role) for i in range(0, len(texts), 32)])
    corpus_vectors = {
        "raw_path": encode(keys, "passage"),
        "readable_path": encode([readable(key) for key in keys], "passage"),
    }
    query_vectors = {}
    for context in ("label_only", "with_section"):
        texts = []
        for q in queries:
            if context == "label_only":
                texts.append(q["label"])
            else:
                section = SECTIONS[q["key"].split("/")[0]][0 if q["language"] == "zh" else 1]
                texts.append(f"section: {section}\nfield: {q['label']}")
        query_vectors[context] = (texts, encode(texts, "query"))
    runs = {}
    for representation, corpus in corpus_vectors.items():
        for context, (texts, vectors) in query_vectors.items():
            scores = np.clip(vectors @ corpus.T, -1, 1)
            rows = []
            for i, q in enumerate(queries):
                order = np.argsort(-scores[i], kind="stable")
                gold_index = keys.index(q["key"])
                rank = int(np.flatnonzero(order == gold_index)[0]) + 1
                rows.append({
                    **q, "query": texts[i], "rank": rank, "gold_cosine": float(scores[i, gold_index]),
                    "top1_margin": float(scores[i, order[0]] - scores[i, order[1]]),
                    "top50": [{"key": keys[j], "cosine": float(scores[i, j])} for j in order[:50]],
                })
            name = representation + "__" + context
            runs[name] = {
                "overall": summarize(rows),
                "zh": summarize([r for r in rows if r["language"] == "zh"]),
                "en": summarize([r for r in rows if r["language"] == "en"]),
                "rows": rows,
            }
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": "multilingual-e5-small-int8", "dimension": 384,
        "candidate_count": len(keys), "target_key_count": len(queries) // 2,
        "query_count_per_run": len(queries), "k": KS,
        "sections": list(SECTIONS), "excluded_field_names": sorted(EXCLUDED),
        "scope": "Actual profile key schema; record arrays collapsed to *; no values, exact key target, no record selection.",
        "limitations": "Manually authored synthetic gold labels, not independent real-form test data. Label-only cases can be ambiguous. Keys remain English; zh means Chinese query -> English key. No fitting or threshold tuning.",
        "runs": runs,
    }
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "per-query.tsv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["run", "language", "query", "gold_key", "rank", "gold_cosine", "predicted_key", "top1_cosine", "top1_margin"])
        for name, run in runs.items():
            for row in run["rows"]:
                writer.writerow([name, row["language"], row["query"], row["key"], row["rank"],
                                 row["gold_cosine"], row["top50"][0]["key"], row["top50"][0]["cosine"], row["top1_margin"]])
    lines = [
        "# 信息表 key 同义字段检索评估", "",
        f"候选业务 key：{len(keys)}；目标 key：{len(queries)//2}；每种配置 {len(queries)} 条查询（各半中英文）。",
        "模型：multilingual-e5-small INT8，CPU，query/passage 前缀，384 维归一化向量。", "",
        "仅编码 key，不编码信息表的真实答案。未修改字段库、模型或信息表。",
        "数组记录折叠为 *，检验字段语义，不检验具体哪一条教育/工作记录。",
        "候选包含所有选定业务模块的字段及干扰项，不仅包含目标 key。",
        "排除审计、来源、历史和重复长短文本等元数据；具体范围和排除名单在 results.json。",
        f"{len(queries)//2} 个目标的同义表达由人工编写；这是合成诊断集，不是独立真实网申测试集。", "",
        "raw_path：原始完整 key 路径；readable_path：去除 *、下划线改为空格，保留所有层级。",
        "label_only：仅标签；with_section：附加真实表单可能提供的模块名，不给具体目标 key。",
        "按唯一目标路径严格计分；Recall@K 为目标出现在前 K 的查询比例。",
        "裸标签可能天然歧义，例如家庭成员/本人的联系电话；相似度不是正确概率。",
        "zh 是中文 query → 英文 key；en 是英文 query → 英文 key。没有测试中文候选描述。", "",
        "| 配置 | 语言 | N | " + " | ".join(f"R@{k}" for k in KS) + " | MRR | 正例平均 cosine |",
        "|---|---|---:" + "|---:" * (len(KS) + 2) + "|",
    ]
    for name, run in runs.items():
        for group in ("overall", "zh", "en"):
            m = run[group]
            lines.append(f"| {name} | {group} | {m['n']} | " + " | ".join(f"{m[f'recall@{k}']:.2%}" for k in KS) + f" | {m['mrr']:.4f} | {m['gold_cosine_mean']:.4f} |")
    lines += ["", "## 易混淆案例：readable_path + with_section", "",
              "| 查询 | 正确 key | 排名 | 正例 cosine | Top-1 key | Top-1 cosine |",
              "|---|---|---:|---:|---|---:|"]
    hardest = sorted(runs["readable_path__with_section"]["rows"], key=lambda r: -r["rank"])[:20]
    for r in hardest:
        lines.append(f"| {r['query'].replace(chr(10), ' / ')} | {r['key']} | {r['rank']} | {r['gold_cosine']:.4f} | {r['top50'][0]['key']} | {r['top50'][0]['cosine']:.4f} |")
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2))
    print(json.dumps({name: {g: run[g] for g in ("overall", "zh", "en")} for name, run in runs.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
