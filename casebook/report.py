"""Build the casebook document and the static viewer from results/.

Outputs:
    results/summary.json   one row per case, the numbers the report quotes
    reports/casebook.md    the ten cases: prose from cases/*/README.md plus a
                           generated "Measured" section per case
    index.html             a self-contained viewer of the same material
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from . import cases as casemod
from .db import ROOT
from .measure import RESULTS

REPORTS = ROOT / "reports"
INDEX = ROOT / "index.html"
PLAN_LINES = 45


# --------------------------------------------------------------------------- formatting

def ms(v) -> str:
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v / 1000:,.2f} s"
    if v >= 10:
        return f"{v:,.1f} ms"
    return f"{v:,.2f} ms"


def size(b) -> str:
    if b is None:
        return "-"
    sign = "-" if b < 0 else "+" if b > 0 else ""
    b = abs(b)
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024 or unit == "GB":
            return f"{sign}{b:,.0f} {unit}" if unit == "B" else f"{sign}{b:,.1f} {unit}"
        b /= 1024
    return "-"


def mb(blocks) -> str:
    return f"{blocks * 8192 / 2**20:,.1f} MB" if blocks is not None else "-"


def times(x) -> str:
    return f"{x:,.0f}x" if x >= 100 else f"{x:,.1f}x"


def qerr(plan: dict | None) -> str:
    if not plan or not plan.get("worst_estimate"):
        return "-"
    w = plan["worst_estimate"]
    return f"{w['q_error']:,.0f}x" if w["q_error"] >= 10 else f"{w['q_error']:,.1f}x"


def trim_plan(text: str, limit: int = PLAN_LINES) -> str:
    lines = [l for l in text.rstrip().splitlines() if not l.startswith("Settings:")]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines in the plan file)"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- data

def load_results() -> list[dict]:
    out = []
    for case in casemod.all_cases():
        f = RESULTS / case.slug / "result.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        bench = RESULTS / case.slug / "bench.json"
        r["bench"] = json.loads(bench.read_text()) if bench.exists() else None
        r["readme"] = case.readme
        for side in ("before", "after"):
            p = RESULTS / case.slug / f"{side}.plan.txt"
            r.setdefault("plan_text", {})[side] = p.read_text() if p.exists() else ""
            q = RESULTS / case.slug / f"{side}.sql"
            r.setdefault("sql_text", {})[side] = q.read_text() if q.exists() else ""
        if case.driver == "pgbench":
            for side in ("before", "after"):
                p = RESULTS / case.slug / f"{side}.pgbench"
                r["sql_text"][side] = p.read_text() if p.exists() else ""
        out.append(r)
    return out


def summary_row(r: dict) -> dict:
    o = r["outcome"]
    row = {
        "case": r["case"], "slug": r["slug"], "title": r["title"], "category": r["category"],
        "driver": r["driver"], "metric": o["metric"], "before": o["before"], "after": o["after"],
        "speedup": o["speedup"], "passed": o["passed"], "same_result": o.get("same_result"),
        "index_bytes": r["fix"]["storage"]["index_bytes"],
        "table_bytes": r["fix"]["storage"]["table_bytes"],
    }
    if r["driver"] == "plan":
        b, a = r["before"], r["after"]
        row.update({
            "buffers_before_mb": b["plan"]["buffers_touched_mb"],
            "buffers_after_mb": a["plan"]["buffers_touched_mb"],
            "io_ms_before": b["timing"]["server"]["io_ms"],
            "io_ms_after": a["timing"]["server"]["io_ms"],
            "non_io_ms_before": b["timing"]["server"]["non_io_ms"],
            "non_io_ms_after": a["timing"]["server"]["non_io_ms"],
            "q_error_before": (b["plan"]["worst_estimate"] or {}).get("q_error"),
            "q_error_after": (a["plan"]["worst_estimate"] or {}).get("q_error"),
            "client_ms_before": b["timing"]["median_ms"],
            "client_ms_after": a["timing"]["median_ms"],
        })
    elif r["driver"] == "n_plus_one":
        row.update({
            "round_trips_before": r["before"]["timing"]["round_trips"],
            "round_trips_after": 1,
            "server_ms_before": r["before"]["timing"]["server"]["exec_ms"],
            "server_ms_after": r["after"]["timing"]["server"]["exec_ms"],
            "buffers_before_mb": round((r["before"]["timing"]["server"]["shared_hit"]
                                        + r["before"]["timing"]["server"]["shared_read"]) * 8192 / 2**20, 2),
            "buffers_after_mb": r["after"]["plan"]["buffers_touched_mb"],
        })
    else:
        row.update({
            "p95_ms_before": r["before"]["latency_ms"]["p95"],
            "p95_ms_after": r["after"]["latency_ms"]["p95"],
            "lock_waiters_before": r["before"]["locks"]["avg_waiting_on_lock"],
            "lock_waiters_after": r["after"]["locks"]["avg_waiting_on_lock"],
        })
    return row


# --------------------------------------------------------------------------- markdown

def measured_markdown(r: dict) -> str:
    out = ["### Measured", ""]
    o = r["outcome"]
    same = {True: "identical", False: "**different**", None: "n/a"}[o.get("same_result")]
    if r["driver"] == "plan":
        b, a = r["before"], r["after"]
        bs, as_ = b["timing"]["server"], a["timing"]["server"]
        out += [
            "| | Before | After |",
            "|---|---:|---:|",
            f"| Server execution time (pg_stat_statements, mean) | {ms(bs['exec_ms'])} | {ms(as_['exec_ms'])} |",
            f"| Client wall clock (median of {len(b['timing']['runs_ms'])} / {len(a['timing']['runs_ms'])}) | {ms(b['timing']['median_ms'])} | {ms(a['timing']['median_ms'])} |",
            f"| Waiting on block I/O | {ms(bs['io_ms'])} | {ms(as_['io_ms'])} |",
            f"| Not waiting on I/O (CPU proxy) | {ms(bs['non_io_ms'])} | {ms(as_['non_io_ms'])} |",
            f"| Buffers touched (hit + read) | {b['plan']['buffers_touched_mb']:,} MB | {a['plan']['buffers_touched_mb']:,} MB |",
            f"| Blocks read from outside shared_buffers | {b['plan']['shared_read_blocks']:,} | {a['plan']['shared_read_blocks']:,} |",
            f"| Rows read then discarded by filters | {b['plan']['rows_removed']:,} | {a['plan']['rows_removed']:,} |",
            f"| Worst row-estimate error (q-error) | {qerr(b['plan'])} | {qerr(a['plan'])} |",
            f"| Rows returned | {b['timing']['rows']:,} | {a['timing']['rows']:,} |",
            f"| Plan nodes | {', '.join(b['plan']['node_types'])} | {', '.join(a['plan']['node_types'])} |",
            "",
        ]
    elif r["driver"] == "n_plus_one":
        b, a = r["before"]["timing"], r["after"]["timing"]
        out += [
            "| | Before (lazy loading) | After (one query) |",
            "|---|---:|---:|",
            f"| Page render time, client (median of {len(b['runs_ms'])} / {len(a['runs_ms'])}) | {ms(b['median_ms'])} | {ms(a['median_ms'])} |",
            f"| Round trips per page | {b['round_trips']:,} | 1 |",
            f"| Server execution time per page (sum of statements) | {ms(b['server']['exec_ms'])} | {ms(a['server']['exec_ms'])} |",
            f"| Statements executed per page | {b['server']['calls']:,.0f} | {a['server']['calls']:,.0f} |",
            f"| Buffers touched per page | {mb(b['server']['shared_hit'] + b['server']['shared_read'])} | {r['after']['plan']['buffers_touched_mb']:,} MB |",
            "",
            "Every individual query in the N+1 version is an index lookup that runs in well under "
            "a millisecond on the server; the page is slow because of the number of round trips, "
            "which is why the headline metric for this case is client-side page time.",
            "",
        ]
    else:
        b, a = r["before"], r["after"]
        out += [
            f"pgbench, {b['clients']} clients, {b['threads']} threads, {b['duration_s']} s per run.",
            "",
            "| | Before | After |",
            "|---|---:|---:|",
            f"| Throughput | {b['tps']:,.0f} TPS | {a['tps']:,.0f} TPS |",
            f"| Latency p50 / p95 / p99 | {b['latency_ms']['p50']:,} / {b['latency_ms']['p95']:,} / {b['latency_ms']['p99']:,} ms | {a['latency_ms']['p50']:,} / {a['latency_ms']['p95']:,} / {a['latency_ms']['p99']:,} ms |",
            f"| Backends waiting on a lock (mean of samples) | {b['locks']['avg_waiting_on_lock']} of {b['clients']} | {a['locks']['avg_waiting_on_lock']} of {a['clients']} |",
            f"| Backends idle in transaction (mean) | {b['locks']['avg_idle_in_transaction']} | {a['locks']['avg_idle_in_transaction']} |",
            f"| Failed transactions | {b['failed']:,} | {a['failed']:,} |",
            "",
        ]
    out += [
        f"**Result: {o['before']} → {o['after']} ({times(o['speedup'])} on {o['metric']}); "
        f"result set {same}; {'meets' if o['passed'] else '**misses**'} the {o['target']:.0f}x target.**",
        "",
    ]

    storage = r["fix"]["storage"]
    if storage["changes"]:
        out += ["Storage cost of the fix:", "", "| Relation | Kind | Change | Size after |", "|---|---|---:|---:|"]
        for c in storage["changes"]:
            out.append(f"| `{c['relation']}` | {c['kind']} ({c['status']}) | {size(c['delta_bytes'])} | {size(c['after_bytes'])} |")
        out += ["", f"Net index change {size(storage['index_bytes'])}, net table change "
                    f"{size(storage['table_bytes'])}. The fix took {r['fix']['seconds']:,.1f} s to apply.", ""]
    else:
        out += [f"The fix adds no storage. It took {r['fix']['seconds']:,.1f} s to apply.", ""]

    if r.get("hypo"):
        h = r["hypo"]
        idx = ", ".join(f"{i['name']} (~{i['estimated_size']})" for i in h["hypothetical_indexes"])
        out += [f"HypoPG what-if before building anything: estimated plan cost "
                f"{h['estimated_cost_before']:,.0f} → {h['estimated_cost_with_hypothetical']:,.0f}; "
                f"planner {'would' if h['planner_uses_hypothetical_index'] else 'would not'} use the "
                f"hypothetical index {idx}.", ""]

    if r.get("alternatives"):
        out += ["Alternatives measured and not chosen:", "",
                "| Alternative | Server time | Index/table cost | Why not |", "|---|---:|---:|---|"]
        for alt in r["alternatives"]:
            st = alt["storage"]
            out.append(f"| {alt['name']} | {ms(alt['timing']['server']['exec_ms'])} | "
                       f"{size(st['index_bytes'] + st['table_bytes'])} | {alt['note']} |")
        out.append("")

    if r.get("extras"):
        out += ["Other workloads affected by the fix:", "", "| Workload | Before | After |", "|---|---:|---:|"]
        for e in r["extras"]:
            def cell(side):
                v = e.get(side)
                if not v:
                    return "-"
                if "tps" in v:
                    return f"{v['tps']:,.0f} TPS (p95 {v['latency_ms']['p95']} ms)"
                return ms(v["timing"]["median_ms"])
            out.append(f"| {e['name']} | {cell('before')} | {cell('after')} |")
        notes = [e["note"] for e in r["extras"] if e.get("note")]
        out += [""] + [f"*{n}*" for n in notes] + ([""] if notes else [])

    if r.get("bench"):
        bb, ba = r["bench"]["before"], r["bench"]["after"]
        out += [f"Under concurrency (pgbench, {bb['clients']} clients, {bb['duration_s']} s): "
                f"{bb['tps']:,.1f} → {ba['tps']:,.1f} TPS, p99 {bb['latency_ms']['p99']:,} → "
                f"{ba['latency_ms']['p99']:,} ms.", ""]

    lang = "" if r["driver"] == "pgbench" else "sql"
    out += [
        "<details><summary>Statements, plans and fix</summary>", "",
        "Before:", "", f"```{lang}", r["sql_text"]["before"].strip(), "```", "",
        "After:", "", f"```{lang}", r["sql_text"]["after"].strip(), "```", "",
        "Fix:", "", "```sql", r["fix"]["sql"].strip(), "```", "",
    ]
    if r["plan_text"]["before"]:
        out += ["Plan before:", "", "```", trim_plan(r["plan_text"]["before"]), "```", ""]
    if r["plan_text"]["after"]:
        out += ["Plan after:", "", "```", trim_plan(r["plan_text"]["after"]), "```", ""]
    out += [f"Full plans (JSON and text): `results/{r['slug']}/`.", "", "</details>", ""]
    return "\n".join(out)


def strip_h1(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def environment_markdown(env: dict) -> str:
    rows = env.get("rows", {})
    s = env.get("settings", {})
    return "\n".join([
        "| | |", "|---|---|",
        f"| PostgreSQL | {env.get('server_version')} |",
        f"| Dataset | scale {env.get('scale')}: {sum(rows.values()):,} rows, {env.get('database_size')} on disk |",
        "| Rows | " + ", ".join(f"{k} {v:,}" for k, v in rows.items()) + " |",
        f"| Machine | Docker VM, {env.get('cpus')} vCPUs, {env.get('memory_gb')} GB RAM, SSD |",
        f"| Key settings | shared_buffers {s.get('shared_buffers')}, work_mem {s.get('work_mem')}, "
        f"random_page_cost {s.get('random_page_cost')}, parallel workers/gather "
        f"{s.get('max_parallel_workers_per_gather')}, jit {s.get('jit')} |",
    ])


def casebook_markdown(results: list[dict], rows: list[dict]) -> str:
    env = results[0]["environment"] if results else {}
    passed = sum(1 for r in rows if r["passed"])
    median = median_speedup(rows)
    out = [
        "# PostgreSQL Performance Casebook",
        "",
        "Ten slow queries on a 64-million-row dataset, each with the plan PostgreSQL chose, what it was "
        "getting wrong, the fix, and the measured difference. Everything in this document is generated "
        "from `results/` by `make report`; rerun `make run` to reproduce it on your machine.",
        "",
        f"**{passed} of {len(rows)} cases meet the 5x target. Median improvement {times(median)}.**",
        "",
        "## How the numbers were measured",
        "",
        environment_markdown(env),
        "",
        "- Each statement runs warm-up passes, then a fixed number of timed passes inside "
        "`BEGIN ... ROLLBACK`. The headline is the mean server execution time from "
        "`pg_stat_statements` over the timed passes, so it excludes network and driver overhead. "
        "Client wall clock is reported next to it.",
        "- Buffers, rows removed and row-estimate errors come from one `EXPLAIN (ANALYZE, BUFFERS)` "
        "after the timed passes. The q-error is max(estimate/actual, actual/estimate) for the worst "
        "node, ignoring nodes under a LIMIT that stopped early.",
        "- `pg_stat_statements` has no CPU counter. \"Not waiting on I/O\" is execution time minus "
        "`track_io_timing` read/write time. It stands in for CPU on a serial plan and understates it "
        "on a parallel one.",
        "- The dataset (~7 GB) is larger than the VM's memory, so caches are warm but not complete. "
        "Large scans still read from the OS page cache or disk, as they would on a real instance. "
        "Runs on other hardware will differ in absolute terms; the plan shapes and ratios are the point.",
        "- Each fix's result set is fingerprinted and compared with the slow version's. A fix that "
        "changes the answer fails the case.",
        "",
        "## Summary",
        "",
        "| # | Case | Category | Before | After | Improvement | Index / table cost |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        cost = r["index_bytes"] + r["table_bytes"]
        out.append(f"| {r['case']} | [{r['title']}](#{anchor(r)}) | {r['category']} | {r['before']} | "
                   f"{r['after']} | {times(r['speedup'])} | {size(cost) if cost else 'none'} |")
    out += ["", "Indexing and partitioning decisions drawn from these cases: "
                "[docs/indexing-decision-guide.md](../docs/indexing-decision-guide.md).", ""]
    for r in results:
        out += [f"## {r['case']}. {r['title']}", "", strip_h1(r["readme"]) or f"*{r['summary']}*", "",
                measured_markdown(r)]
    return "\n".join(out).rstrip() + "\n"


def median_speedup(rows: list[dict]) -> float:
    xs = sorted(r["speedup"] for r in rows)
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def anchor(r: dict) -> str:
    text = f"{r['case']}. {r['title']}".lower()
    return re.sub(r"[^a-z0-9 -]", "", text).replace(" ", "-")


# --------------------------------------------------------------------------- tiny markdown -> HTML

def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*]+)\*(?![*\w])", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def md_to_html(md: str) -> str:
    out, para, lines = [], [], md.splitlines()
    i = 0

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            lang = s[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            cls = f' class="lang-{lang}"' if lang else ""
            out.append(f"<pre{cls}><code>{html.escape(chr(10).join(body))}</code></pre>")
        elif s.startswith("<details") or s.startswith("</details") or s.startswith("<summary"):
            flush()
            out.append(s)
        elif re.match(r"#{1,4} ", s):
            flush()
            level = len(s) - len(s.lstrip("#"))
            out.append(f"<h{level + 1}>{_inline(s[level + 1:])}</h{level + 1}>")
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            head = [c.strip() for c in s.strip("|").split("|")]
            aligns = ["right" if c.strip().endswith(":") else "left"
                      for c in lines[i + 1].strip().strip("|").split("|")]
            rows_html = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows_html.append("<tr>" + "".join(
                    f'<td class="{aligns[j] if j < len(aligns) else "left"}">{_inline(c)}</td>'
                    for j, c in enumerate(cells)) + "</tr>")
                i += 1
            out.append('<div class="table"><table><thead><tr>' + "".join(
                f'<th class="{aligns[j]}">{_inline(h)}</th>' for j, h in enumerate(head))
                + "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table></div>")
            continue
        elif re.match(r"^(- |\d+\. )", s):
            flush()
            ordered = bool(re.match(r"^\d+\. ", s))
            items = []
            while i < len(lines) and re.match(r"^(- |\d+\. )", lines[i].strip()):
                item = re.sub(r"^(- |\d+\. )", "", lines[i].strip())
                i += 1
                while i < len(lines) and lines[i].startswith("  ") and lines[i].strip() \
                        and not re.match(r"^(- |\d+\. )", lines[i].strip()):
                    item += " " + lines[i].strip()
                    i += 1
                items.append(f"<li>{_inline(item)}</li>")
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        elif not s:
            flush()
        else:
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)


# --------------------------------------------------------------------------- HTML viewer

def viewer_html(results: list[dict], rows: list[dict], casebook_md: str) -> str:
    sections = re.split(r"(?m)^## ", casebook_md)
    intro = sections[0]
    rest = ["## " + s for s in sections[1:]]
    method = next((s for s in rest if s.startswith("## How the numbers")), "")
    case_sections = {s.split(".", 1)[0][3:].strip(): s for s in rest if re.match(r"## \d\d\.", s)}

    cards = []
    for r in rows:
        width = max(2.0, min(100.0, 100 * (1 / r["speedup"]))) if r["speedup"] else 100
        cards.append(f"""
      <a class="card" href="#case-{r['case']}">
        <span class="num">{r['case']}</span>
        <span class="title">{html.escape(r['title'])}</span>
        <span class="cat">{html.escape(r['category'])}</span>
        <span class="bars" aria-hidden="true"><span class="bar before"></span><span class="bar after" style="width:{width:.1f}%"></span></span>
        <span class="vals"><span>{html.escape(r['before'])}</span><span>{html.escape(r['after'])}</span></span>
        <span class="gain {'ok' if r['passed'] else 'bad'}">{times(r['speedup'])}</span>
      </a>""")

    body_cases = []
    for r in rows:
        sec = case_sections.get(r["case"], "")
        body_cases.append(f'<section class="case" id="case-{r["case"]}">{md_to_html(sec)}</section>')

    passed = sum(1 for r in rows if r["passed"])
    env = results[0]["environment"] if results else {}
    total_rows = sum(env.get("rows", {}).values())
    intro_html = md_to_html(strip_h1(intro))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0d1723">
<title>PostgreSQL Performance Casebook</title>
<style>
:root {{
  --bg: #f6f7f9; --panel: #ffffff; --ink: #14202e; --muted: #5b6878; --line: #dfe4ea;
  --accent: #2563eb; --before: #c2410c; --after: #0f766e; --code-bg: #f1f4f7; --bad: #b91c1c;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0d1723; --panel: #132131; --ink: #e6edf5; --muted: #9aa8b8; --line: #23364a;
    --accent: #7aa7ff; --before: #f08a4b; --after: #4cc3b0; --code-bg: #0a131d; --bad: #f87171;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0d1723; --panel: #132131; --ink: #e6edf5; --muted: #9aa8b8; --line: #23364a;
  --accent: #7aa7ff; --before: #f08a4b; --after: #4cc3b0; --code-bg: #0a131d; --bad: #f87171;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 32px 16px 80px; }}
header h1 {{ font-size: 30px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }}
header p.lede {{ color: var(--muted); margin: 0 0 20px; max-width: 720px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0 28px; }}
.stat {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }}
.stat b {{ display: block; font-size: 24px; font-variant-numeric: tabular-nums; }}
.stat span {{ color: var(--muted); font-size: 13px; }}
.cards {{ display: grid; gap: 8px; }}
.card {{ display: grid; grid-template-columns: 34px 1fr 160px 150px 64px; gap: 4px 14px; align-items: center;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  color: inherit; text-decoration: none; }}
.card:hover {{ border-color: var(--accent); }}
.card .num {{ color: var(--muted); font-variant-numeric: tabular-nums; grid-row: span 2; }}
.card .title {{ font-weight: 600; }}
.card .cat {{ color: var(--muted); font-size: 13px; grid-column: 2; }}
.card .bars {{ grid-column: 3; grid-row: 1 / span 2; display: grid; gap: 4px; }}
.card .bar {{ height: 8px; border-radius: 4px; display: block; }}
.card .bar.before {{ background: var(--before); width: 100%; }}
.card .bar.after {{ background: var(--after); }}
.card .vals {{ grid-column: 4; grid-row: 1 / span 2; display: grid; font-size: 13px; color: var(--muted);
  font-variant-numeric: tabular-nums; }}
.card .gain {{ grid-column: 5; grid-row: 1 / span 2; text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; }}
.gain.ok {{ color: var(--after); }} .gain.bad {{ color: var(--bad); }}
.legend {{ display: flex; gap: 16px; color: var(--muted); font-size: 13px; margin: 0 0 8px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }}
section.case, section.method {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 8px 24px 16px; margin-top: 24px; }}
h2 {{ font-size: 22px; margin: 18px 0 8px; }}
h3 {{ font-size: 17px; margin: 22px 0 6px; }}
h4 {{ font-size: 15px; margin: 18px 0 4px; }}
a {{ color: var(--accent); }}
code {{ font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--code-bg);
  padding: 1px 4px; border-radius: 4px; }}
pre {{ background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
  overflow-x: auto; }}
pre code {{ background: none; padding: 0; font-size: 12.5px; }}
.table {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; margin: 8px 0; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 6px 8px; vertical-align: top; }}
th {{ text-align: left; color: var(--muted); font-weight: 600; }}
td.right, th.right {{ text-align: right; font-variant-numeric: tabular-nums; }}
td:first-child {{ min-width: 180px; }}
details {{ margin: 12px 0; }}
summary {{ cursor: pointer; color: var(--accent); font-weight: 600; }}
footer {{ color: var(--muted); font-size: 13px; margin-top: 32px; }}
@media (max-width: 720px) {{
  .card {{ grid-template-columns: 28px 1fr 56px; }}
  .card .bars, .card .vals {{ grid-column: 2; grid-row: auto; }}
  .card .gain {{ grid-column: 3; grid-row: 1; }}
  section.case, section.method {{ padding: 4px 14px 12px; }}
}}
</style>
</head>
<body>
<main>
<header>
  <h1>PostgreSQL Performance Casebook</h1>
  <p class="lede">Ten slow queries on a {total_rows / 1e6:,.0f}-million-row dataset: the plan PostgreSQL chose,
  what it was getting wrong, the fix, and the measured difference. Generated from <code>results/</code>
  by <code>make report</code>.</p>
</header>
<div class="stats">
  <div class="stat"><b>{passed}/{len(rows)}</b><span>cases at 5x or better</span></div>
  <div class="stat"><b>{times(median_speedup(rows)) if rows else '-'}</b><span>median improvement</span></div>
  <div class="stat"><b>{total_rows:,}</b><span>rows in the seed</span></div>
  <div class="stat"><b>{html.escape(str(env.get('server_version', '-')).split(' ')[0])}</b><span>PostgreSQL</span></div>
</div>
<p class="legend"><span><i style="background:var(--before)"></i>before</span><span><i style="background:var(--after)"></i>after (bar length relative to before)</span></p>
<nav class="cards">{''.join(cards)}
</nav>
<section class="method">{md_to_html(method)}</section>
{''.join(body_cases)}
<footer>Generated by <code>python -m casebook report</code>. Plans, SQL and raw numbers for every case are in <code>results/&lt;case&gt;/</code>.</footer>
</main>
</body>
</html>
"""


