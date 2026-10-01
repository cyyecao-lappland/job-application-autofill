"""Descriptive score/precision analysis, never an automatic-fill policy."""
import argparse
import json
import math
from pathlib import Path

THRESHOLDS = [0.85, 0.88, 0.89, 0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97]
RULES = [(0.90, 0.005), (0.90, 0.010), (0.90, 0.015), (0.90, 0.020),
         (0.90, 0.030), (0.92, 0.005), (0.92, 0.010), (0.92, 0.020),
         (0.94, 0.005), (0.94, 0.010)]
EDGES = [0.0, 0.85, 0.88, 0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 1.000001]


def wilson(correct, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = correct / n
    denominator = 1 + z*z / n
    center = (p + z*z/(2*n)) / denominator
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def stats(selected, total):
    n = len(selected)
    correct = sum(row["rank"] == 1 for row in selected)
    return {"accepted": n, "correct": correct, "errors": n - correct,
            "coverage": n / total if total else 0,
            "precision": correct / n if n else None,
            "nominal_wilson_95": wilson(correct, n)}


def analyze(rows):
    thresholds = []
    for score in THRESHOLDS:
        thresholds.append({"score_gte": score, **stats(
            [r for r in rows if r["top50"][0]["cosine"] >= score], len(rows))})
    rules = []
    for score, margin in RULES:
        rules.append({"score_gte": score, "margin_gte": margin, **stats([
            r for r in rows if r["top50"][0]["cosine"] >= score and r["top1_margin"] >= margin
        ], len(rows))})
    bins = [{"score_gte": lo, "score_lt": hi, **stats([
        r for r in rows if lo <= r["top50"][0]["cosine"] < hi
    ], len(rows))} for lo, hi in zip(EDGES, EDGES[1:])]
    errors = sorted([{
        "label": r["label"], "language": r["language"], "gold_key": r["key"],
        "predicted_key": r["top50"][0]["key"], "score": r["top50"][0]["cosine"],
        "margin": r["top1_margin"], "gold_rank": r["rank"],
    } for r in rows if r["rank"] != 1], key=lambda r: -r["score"])
    return {"n": len(rows), "thresholds": thresholds, "score_bins": bins, "joint_rules": rules, "highest_score_errors": errors[:10]}


def pct(value):
    return "N/A" if value is None else f"{value:.2%}"


def table_row(label, s):
    interval = s["nominal_wilson_95"]
    ci = "N/A" if interval is None else "–".join(pct(v) for v in interval)
    return f"| {label} | {s['accepted']} | {s['errors']} | {pct(s['coverage'])} | {pct(s['precision'])} | {ci} |"


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=root / "benchmark-results/bilingual-profile/results.json")
    parser.add_argument("--output", type=Path, default=root / "benchmark-results/bilingual-confidence")
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    analyses = {}
    for context in ("with_section", "label_only"):
        rows = source["runs"]["bilingual_routed__" + context]["rows"]
        for r in rows:
            assert (r["rank"] == 1) == (r["top50"][0]["key"] == r["key"])
            assert abs(r["top1_margin"] - (r["top50"][0]["cosine"] - r["top50"][1]["cosine"])) < 1e-7
        analyses[context] = {
            "all": analyze(rows),
            "zh": analyze([r for r in rows if r["language"] == "zh"]),
            "en": analyze([r for r in rows if r["language"] == "en"]),
        }
    report = {
        "source": str(args.input), "analyses": analyses,
        "score_definition": "top1 cosine, not probability; margin=top1 cosine-top2 cosine",
        "correctness": "Exact field-key Top1 equals the manually labeled target",
        "limitations": [
            "Same synthetic development set already used to design bilingual names; threshold exploration is in-sample.",
            "Chinese and English versions share 80 target keys, so 160 queries are not independent.",
            "Wilson intervals are nominal independent-Bernoulli reference intervals only, not valid production guarantees here.",
            "Coverage denominator is all queries of the indicated group; zero selected yields N/A precision, never 100%.",
            "No thresholds or auto-fill decisions are changed in the service.",
        ],
        "prospective_zero_error_samples_for_99pct_precision_at_95pct_one_sided": math.ceil(math.log(0.05) / math.log(0.99)),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 双语 E5 相似度阈值与误匹配分析", "",
             "使用固定 279 个候选、160 条中英查询的既有逐条结果，没有重新训练或修改查询。",
             "分数指 Top-1 cosine，不是概率。margin 为 Top-1 与 Top-2 cosine 的差。",
             "正确率=通过阈值的查询中 Top-1 key 完全正确的比例；覆盖率=通过阈值的查询数/该组查询总数。",
             "同一 key 的两种语言存在相关性，且本数据已用于开发，以下 Wilson 区间仅是名义独立样本参考，不是上线保证。",
             "无样本时正确率为 N/A；样本内零错误不代表真实错误率为零。所有阈值仅用于分析，未写入自动填写规则。", ""]
    header = ["| 规则/区间 | 通过数 | 错误数 | 覆盖率 | 观察正确率 | 名义95% Wilson区间 |",
              "|---|---:|---:|---:|---:|---|"]
    for context, groups in analyses.items():
        lines += [f"## {context}", ""]
        for lang, data in groups.items():
            lines += [f"### {lang}（N={data['n']}）", "", "累积阈值：", ""] + header
            lines += [table_row(f"score ≥ {s['score_gte']:.3f}", s) for s in data["thresholds"]]
            lines += ["", "分数区间：", ""] + header
            lines += [table_row(f"[{s['score_gte']:.2f}, {min(1,s['score_lt']):.2f})", s)
                      for s in data["score_bins"] if s["accepted"]]
            lines += ["", "分数＋分差：", ""] + header
            lines += [table_row(f"score ≥ {s['score_gte']:.3f}, margin ≥ {s['margin_gte']:.3f}", s)
                      for s in data["joint_rules"]]
            lines += [""]
        lines += ["高分误匹配：", "", "| 标签 | 语言 | 正确 key | Top-1 key | score | margin |",
                  "|---|---|---|---|---:|---:|"]
        for r in groups["all"]["highest_score_errors"]:
            lines.append(f"| {r['label']} | {r['language']} | {r['gold_key']} | {r['predicted_key']} | {r['score']:.4f} | {r['margin']:.4f} |")
    lines += ["", "## 结论边界", "",
              "score ≥ 0.90 且 margin ≥ 0.015 可以作为后续独立验证的候选规则，但只能针对带模块上下文的配置。",
              "同一规则在裸标签上仍有误匹配，不能跨输入格式照搬。",
              "在预先固定规则、独立同分布样本的理想假设下，至少需 299 条通过规则且全部正确的验证样本，",
              "才可使零错误情形的单侧95%精确二项置信下界达到99%；本开发集不满足这些验证条件。"]
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for context in analyses:
        print(context, json.dumps({"thresholds": analyses[context]["all"]["thresholds"],
                                  "joint_rules": analyses[context]["all"]["joint_rules"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
