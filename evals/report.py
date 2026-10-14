"""Turning trial records into numbers: pass rate, pass@k, pass^k, tokens, time."""

from math import comb
from statistics import median


def pass_at_k(n: int, c: int, k: int) -> float:
    """Chance that at least one of k trials passes, given c of n passed (unbiased estimator)."""
    if k > n:
        raise ValueError("k cannot exceed the number of trials")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_pow_k(n: int, c: int, k: int) -> float:
    """Chance that all k trials pass, given c of n passed: how reliable the agent is."""
    if k > n:
        raise ValueError("k cannot exceed the number of trials")
    return comb(c, k) / comb(n, k)


def summarize_task(records: list[dict]) -> dict:
    valid = [r for r in records if r["verdict"] != "invalid"]
    n = len(valid)
    c = sum(r["verdict"] == "pass" for r in valid)

    graders = {}
    for r in valid:
        for g in r["graders"]:
            stats = graders.setdefault(g["id"], {"passed": 0, "gating": g["gating"]})
            stats["passed"] += g["passed"]

    def med(values):
        return median(values) if values else 0

    return {
        "task": records[0]["task"],
        "trials": n,
        "passed": c,
        "invalid": len(records) - n,
        "pass_rate": c / n if n else None,
        "pass_at_k": pass_at_k(n, c, n) if n else None,
        "pass_pow_k": pass_pow_k(n, c, n) if n else None,
        "median_seconds": med([r["seconds"] for r in valid]),
        "median_input_tokens": med([r["tokens"]["input"] for r in valid]),
        "median_output_tokens": med([r["tokens"]["output"] for r in valid]),
        "median_calls": med([r["tokens"]["calls"] for r in valid]),
        "graders": graders,
    }


def _pct(x) -> str:
    return "-" if x is None else f"{x:.0%}"


def render_summary(meta: dict, records: list[dict]) -> str:
    by_task: dict[str, list] = {}
    for r in records:
        by_task.setdefault(r["task"], []).append(r)
    summaries = [summarize_task(rs) for rs in by_task.values()]

    total_n = sum(s["trials"] for s in summaries)
    total_c = sum(s["passed"] for s in summaries)
    tokens_in = sum(r["tokens"]["input"] for r in records)
    tokens_out = sum(r["tokens"]["output"] for r in records)

    harness = meta.get("harness", {})
    lines = [
        f"# Eval run {meta['run_id']}",
        "",
        f"- harnest: `{harness.get('git_sha', '?')[:10]}`"
        f"{' (uncommitted changes)' if harness.get('dirty') else ''}"
        f" · files hash `{harness.get('files_hash', '?')[:10]}`",
        f"- model: {meta.get('model') or '?'} · trials per task: {meta.get('trials')}",
        f"- overall: **{total_c}/{total_n} trials passed**"
        f" · tokens {tokens_in:,} in / {tokens_out:,} out",
        "",
        "k = trials per task. pass@k: at least one trial passed. "
        "pass^k: every trial passed (reliability).",
        "",
        "| task | passed | pass rate | pass@k | pass^k | invalid | median time | "
        "median tokens in / out | median API calls |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for s in summaries:
        lines.append(
            f"| {s['task']} | {s['passed']}/{s['trials']} | {_pct(s['pass_rate'])} | "
            f"{_pct(s['pass_at_k'])} | {_pct(s['pass_pow_k'])} | {s['invalid']} | "
            f"{s['median_seconds']:.0f}s | {s['median_input_tokens']:,.0f} / "
            f"{s['median_output_tokens']:,.0f} | {s['median_calls']:.0f} |")

    lines += ["", "## Graders", "", "| task | grader | gating | passed |", "|---|---|---|---|"]
    for s in summaries:
        for gid, g in s["graders"].items():
            lines.append(f"| {s['task']} | {gid} | {'yes' if g['gating'] else 'no'} | "
                         f"{g['passed']}/{s['trials']} |")

    lines += ["", "## Trials", "",
              "| task | trial | verdict | status | time | tokens in / out | failed graders |",
              "|---|---|---|---|---|---|---|"]
    for r in records:
        failed = [g["id"] for g in r["graders"] if not g["passed"]]
        lines.append(f"| {r['task']} | {r['trial']} | {r['verdict'].upper()} | {r['status']} | "
                     f"{r['seconds']:.0f}s | {r['tokens']['input']:,} / {r['tokens']['output']:,} | "
                     f"{', '.join(failed) or '-'} |")

    return "\n".join(lines) + "\n"
