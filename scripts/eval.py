#!/usr/bin/env python3
"""
eval.py — wiki quality eval (LLM-as-judge metrics).

Hardens the checks lint.py defers to prose ("contradiction scan and source drift
require LLM"). Metrics are scored 0..1 with rationales; a gating layer applies
thresholds and averages runs so quality regressions can be caught.

The judge is the host agent CLI, auto-detected on PATH (claude, codex) by
default, driven via subprocess and inheriting the user's existing login — no new
API key. A generated wiki can set OWNER_JUDGE to owner-lock evals to a specific
agent CLI. Falls back to deterministic-only + an emitted brief when no allowed
agent CLI is present.

Layers:
  deterministic  — structural validity, near-duplicate candidates (no judge)
  judge          — contradictions, grounding, redundancy, disambiguation
  gating         — thresholds + multi-run averaging + pass/fail + exit code

See docs/superpowers/specs/2026-06-17-eval-metrics-design.md.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

# Reuse lint's loaders rather than duplicating frontmatter/page parsing.
sys.path.insert(0, str(Path(__file__).parent))
import lint  # noqa: E402

WIKI_ROOT = Path(__file__).parent.parent

# A judge takes a prompt and returns model text (expected to contain JSON).
# Tests inject a stub so every metric runs without tokens/network.
Judge = Callable[[str], str]


@dataclasses.dataclass
class MetricResult:
    name: str
    score: float | None          # 0..1; None = not scored (legitimate skip OR judge error)
    passed: bool
    detail: str = ""             # rationale: WHY this score (judge metrics quote offending content)
    insights: str = ""           # HOW to improve
    error: bool = False          # True = a judge error (loud, fails the gate) vs a legitimate N/A skip
    extra: dict = dataclasses.field(default_factory=dict)


class JudgeError(Exception):
    """Raised when a judge that was expected to return a verdict fails (garbage,
    empty, or raised) after retries. Distinct from a legitimate N/A skip — this
    must be loud and fail the gate (fail-closed)."""


def parse_json(text: str):
    """Best-effort JSON extraction from a judge response (strips ```json fences)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    m = re.search(r"(\{.*\}|\[.*\])", t, re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except (ValueError, json.JSONDecodeError):
        return None


def parse_json_obj(text: str) -> dict:
    """Like parse_json but ALWAYS returns a dict, so every `.get(...)` call site is
    safe even when the judge emits an array or garbage."""
    res = parse_json(text)
    return res if isinstance(res, dict) else {}


# ── judge adapters ──────────────────────────────────────────────────────────────
# A judge is a SINGLE-SHOT completion, not an agentic session.
#   claude → Claude Agent SDK, tools disabled + single turn (a raw `claude -p`
#            session intermittently invoked tools and returned empty output).
#   codex  → `codex exec` (reliable). MUST redirect stdin from DEVNULL or it
#            blocks forever on an open stdin pipe.
# Both inherit the user's existing login — no new API key.

JUDGE_TIMEOUT = 180  # seconds per judge call
OWNER_JUDGE = None   # None = auto-detect; set to "claude" or "codex" to owner-lock.
OWNER_LABEL = "wiki"
CODEX_JUDGE_REASONING_ENV = "CODEX_JUDGE_REASONING_EFFORT"
_JUDGE_SYSTEM_PROMPT = ("You are a strict evaluation judge. Respond with ONLY the "
                        "requested JSON object and nothing else — no prose, no fences.")


def _trim_process_output(text: str, limit: int = 1200) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=JUDGE_TIMEOUT)
    if proc.returncode != 0:
        detail = (_trim_process_output(proc.stderr)
                  or _trim_process_output(proc.stdout)
                  or "no stderr/stdout")
        raise RuntimeError(f"judge command failed with exit {proc.returncode}: {detail}")
    return proc


def claude_judge(prompt: str) -> str:
    """Single-shot judge via the Claude Agent SDK: all tools disabled, one turn,
    keyless (inherits Claude Code auth). The SDK is imported lazily so eval.py runs
    without it (deterministic checks + codex judge don't need it). Any SDK failure
    (not installed, not logged in, network, empty) propagates — _judge_json turns
    it into a loud JudgeError (fail-closed)."""
    import asyncio
    from claude_agent_sdk import (query, ClaudeAgentOptions,
                                  AssistantMessage, TextBlock)

    async def _run_sdk() -> str:
        options = ClaudeAgentOptions(allowed_tools=[], max_turns=1,
                                     system_prompt=_JUDGE_SYSTEM_PROMPT)
        chunks = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "\n".join(chunks)

    return asyncio.run(_run_sdk())


def codex_judge(prompt: str) -> str:
    """codex exec writes its final message to --output-last-message; read it back."""
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
        out_path = f.name
    cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only"]
    reasoning_effort = os.environ.get(CODEX_JUDGE_REASONING_ENV)
    if reasoning_effort:
        cmd += ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
    cmd += ["--output-last-message", out_path, prompt]
    _run(cmd)
    try:
        return Path(out_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        try:
            Path(out_path).unlink()
        except OSError:
            pass


def make_custom_judge(cmd_str: str) -> Judge:
    """Escape hatch: any CLI that takes a prompt arg and prints text to stdout."""
    base = shlex.split(cmd_str)

    def judge(prompt: str) -> str:
        return _run(base + [prompt]).stdout
    return judge


_KNOWN_ADAPTERS = {"claude": claude_judge, "codex": codex_judge}


def detect_judge(spec: str | None) -> tuple[Judge | None, str]:
    """Resolve the judge from a --judge spec.

      None        → auto-detect a known agent CLI on PATH (claude, then codex)
      "none"      → deterministic-only (no judge)
      "claude"/"codex" → that adapter
      anything else    → a custom command (escape hatch)

    When OWNER_JUDGE is set to "claude" or "codex", auto-detect is restricted to
    that adapter; explicitly selecting a different adapter or custom command is
    rejected. "none" remains available for deterministic-only fallback.
    Returns (judge_or_None, label).
    """
    if spec == "none":
        return None, "none"
    owner = str(OWNER_JUDGE or "").strip().lower() or None
    if owner and owner not in _KNOWN_ADAPTERS:
        raise ValueError(f"{OWNER_LABEL} evals have invalid OWNER_JUDGE={OWNER_JUDGE!r}.")
    if owner:
        if spec in _KNOWN_ADAPTERS:
            if spec != owner:
                raise ValueError(f"{OWNER_LABEL} evals must use {owner}; got {spec}.")
            return _KNOWN_ADAPTERS[spec], spec
        if spec:
            raise ValueError(
                f"Custom judge commands are disabled for owner-locked evals; "
                f"use --judge {owner} or --judge none."
            )
        if shutil.which(owner):
            return _KNOWN_ADAPTERS[owner], owner
        return None, "none"
    if spec in _KNOWN_ADAPTERS:
        return _KNOWN_ADAPTERS[spec], spec
    if spec:
        return make_custom_judge(spec), spec
    for name in ("claude", "codex"):
        if shutil.which(name):
            return _KNOWN_ADAPTERS[name], name
    return None, "none"


# ── page loading (reuses lint) ──────────────────────────────────────────────────

def load_pages(wiki_root: Path) -> list[dict]:
    """Load wiki pages via lint's collector. Each page: {file, fm, text, sections}."""
    lint.WIKI_ROOT = Path(wiki_root)
    return lint.collect_pages()


# ── deterministic metrics (no judge) ────────────────────────────────────────────

# Per-page structural issues — about whether pages are well-formed, not whether the
# index/sources are in sync (that's lint's job, not a quality signal).
STRUCTURAL_CHECKS = {"frontmatter", "missing_section", "broken_body_link",
                     "okf_no_frontmatter", "okf_version_missing"}


def check_structural(pages: list[dict], wiki_root: Path) -> MetricResult:
    """Fraction of pages free of structural issues, reusing lint's checks.

    Passes (1.0) only when there are zero structural issues. Otherwise the score
    degrades with the issue count so a regression moves the number.
    """
    lint.WIKI_ROOT = Path(wiki_root)
    issues = lint.run_checks(pages, lint.collect_source_files(),
                             lint.parse_index_entries(), lint.collect_all_md_paths())
    issues += lint.check_okf_conformance()
    structural = [i for i in issues if i["check"] in STRUCTURAL_CHECKS]
    n = len(structural)
    n_pages = max(1, len(pages))
    if n == 0:
        return MetricResult("structural_validity", 1.0, True,
                            f"All {len(pages)} page(s) structurally well-formed.")
    score = round(max(0.0, 1.0 - n / (n + n_pages)), 3)
    sample = "; ".join(f"{i['file']}: {i['detail']}" for i in structural[:3])
    return MetricResult("structural_validity", score, False,
                        f"{n} structural issue(s) across {len(pages)} page(s) — e.g. {sample}",
                        extra={"n_issues": n})


def _title_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"\W+", str(s or "").lower()) if len(t) > 2}


def near_duplicate_candidates(pages: list[dict]) -> list[tuple[str, str, str]]:
    """Cheap deterministic pre-filter for the disambiguation metric: page pairs
    similar enough to be worth a judge's confirmation. Returns (file_a, file_b,
    reason). Avoids an O(n^2) judge sweep — only candidate pairs get judged.

    Signals: same non-empty `source:`; high title-token overlap; or same category
    with >=2 shared tags. Entity/concept and meta pages are excluded (different
    shape, expected to overlap).
    """
    cands = []
    primary = [p for p in pages
               if p["fm"].get("type") not in ("entity", "concept", "meta")
               and "meta" not in str(p["fm"].get("category", "")).lower()]
    for a, b in itertools.combinations(primary, 2):
        fa, fb = a["fm"], b["fm"]
        sa, sb = fa.get("source"), fb.get("source")
        if sa and sb and Path(str(sa)).name == Path(str(sb)).name:
            cands.append((a["file"], b["file"], "same source"))
            continue
        ta, tb = _title_tokens(fa.get("title")), _title_tokens(fb.get("title"))
        if ta and tb:
            jacc = len(ta & tb) / len(ta | tb)
            if jacc >= 0.5:
                cands.append((a["file"], b["file"], f"title overlap ({jacc:.0%})"))
                continue
        tags_a, tags_b = set(fa.get("tags") or []), set(fb.get("tags") or [])
        if (fa.get("category") == fb.get("category")
                and len(tags_a & tags_b) >= 2):
            cands.append((a["file"], b["file"], "same category + shared tags"))
    return cands


# ── judge metrics ───────────────────────────────────────────────────────────────

import concurrent.futures  # noqa: E402

_JUDGE_WORKERS = 3
_JUDGE_RETRY_BACKOFF = 1.0  # seconds, scaled per attempt (0 in tests)


def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def _body(text: str) -> str:
    """Markdown body with the YAML frontmatter stripped — judges must score prose,
    not treat frontmatter keys (status, owner, …) as factual claims."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:] if nl != -1 else ""
    return text


def _judge_json(judge: Judge, prompt: str, retries: int = 2, label: str = "",
                require: str = "score") -> dict:
    """Call the judge and parse a JSON object containing `require`, retrying with
    backoff. A judge that raises, returns empty/garbage, or omits the required key
    on every attempt is a judge ERROR (not a skip): raise JudgeError loudly so the
    caller fails the gate (fail-closed). Never silently degrades to a pass."""
    reason = "no response"
    for attempt in range(retries + 1):
        if label:
            sys.stderr.write(f"[eval] judge start: {label} "
                             f"(attempt {attempt + 1}/{retries + 1})\n")
            sys.stderr.flush()
        try:
            res = parse_json_obj(judge(prompt))
        except Exception as e:  # SDK/CLI failure (auth, network, not-installed, …)
            reason = f"{type(e).__name__}: {e}"
            res = {}
        else:
            if res and require in res:
                if label:
                    sys.stderr.write(f"[eval] judge ok: {label}\n")
                    sys.stderr.flush()
                return res
            reason = "unparseable or missing required key" if not res or require not in res else reason
        if attempt < retries and _JUDGE_RETRY_BACKOFF:
            time.sleep(_JUDGE_RETRY_BACKOFF * (attempt + 1))
    msg = (f"judge failed after {retries + 1} attempts"
           + (f" for {label}" if label else "") + f" — {reason}")
    sys.stderr.write(f"[eval] ERROR: {msg}\n")
    raise JudgeError(msg)


def _judge_error_result(name: str, e: Exception) -> MetricResult:
    """A loud, gate-failing result for a judge error (vs a quiet N/A skip)."""
    return MetricResult(name, None, False, f"JUDGE ERROR — {e}", error=True)


def _read_source(wiki_root: Path, src: str) -> str:
    f = Path(wiki_root) / src
    if not f.exists():
        f = Path(wiki_root) / "sources" / Path(src).name
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def check_grounding(pages: list[dict], wiki_root: Path, judge: Judge) -> MetricResult:
    """Every factual claim in a *derived* page is supported by its source file.
    Self-skips (None) when no page declares a source. One judge call per derived
    page, fanned out in parallel."""
    derived = [p for p in pages if p["fm"].get("source")]
    if not derived:
        return MetricResult("grounding", None, True,
                            "no derived pages with a source to ground against")

    def _one(p):
        src_text = _read_source(wiki_root, str(p["fm"]["source"]))
        if not src_text.strip():
            return None
        prompt = (
            "You are a strict groundedness judge. Decide whether the factual claims "
            "in the PAGE are supported by the SOURCE. Be lenient on paraphrase; "
            "schema/structural facts present in the source count as supported.\n\n"
            f"SOURCE:\n{src_text[:48000]}\n\nPAGE:\n{_body(p['text'])[:48000]}\n\n"
            'Return STRICT JSON: {"score":<0..1 fraction of claims supported>,'
            '"unsupported":[<quoted unsupported claims>],"rationale":"<one sentence>"}')
        res = _judge_json(judge, prompt, label=f"grounding {p['file']}")
        return (p["file"], float(res.get("score") or 0.0),
                res.get("unsupported") or [], res.get("rationale", ""))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_JUDGE_WORKERS) as ex:
            results = [r for r in ex.map(_one, derived) if r is not None]
    except JudgeError as e:
        return _judge_error_result("grounding", e)
    if not results:
        return MetricResult("grounding", None, True,
                            "no gradeable derived pages (sources empty/unreadable)")
    score = round(sum(r[1] for r in results) / len(results), 3)
    unsupported = [f"{f}: {_clip(c, 160)}" for f, _, us, _ in results for c in us]
    detail = (f"All claims across {len(results)} derived page(s) grounded."
              if not unsupported else
              f"{len(unsupported)} unsupported claim(s) — e.g. " + "; ".join(unsupported[:3]))
    return MetricResult("grounding", score,
                        score >= DEFAULT_THRESHOLDS["grounding"], _clip(detail, 1500),
                        extra={"unsupported": unsupported[:20]})


def _content_blob(pages: list[dict], limit: int = 60000) -> str:
    return "\n\n".join(f"### {p['file']}\n{_body(p['text'])}" for p in pages)[:limit]


def check_contradictions(pages: list[dict], judge: Judge) -> MetricResult:
    """Conflicting claims about the same tool/service/credential/behaviour across
    pages. Unparseable judge output self-skips (None), never a silent 0."""
    if not pages:
        return MetricResult("absence_of_contradictions", None, True, "no pages to compare")
    prompt = (
        "You are a rigorous consistency auditor. Find CONTRADICTIONS within or "
        "across the wiki pages below — conflicting claims about the same tool, "
        "service, credential, join key, enum, metric definition, or behaviour. "
        "BE SPECIFIC: name both conflicting statements and where each appears. "
        "Generic claims like 'has some contradictions' are not acceptable.\n\n"
        f"PAGES:\n{_content_blob(pages)}\n\n"
        'Return STRICT JSON: {"score":<0..1, 1=no contradictions 0=explicit conflict>,'
        '"conflicts":[<each conflict described specifically>],"rationale":"<one sentence>"}')
    try:
        res = _judge_json(judge, prompt, label="absence_of_contradictions")
    except JudgeError as e:
        return _judge_error_result("absence_of_contradictions", e)
    score = float(res.get("score") or 0.0)
    conflicts = res.get("conflicts") or []
    detail = ("No contradictions found." if not conflicts
              else f"{len(conflicts)} conflict(s): " + "; ".join(_clip(c, 200) for c in conflicts[:3]))
    return MetricResult("absence_of_contradictions", score,
                        score >= DEFAULT_THRESHOLDS["absence_of_contradictions"],
                        _clip(detail, 1500), extra={"conflicts": conflicts[:20]})


def check_redundancy(pages: list[dict], judge: Judge) -> MetricResult:
    """Do pages add synthesis beyond restating their source/template, or is it
    boilerplate? 1=rich, 0=tautological."""
    if not pages:
        return MetricResult("redundancy_index", None, True, "no pages to assess")
    prompt = (
        "You are a documentation quality auditor. Rate how much NOVEL synthesis the "
        "wiki pages add beyond restating their template/source/schema. 1.0=rich "
        "synthesis, 0.0=boilerplate restatement. BE SPECIFIC: name pages/lines that "
        "merely restate structure.\n\n"
        f"PAGES:\n{_content_blob(pages)}\n\n"
        'Return STRICT JSON: {"score":<0..1>,"boilerplate":[<offending pages/lines>],'
        '"rationale":"<one sentence>","insights":"<how to improve>"}')
    try:
        res = _judge_json(judge, prompt, label="redundancy_index")
    except JudgeError as e:
        return _judge_error_result("redundancy_index", e)
    score = float(res.get("score") or 0.0)
    return MetricResult("redundancy_index", score,
                        score >= DEFAULT_THRESHOLDS["redundancy_index"],
                        _clip(res.get("rationale", "") or "scored", 1200),
                        insights=res.get("insights", "") or "")


def check_disambiguation(pages: list[dict], judge: Judge) -> MetricResult:
    """For each near-duplicate candidate pair, the judge confirms whether the pages
    are genuinely distinct or should merge. No candidates → nothing ambiguous (1.0)."""
    pairs = near_duplicate_candidates(pages)
    if not pairs:
        return MetricResult("disambiguation", 1.0, True, "no near-duplicate candidates")
    by_file = {p["file"]: p for p in pages}

    def _one(pair):
        fa, fb, reason = pair
        a, b = by_file.get(fa), by_file.get(fb)
        if not a or not b:
            return None
        prompt = (
            "Two wiki pages look similar (" + reason + "). Decide whether they are "
            "GENUINELY DISTINCT concepts (keep both) or near-duplicates that should "
            "merge. 1.0=clearly distinct, 0.0=should merge.\n\n"
            f"PAGE A ({fa}):\n{_body(a['text'])[:24000]}\n\nPAGE B ({fb}):\n{_body(b['text'])[:24000]}\n\n"
            'Return STRICT JSON: {"score":<0..1>,"distinct":<bool>,"rationale":"<one sentence>"}')
        res = _judge_json(judge, prompt, label=f"disambiguation {fa}~{fb}")
        return (fa, fb, float(res.get("score") or 0.0), res.get("rationale", ""))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_JUDGE_WORKERS) as ex:
            judged = [r for r in ex.map(_one, pairs) if r is not None]
    except JudgeError as e:
        return _judge_error_result("disambiguation", e)
    if not judged:
        return MetricResult("disambiguation", None, True,
                            f"{len(pairs)} candidate pair(s) had no comparable content")
    score = round(sum(r[2] for r in judged) / len(judged), 3)
    dupes = [f"{fa} ~ {fb}: {_clip(rat, 160)}" for fa, fb, sc, rat in judged if sc < 1.0]
    detail = (f"All {len(judged)} candidate pair(s) judged distinct."
              if not dupes else f"{len(dupes)} near-duplicate(s): " + "; ".join(dupes[:3]))
    return MetricResult("disambiguation", score,
                        score >= DEFAULT_THRESHOLDS["disambiguation"], _clip(detail, 1500),
                        extra={"near_duplicates": dupes[:20]})


# ── gating layer (judge-agnostic, pure Python) ──────────────────────────────────

DEFAULT_THRESHOLDS = {
    "structural_validity": 1.0,
    "absence_of_contradictions": 1.0,   # any contradiction fails
    "grounding": 0.95,
    "redundancy_index": 0.6,
    "disambiguation": 1.0,              # any unmerged near-dup fails
}


def load_thresholds(wiki_root: Path) -> dict:
    """Defaults, overlaid by .eval/thresholds.json if present."""
    th = dict(DEFAULT_THRESHOLDS)
    f = Path(wiki_root) / ".eval" / "thresholds.json"
    if f.exists():
        try:
            th.update(json.loads(f.read_text(encoding="utf-8")) or {})
        except (ValueError, json.JSONDecodeError):
            pass
    return th


def aggregate(runs: list[list[MetricResult]], thresholds: dict) -> list[dict]:
    """Collapse N runs into one row per metric: mean score (None scores skipped),
    runs_passed/n, and a gate verdict. A metric that self-skipped on every run
    (mean None) does NOT fail the gate.
    """
    names = []
    for run in runs:
        for r in run:
            if r.name not in names:
                names.append(r.name)
    rows = []
    for name in names:
        results = [r for run in runs for r in run if r.name == name]
        scores = [r.score for r in results if r.score is not None]
        mean = round(sum(scores) / len(scores), 4) if scores else None
        threshold = thresholds.get(name)
        errored = any(r.error for r in results)
        if errored:
            passed = False           # judge error → fail-closed (loud), never a silent pass
        elif mean is None or threshold is None:
            passed = True            # legitimate N/A skip → not a failure
        else:
            passed = mean >= threshold
        rows.append({
            "name": name,
            "score": mean,
            "threshold": threshold,
            "passed": passed,
            "error": errored,
            "runs": len(results),
            "runs_passed": sum(1 for r in results if r.passed),
            "rationale": next((r.detail for r in results if r.detail), ""),
            "insights": next((r.insights for r in results if r.insights), ""),
        })
    return rows


def overall_passed(aggregated: list[dict]) -> bool:
    return all(row["passed"] for row in aggregated)


# ── orchestration ───────────────────────────────────────────────────────────────

import argparse  # noqa: E402
import datetime  # noqa: E402

JUDGE_METRIC_NAMES = ["grounding", "absence_of_contradictions",
                      "redundancy_index", "disambiguation"]


def run_once(wiki_root: Path, judge: Judge | None) -> list[MetricResult]:
    """One eval pass: deterministic metrics always; judge metrics if a judge is
    available, else skipped (None) placeholders so the report is complete."""
    pages = load_pages(wiki_root)
    results = [check_structural(pages, wiki_root)]
    if judge is None:
        results += [MetricResult(n, None, True,
                                 "no judge available — not scored (use an agent CLI on PATH or --judge)")
                    for n in JUDGE_METRIC_NAMES]
        return results
    results.append(check_grounding(pages, wiki_root, judge))
    results.append(check_contradictions(pages, judge))
    results.append(check_redundancy(pages, judge))
    results.append(check_disambiguation(pages, judge))
    return results


def _avg(aggregated: list[dict]) -> float | None:
    scores = [r["score"] for r in aggregated if r["score"] is not None]
    return round(sum(scores) / len(scores), 4) if scores else None


def write_run_record(wiki_root: Path, aggregated: list[dict], judge_label: str) -> Path:
    eval_dir = Path(wiki_root) / ".eval"
    (eval_dir / "runs").mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    record = {"timestamp": ts, "judge": judge_label,
              "overall_passed": overall_passed(aggregated),
              "average": _avg(aggregated), "metrics": aggregated}
    path = eval_dir / "runs" / f"{ts}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    hist = eval_dir / "history.json"
    entries = []
    if hist.exists():
        try:
            entries = json.loads(hist.read_text(encoding="utf-8")) or []
        except (ValueError, json.JSONDecodeError):
            entries = []
    entries.append({"timestamp": ts, "judge": judge_label,
                    "overall_passed": record["overall_passed"], "average": record["average"]})
    hist.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def fmt_score(s) -> str:
    return "n/a" if s is None else f"{float(s) * 100:.0f}"


def build_report(aggregated: list[dict], judge_label: str) -> str:
    lines = [f"# Wiki Quality Eval\n", f"Judge: `{judge_label}`\n"]
    for row in aggregated:
        verdict = "ERROR" if row.get("error") else ("PASS" if row["passed"] else "FAIL")
        th = row.get("threshold")
        thtxt = "" if th is None else f", threshold {fmt_score(th)}"
        runs = row.get("runs", 1)
        runtxt = f" [{row.get('runs_passed', 0)}/{runs} runs]" if runs > 1 else ""
        lines.append(f"## {row['name']} — {verdict} ({fmt_score(row['score'])}/100{thtxt}){runtxt}")
        if row.get("rationale"):
            lines.append(f"\n{row['rationale']}")
        if row.get("insights"):
            lines.append(f"\n*Improve:* {row['insights']}")
        lines.append("")
    lines.append(f"---\n**Overall: {'PASS' if overall_passed(aggregated) else 'FAIL'}**")
    return "\n".join(lines)


def gate_exit_code(aggregated: list[dict]) -> int:
    return 0 if overall_passed(aggregated) else 1


def emit_brief(wiki_root: Path) -> Path:
    """Fallback when no judge is available: write the evidence a human/agent needs
    to judge manually (derived-page→source pairs, near-duplicate candidates)."""
    pages = load_pages(wiki_root)
    eval_dir = Path(wiki_root) / ".eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    derived = [(p["file"], p["fm"]["source"]) for p in pages if p["fm"].get("source")]
    cands = near_duplicate_candidates(pages)
    lines = ["# Eval judging brief\n",
             "No agent CLI judge was available. Install/point one with `--judge`, "
             "or judge the items below manually.\n",
             "## Grounding — check each page's claims against its source"]
    lines += [f"- `{f}` ← `{s}`" for f, s in derived] or ["- (no derived pages)"]
    lines += ["\n## Contradictions / redundancy — review all pages together",
              "\n## Disambiguation — confirm these near-duplicate candidates are distinct"]
    lines += [f"- `{a}` ~ `{b}` ({why})" for a, b, why in cands] or ["- (no candidates)"]
    path = eval_dir / "brief.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_verdicts(run_dir: Path) -> list[MetricResult]:
    """Read externally-produced verdicts (verdicts.json: list of metric dicts)."""
    f = Path(run_dir) / "verdicts.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    return [MetricResult(d["name"], d.get("score"), bool(d.get("passed", True)),
                         d.get("rationale", d.get("detail", "")), d.get("insights", ""))
            for d in data]


def _report_or_json(aggregated, judge_label, as_json):
    if as_json:
        print(json.dumps({"judge": judge_label, "overall_passed": overall_passed(aggregated),
                          "metrics": aggregated}, indent=2, ensure_ascii=False))
    else:
        print(build_report(aggregated, judge_label))


def main():
    parser = argparse.ArgumentParser(description="Wiki quality eval (LLM-as-judge metrics)")
    parser.add_argument("command", nargs="?", choices=["collect", "score"],
                        help="collect: deterministic + emit brief; score <dir>: gate captured verdicts")
    parser.add_argument("path", nargs="?", help="run dir for `score`")
    parser.add_argument("--judge", help="claude | codex | none | <custom command> (default: auto-detect; OWNER_JUDGE can restrict)")
    parser.add_argument("--runs", type=int, default=1, help="judge runs to average (default 1)")
    parser.add_argument("--gate", action="store_true", help="exit non-zero on regression")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    wiki_root = WIKI_ROOT
    thresholds = load_thresholds(wiki_root)

    if args.command == "collect":
        print(f"Wrote judging brief: {emit_brief(wiki_root)}")
        return
    if args.command == "score":
        if not args.path:
            sys.exit("score requires a run dir containing verdicts.json")
        agg = aggregate([load_verdicts(Path(args.path))], thresholds)
        _report_or_json(agg, "external", args.json)
        sys.exit(gate_exit_code(agg) if args.gate else 0)

    try:
        judge, label = detect_judge(args.judge)
    except ValueError as e:
        sys.exit(f"[eval] {e}")
    if judge is None:
        brief = emit_brief(wiki_root)
        agg = aggregate([run_once(wiki_root, None)], thresholds)
        _report_or_json(agg, label, args.json)
        if not args.json:
            print(f"\nNo judge available — judge metrics not scored. Brief: {brief}")
        return

    runs = [run_once(wiki_root, judge) for _ in range(max(1, args.runs))]
    agg = aggregate(runs, thresholds)
    record = write_run_record(wiki_root, agg, label)
    _report_or_json(agg, label, args.json)
    if not args.json:
        print(f"\nRun record: {record}")
    if args.gate:
        sys.exit(gate_exit_code(agg))


if __name__ == "__main__":
    main()