# --------------------------------------------------------------------------- README results block

README = ROOT / "README.md"


def update_readme(rows: list[dict], env: dict) -> None:
    """Replace the block between the results markers in README.md, if present."""
    if not README.exists():
        return
    text = README.read_text()
    start, end = "<!-- results:start -->", "<!-- results:end -->"
    if start not in text or end not in text:
        return
    passed = sum(1 for r in rows if r["passed"])
    lines = [
        start,
        f"Measured on PostgreSQL {str(env.get('server_version', '')).split(' ')[0]}, "
        f"{sum(env.get('rows', {}).values()):,} rows, {env.get('cpus')} vCPUs / "
        f"{env.get('memory_gb')} GB RAM (Docker VM). {passed} of {len(rows)} cases meet the 5x "
        f"target; median improvement {times(median_speedup(rows))}. Generated by `make report`.",
        "",
        "| # | Case | Before | After | Improvement | Storage cost of fix |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        cost = r["index_bytes"] + r["table_bytes"]
        lines.append(f"| {r['case']} | {r['title']} | {r['before']} | {r['after']} | "
                     f"{times(r['speedup'])} | {size(cost) if cost else 'none'} |")
    lines += ["", "Times are server execution time per call unless the metric says otherwise "
                  "(case 03: client page time; case 07: throughput).", end]
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    README.write_text(head + "\n".join(lines) + tail)


# --------------------------------------------------------------------------- entry point

def build() -> dict:
    results = load_results()
    if not results:
        print("No results yet; run `make run` first.")
        return {}
    rows = [summary_row(r) for r in results]
    env = results[0]["environment"]
    summary = {"environment": env, "cases": rows}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2))
    REPORTS.mkdir(exist_ok=True)
    md = casebook_markdown(results, rows)
    (REPORTS / "casebook.md").write_text(md)
    INDEX.write_text(viewer_html(results, rows, md))
    update_readme(rows, results[0]["environment"])
    print(f"Wrote {RESULTS / 'summary.json'}, {REPORTS / 'casebook.md'} and {INDEX}")
    return {"cases": len(rows), "passed": sum(1 for r in rows if r["passed"])}
