"""Running a case and measuring it.

For every case the sequence is the same:

    setup.sql -> resolve params -> [HypoPG what-if] -> measure BEFORE
    -> snapshot relation sizes -> fix.sql -> snapshot again -> measure AFTER
    -> teardown.sql

"Measure" depends on the driver:

plan        Run the statement `warmup` times, then `repeat` times timed from the
            client (wall clock, rows fetched). pg_stat_statements is reset
            around the timed runs to get buffers, I/O wait and execution time
            per call from the server's side. Then one EXPLAIN (ANALYZE, BUFFERS)
            in JSON for the numbers and one in text for humans.

n_plus_one  Render one "page" the way an ORM would: a parent query, one query
            per parent row, one per child row. Time the whole page from the
            client and count round trips.

pgbench     Run a pgbench script with concurrent clients, record TPS and latency
            percentiles from the per-transaction log, and sample
            pg_stat_activity to see how many backends were waiting on locks.

Every timed statement runs inside BEGIN ... ROLLBACK, so a measured DELETE or
DROP never changes the dataset.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql as psql

from . import plans
from .cases import Case
from .db import ROOT, client_side, connect, scalar, statements

RESULTS = ROOT / "results"
PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
SLOW_RUN_S = 5.0


# --------------------------------------------------------------------------- SQL helpers

def render(text: str, params: dict, conn: psycopg.Connection) -> str:
    def sub(m):
        name = m.group(1)
        if name not in params:
            raise KeyError(f"SQL references {{{{{name}}}}} but case.toml defines no such param")
        return psql.Literal(params[name]).as_string(conn)
    return PLACEHOLDER.sub(sub, text).strip().rstrip(";")


def run_script(conn: psycopg.Connection, text: str, params: dict, label: str) -> float:
    """Execute a multi-statement script one statement at a time (autocommit)."""
    started = time.monotonic()
    for stmt in statements(text):
        body = render(stmt, params, conn)
        if not body:
            continue
        t = time.monotonic()
        conn.execute(body)
        took = time.monotonic() - t
        if took > 2:
            first = next((l for l in body.splitlines() if l.strip() and not l.strip().startswith("--")), "")
            print(f"    {label}: {took:6.1f}s  {first.strip()[:80]}", flush=True)
    return time.monotonic() - started


def resolve_params(conn: psycopg.Connection, case: Case) -> dict:
    out = {}
    for name, query in case.params.items():
        value = scalar(conn, render(query, out, conn))
        if value is None:
            raise RuntimeError(f"param {name!r} resolved to NULL: {query}")
        out[name] = value
    return out


# --------------------------------------------------------------------------- server stats

PGSS_TOTALS = """
SELECT coalesce(sum(calls), 0)::bigint                       AS calls,
       coalesce(sum(total_exec_time), 0)::float8              AS exec_ms,
       coalesce(sum(rows), 0)::bigint                         AS rows,
       coalesce(sum(shared_blks_hit), 0)::bigint              AS shared_hit,
       coalesce(sum(shared_blks_read), 0)::bigint             AS shared_read,
       coalesce(sum(shared_blks_dirtied), 0)::bigint          AS shared_dirtied,
       coalesce(sum(temp_blks_read + temp_blks_written), 0)::bigint AS temp_blks,
       coalesce(sum(blk_read_time + blk_write_time), 0)::float8     AS io_ms,
       coalesce(sum(wal_bytes), 0)::bigint                    AS wal_bytes
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND userid = (SELECT oid FROM pg_roles WHERE rolname = current_user)
  AND toplevel
  AND query NOT ILIKE '%%pg_stat_statements%%'
"""


def pgss_reset(conn) -> None:
    conn.execute("SELECT pg_stat_statements_reset()")


def pgss_per_execution(conn, executions: int) -> dict:
    row = conn.execute(PGSS_TOTALS).fetchone()
    keys = ["calls", "exec_ms", "rows", "shared_hit", "shared_read", "shared_dirtied",
            "temp_blks", "io_ms", "wal_bytes"]
    totals = dict(zip(keys, row))
    n = max(executions, 1)
    per = {k: (v / n) for k, v in totals.items()}
    # pg_stat_statements has no CPU counter. Execution time minus time spent
    # waiting on block I/O is the closest server-side proxy; for parallel plans
    # it is the leader's elapsed time, not CPU summed across workers.
    per["non_io_ms"] = per["exec_ms"] - per["io_ms"]
    return {k: round(v, 3) for k, v in per.items()}


SIZES = """
SELECT c.oid::regclass::text, c.relkind, pg_relation_size(c.oid)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'i', 'm')
"""


def relation_sizes(conn) -> dict[str, tuple[str, int]]:
    return {name: (kind, size) for name, kind, size in conn.execute(SIZES).fetchall()}


def size_delta(before: dict, after: dict) -> dict:
    changes = []
    for name in sorted(set(before) | set(after)):
        kind = (after.get(name) or before.get(name))[0]
        b = before.get(name, (kind, 0))[1]
        a = after.get(name, (kind, 0))[1]
        if a != b and (abs(a - b) >= 64 * 1024 or name not in before or name not in after):
            changes.append({"relation": name, "kind": {"r": "table", "i": "index", "m": "matview"}[kind],
                            "before_bytes": b, "after_bytes": a, "delta_bytes": a - b,
                            "status": "added" if name not in before else
                                      "dropped" if name not in after else "changed"})
    return {
        "index_bytes": sum(c["delta_bytes"] for c in changes if c["kind"] == "index"),
        "table_bytes": sum(c["delta_bytes"] for c in changes if c["kind"] != "index"),
        "changes": changes,
    }


# --------------------------------------------------------------------------- statement timing

def _one_run(conn, cur, text: str, timeout_s: int) -> tuple[float, int, str]:
    conn.execute("BEGIN")
    try:
        conn.execute(f"SET LOCAL statement_timeout = '{timeout_s}s'")
        t = time.perf_counter()
        cur.execute(text)
        data = cur.fetchall() if cur.description else []
        ms = (time.perf_counter() - t) * 1000
        rows = len(data) if cur.description else cur.rowcount
        # Fingerprint of the result, normalised so int vs numeric sums and
        # equal timestamps compare equal. Used to prove a fix returns the same
        # answer as the slow version.
        digest = hashlib.sha256(repr([tuple(str(v) for v in r) for r in data]).encode()).hexdigest()[:16]
        return ms, rows, digest
    finally:
        conn.execute("ROLLBACK")


def time_statement(conn, text: str, repeat: int, warmup: int, timeout_s: int) -> dict:
    cur = client_side(conn)
    first_ms = None
    for i in range(max(warmup, 1)):
        ms, _, _ = _one_run(conn, cur, text, timeout_s)
        first_ms = first_ms or ms
        if ms > SLOW_RUN_S * 1000:
            break
    if first_ms and first_ms > SLOW_RUN_S * 1000:
        repeat = min(repeat, 3)

    pgss_reset(conn)
    runs, rows, digest = [], 0, ""
    for _ in range(repeat):
        ms, rows, digest = _one_run(conn, cur, text, timeout_s)
        runs.append(ms)
    server = pgss_per_execution(conn, repeat)
    return {
        "runs_ms": [round(r, 3) for r in runs],
        "median_ms": round(statistics.median(runs), 3),
        "min_ms": round(min(runs), 3),
        "max_ms": round(max(runs), 3),
        "rows": rows,
        "result_digest": digest,
        "server": server,
    }


def explain(conn, text: str, fmt: str, analyze: bool = True, timeout_s: int = 300):
    opts = ["ANALYZE", "BUFFERS", "SETTINGS"] if analyze else ["SETTINGS"]
    opts.append(f"FORMAT {fmt.upper()}")
    conn.execute("BEGIN")
    try:
        conn.execute(f"SET LOCAL statement_timeout = '{timeout_s}s'")
        rows = conn.execute(f"EXPLAIN ({', '.join(opts)}) {text}").fetchall()
    finally:
        conn.execute("ROLLBACK")
    if fmt == "json":
        return rows[0][0]
    return "\n".join(r[0] for r in rows)


def measure_statement(conn, case: Case, text: str, outdir: Path, label: str,
                      with_plan: bool = True) -> dict:
    print(f"  {label}: timing", flush=True)
    timing = time_statement(conn, text, case.repeat, case.warmup, case.timeout_s)
    if not with_plan:
        # DDL such as DROP TABLE cannot be EXPLAINed; keep the timing only.
        (outdir / f"{label}.sql").write_text(text + ";\n")
        print(f"  {label}: median {timing['median_ms']:,.2f} ms over {len(timing['runs_ms'])} runs", flush=True)
        return {"timing": timing, "plan": None}
    plan_json = explain(conn, text, "json", timeout_s=case.timeout_s)
    plan_text = explain(conn, text, "text", timeout_s=case.timeout_s)
    (outdir / f"{label}.plan.json").write_text(json.dumps(plan_json, indent=2))
    (outdir / f"{label}.plan.txt").write_text(plan_text + "\n")
    (outdir / f"{label}.sql").write_text(text + ";\n")
    summary = plans.summarize(plan_json)
    print(f"  {label}: median {timing['median_ms']:,.2f} ms over {len(timing['runs_ms'])} runs; "
          f"{summary['buffers_touched_mb']:,} MB of buffers; {', '.join(summary['node_types'])}", flush=True)
    return {"timing": timing, "plan": summary}


# --------------------------------------------------------------------------- alternatives

def measure_alternative(conn, case: Case, alt: dict, params: dict, outdir: Path) -> dict:
    """Apply a rejected fix, measure it, and roll it back."""
    label = f"alt-{alt['key']}"
    print(f"  {label}: {alt['name']}", flush=True)
    sizes0 = relation_sizes(conn)
    took = run_script(conn, case.sql(alt["fix"]), params, label)
    sizes1 = relation_sizes(conn)
    try:
        text = render(case.sql(alt.get("query", case.after_file)), params, conn)
        measured = measure_statement(conn, case, text, outdir, label)
    finally:
        run_script(conn, case.sql(alt["teardown"]), params, f"{label} teardown")
    return {"key": alt["key"], "name": alt["name"], "note": alt.get("note", ""),
            "fix_sql": case.sql(alt["fix"]), "fix_seconds": round(took, 2),
            "storage": size_delta(sizes0, sizes1), **measured}


# --------------------------------------------------------------------------- HypoPG

def hypothetical(conn, case: Case, before_text: str, params: dict) -> dict | None:
    hypo = case.sql("hypo.sql")
    if not hypo:
        return None
    cost_before = plans.summarize_cost(explain(conn, before_text, "json", analyze=False))
    conn.execute("SELECT hypopg_reset()")
    run_script(conn, hypo, params, "hypo")
    hypo_plan = explain(conn, before_text, "json", analyze=False)
    indexes = conn.execute(
        "SELECT index_name, pg_size_pretty(hypopg_relation_size(indexrelid)), "
        "hypopg_relation_size(indexrelid) FROM hypopg_list_indexes"
    ).fetchall()
    used = any(plans.uses_index(hypo_plan, name) for name, _, _ in indexes)
    conn.execute("SELECT hypopg_reset()")
    result = {
        "estimated_cost_before": cost_before,
        "estimated_cost_with_hypothetical": plans.summarize_cost(hypo_plan),
        "planner_uses_hypothetical_index": used,
        "hypothetical_indexes": [{"name": n, "estimated_size": s, "estimated_bytes": b}
                                 for n, s, b in indexes],
    }
    print(f"  hypo: planner cost {result['estimated_cost_before']:,.0f} -> "
          f"{result['estimated_cost_with_hypothetical']:,.0f}, uses index: {used}", flush=True)
    return result


# --------------------------------------------------------------------------- N+1

def _rows_as_dicts(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def measure_n_plus_one(conn, case: Case, params: dict, outdir: Path) -> dict:
    cfg = case.n_plus_one
    parent_t = case.sql(cfg["parent"])
    child_t = case.sql(cfg["child"])
    grand_t = case.sql(cfg["grandchild"]) if cfg.get("grandchild") else None
    cur = client_side(conn)

    def render_page() -> tuple[float, int, list[str]]:
        trips, samples = 0, []
        t = time.perf_counter()
        parent_sql = render(parent_t, params, conn)
        cur.execute(parent_sql)
        trips += 1
        parents = _rows_as_dicts(cur)
        samples.append(parent_sql)
        for p in parents:
            child_sql = render(child_t, {**params, **p}, conn)
            cur.execute(child_sql)
            trips += 1
            children = _rows_as_dicts(cur)
            if len(samples) == 1:
                samples.append(child_sql)
            for c in children if grand_t else []:
                grand_sql = render(grand_t, {**params, **c}, conn)
                cur.execute(grand_sql)
                trips += 1
                cur.fetchall()
                if len(samples) == 2:
                    samples.append(grand_sql)
        return (time.perf_counter() - t) * 1000, trips, samples

    print("  before: rendering the page N+1 style", flush=True)
    for _ in range(case.warmup):
        render_page()
    pgss_reset(conn)
    runs, trips, samples = [], 0, []
    for _ in range(case.repeat):
        ms, trips, samples = render_page()
        runs.append(ms)
    server = pgss_per_execution(conn, case.repeat)

    statements_out = []
    for i, text in enumerate(samples):
        name = ["parent", "child", "grandchild"][i]
        pj = explain(conn, text, "json")
        (outdir / f"before.{name}.plan.json").write_text(json.dumps(pj, indent=2))
        (outdir / f"before.{name}.plan.txt").write_text(explain(conn, text, "text") + "\n")
        (outdir / f"before.{name}.sql").write_text(text + ";\n")
        statements_out.append({"role": name, "plan": plans.summarize(pj)})
    # A combined text file so the report has one "before" plan to show.
    (outdir / "before.plan.txt").write_text("\n\n".join(
        f"-- {s['role']} query (executed per row)\n" + (outdir / f"before.{s['role']}.plan.txt").read_text()
        if s["role"] != "parent" else
        "-- parent query (executed once)\n" + (outdir / "before.parent.plan.txt").read_text()
        for s in statements_out))

    timing = {
        "runs_ms": [round(r, 3) for r in runs],
        "median_ms": round(statistics.median(runs), 3),
        "min_ms": round(min(runs), 3),
        "max_ms": round(max(runs), 3),
        "round_trips": trips,
        "server": server,
    }
    print(f"  before: median {timing['median_ms']:,.2f} ms per page, {trips} round trips", flush=True)
    return {"timing": timing, "plan": statements_out[0]["plan"], "statements": statements_out}


# --------------------------------------------------------------------------- pgbench

LOCK_SAMPLE = """
SELECT count(*) FILTER (WHERE state = 'active'),
       count(*) FILTER (WHERE wait_event_type = 'Lock'),
       count(*) FILTER (WHERE state = 'idle in transaction')
FROM pg_stat_activity WHERE application_name = 'pgbench'
"""


class LockSampler(threading.Thread):
    def __init__(self, interval: float = 0.05):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[tuple[int, int, int]] = []
        self._done = threading.Event()

    def run(self):
        with connect(application_name="casebook-sampler") as conn:
            while not self._done.is_set():
                self.samples.append(conn.execute(LOCK_SAMPLE).fetchone())
                time.sleep(self.interval)

    def stop(self) -> dict:
        self._done.set()
        self.join()
        s = [x for x in self.samples if sum(x) > 0] or [(0, 0, 0)]
        return {
            "samples": len(s),
            "avg_active": round(statistics.mean(x[0] for x in s), 2),
            "avg_waiting_on_lock": round(statistics.mean(x[1] for x in s), 2),
            "avg_idle_in_transaction": round(statistics.mean(x[2] for x in s), 2),
        }


def pgbench(script_text: str, clients: int, threads: int, duration: int, outdir: Path,
            label: str, protocol: str = "simple") -> dict:
    """Run pgbench with a rendered script; returns TPS and latency percentiles."""
    if not shutil.which("pgbench"):
        raise SystemExit("pgbench not found: run this inside the runner container (make run).")
    work = Path(tempfile.mkdtemp(prefix="pgbench-"))
    script = work / f"{label}.pgbench"
    script.write_text(script_text.rstrip() + "\n")
    (outdir / f"{label}.pgbench").write_text(script_text.rstrip() + "\n")
    cmd = ["pgbench", "-n", "-M", protocol, "-c", str(clients), "-j", str(threads),
           "-T", str(duration), "-f", str(script), "--log", f"--log-prefix={work}/txn"]
    sampler = LockSampler()
    sampler.start()
    proc = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    locks = sampler.stop()
    (outdir / f"{label}.pgbench.out").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"pgbench failed:\n{proc.stdout}\n{proc.stderr}")

    lat_us = []
    for f in work.glob("txn*"):
        for line in f.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                lat_us.append(int(parts[2]))
    shutil.rmtree(work, ignore_errors=True)
    lat_us.sort()

    def pct(p):
        return round(lat_us[min(len(lat_us) - 1, int(p * len(lat_us)))] / 1000, 3) if lat_us else None

    tps = float(re.search(r"tps = ([\d.]+)", proc.stdout).group(1))
    failed = re.search(r"number of failed transactions: (\d+)", proc.stdout)
    return {
        "clients": clients, "threads": threads, "duration_s": duration,
        "tps": round(tps, 1),
        "transactions": len(lat_us),
        "failed": int(failed.group(1)) if failed else 0,
        "latency_ms": {"mean": round(statistics.mean(lat_us) / 1000, 3) if lat_us else None,
                       "p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99)},
        "locks": locks,
    }


def measure_pgbench(conn, case: Case, params: dict, outdir: Path, label: str) -> dict:
    cfg = case.pgbench
    text = render(case.sql(cfg[label]), params, conn)
    pgss_reset(conn)
    print(f"  {label}: pgbench -c {cfg.get('clients', 32)} -T {cfg.get('duration', 20)}", flush=True)
    res = pgbench(text, cfg.get("clients", 32), cfg.get("threads", 4), cfg.get("duration", 20),
                  outdir, label)
    res["server"] = pgss_per_execution(conn, res["transactions"])
    print(f"  {label}: {res['tps']:,.0f} TPS, p95 {res['latency_ms']['p95']} ms, "
          f"{res['locks']['avg_waiting_on_lock']} backends waiting on locks on average", flush=True)
    return res


# --------------------------------------------------------------------------- case runner

def environment(conn) -> dict:
    meta = dict(conn.execute("SELECT key, value FROM casebook_meta").fetchall())
    settings = dict(conn.execute("""
        SELECT name, current_setting(name) FROM pg_settings
        WHERE name IN ('shared_buffers', 'work_mem', 'effective_cache_size', 'random_page_cost',
                       'max_parallel_workers_per_gather', 'jit', 'default_statistics_target')
    """).fetchall())
    mem_kb = None
    try:
        mem_kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
    except OSError:
        pass
    return {
        "server_version": scalar(conn, "SHOW server_version"),
        "scale": meta.get("scale"),
        "as_of": meta.get("as_of"),
        "rows": {k[5:]: int(v) for k, v in meta.items() if k.startswith("rows.")},
        "database_size": scalar(conn, "SELECT pg_size_pretty(pg_database_size(current_database()))"),
        "cpus": os.cpu_count(),
        "memory_gb": round(mem_kb / 2**20, 1) if mem_kb else None,
        "settings": settings,
    }


def run_case(case: Case, keep_fixed: bool = False) -> dict:
    outdir = RESULTS / case.slug
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    print(f"\n== {case.number} {case.title}", flush=True)

    with connect() as conn:
        if not scalar(conn, "SELECT to_regclass('casebook_meta') IS NOT NULL"):
            raise SystemExit("Database is not seeded. Run `make seed` first.")
        env = environment(conn)

        if case.sql("setup.sql"):
            took = run_script(conn, case.sql("setup.sql"), {}, "setup")
            print(f"  setup: {took:.1f}s", flush=True)
        params = resolve_params(conn, case)
        if params:
            print(f"  params: {params}", flush=True)

        result: dict = {
            "case": case.number, "slug": case.slug, "title": case.title,
            "category": case.category, "driver": case.driver, "summary": case.summary,
            "params": {k: str(v) for k, v in params.items()},
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "environment": env,
        }

        before_text = render(case.sql(case.before_file), params, conn) if case.file(case.before_file) else None
        if case.driver == "plan":
            result["hypo"] = hypothetical(conn, case, before_text, params)
            result["before"] = measure_statement(conn, case, before_text, outdir, "before")
        elif case.driver == "n_plus_one":
            result["before"] = measure_n_plus_one(conn, case, params, outdir)
        elif case.driver == "pgbench":
            result["before"] = measure_pgbench(conn, case, params, outdir, "before")
        else:
            raise SystemExit(f"unknown driver {case.driver}")

        extras = []
        for extra in case.extras:
            if extra.get("before"):
                text = render(case.sql(extra["before"]), params, conn)
                extras.append({"name": extra["name"], "key": extra["key"], "note": extra.get("note", ""),
                               "before": measure_statement(conn, case, text, outdir,
                                                           f"extra-{extra['key']}-before",
                                                           with_plan=extra.get("explain", True))})

        result["alternatives"] = [measure_alternative(conn, case, alt, params, outdir)
                                  for alt in case.alternatives]

        sizes0 = relation_sizes(conn)
        print("  fix: applying fix.sql", flush=True)
        fix_seconds = run_script(conn, case.sql("fix.sql"), params, "fix")
        sizes1 = relation_sizes(conn)
        result["fix"] = {"seconds": round(fix_seconds, 2), "storage": size_delta(sizes0, sizes1),
                         "sql": case.sql("fix.sql")}

        if case.driver == "pgbench":
            result["after"] = measure_pgbench(conn, case, params, outdir, "after")
        else:
            after_text = render(case.sql(case.after_file), params, conn)
            result["after"] = measure_statement(conn, case, after_text, outdir, "after")

        for extra in case.extras:
            if extra.get("pgbench"):
                text = render(case.sql(extra["pgbench"]), params, conn)
                cfg = case.pgbench
                print(f"  extra-{extra['key']}: pgbench", flush=True)
                res = pgbench(text, cfg.get("clients", 32), cfg.get("threads", 4),
                              cfg.get("duration", 20), outdir, f"extra-{extra['key']}")
                print(f"  extra-{extra['key']}: {res['tps']:,.0f} TPS, p95 {res['latency_ms']['p95']} ms",
                      flush=True)
                extras.append({"name": extra["name"], "key": extra["key"], "note": extra.get("note", ""),
                               "after": res})
                continue
            match = next((e for e in extras if e["name"] == extra["name"]), None)
            if match is None:
                match = {"name": extra["name"], "key": extra["key"], "note": extra.get("note", "")}
                extras.append(match)
            if extra.get("after"):
                text = render(case.sql(extra["after"]), params, conn)
                match["after"] = measure_statement(conn, case, text, outdir,
                                                   f"extra-{extra['key']}-after",
                                                   with_plan=extra.get("explain", True))
        result["extras"] = extras

        if case.sql("teardown.sql") and not keep_fixed:
            took = run_script(conn, case.sql("teardown.sql"), params, "teardown")
            print(f"  teardown: {took:.1f}s", flush=True)

    result["outcome"] = outcome(case, result)
    (outdir / "result.json").write_text(json.dumps(result, indent=2, default=str))
    o = result["outcome"]
    same = {True: ", same result", False: ", RESULT DIFFERS", None: ""}[o["same_result"]]
    print(f"  => {o['metric']}: {o['before']} -> {o['after']}  ({o['speedup']}x{same}, "
          f"{'PASS' if o['passed'] else 'FAIL'} at >= {o['target']}x)", flush=True)
    return result


TARGET_SPEEDUP = 5.0


def outcome(case: Case, r: dict) -> dict:
    if case.driver == "pgbench":
        b, a = r["before"]["tps"], r["after"]["tps"]
        speedup = a / b if b else float("inf")
        metric, fmt = "throughput (TPS)", lambda v: f"{v:,.0f} TPS"
    elif case.driver == "n_plus_one":
        # The cost being fixed is round trips, so the client-side page time is
        # the honest metric here.
        b, a = r["before"]["timing"]["median_ms"], r["after"]["timing"]["median_ms"]
        speedup = b / a if a else float("inf")
        metric, fmt = "page render time (client)", lambda v: f"{v:,.2f} ms"
    else:
        # Server execution time per call from pg_stat_statements: what the
        # database spent, without client and network overhead, which is a
        # fixed ~0.3 ms floor in this setup and would hide sub-ms results.
        b = r["before"]["timing"]["server"]["exec_ms"]
        a = r["after"]["timing"]["server"]["exec_ms"]
        speedup = b / a if a else float("inf")
        metric, fmt = "server execution time", lambda v: f"{v:,.2f} ms"
    same = None
    if case.driver == "plan":
        same = r["before"]["timing"]["result_digest"] == r["after"]["timing"]["result_digest"]
    return {"metric": metric, "before": fmt(b), "after": fmt(a), "before_value": b, "after_value": a,
            "speedup": round(speedup, 1), "target": TARGET_SPEEDUP,
            "same_result": same,
            "passed": speedup >= TARGET_SPEEDUP and same is not False}


# --------------------------------------------------------------------------- generic pgbench harness

def bench_case(case: Case, clients: int, threads: int, duration: int) -> dict:
    """Replay a plan case's before/after statement under concurrency with pgbench."""
    if case.driver != "plan":
        raise SystemExit(f"{case.slug}: bench applies to single-statement cases only")
    outdir = RESULTS / case.slug
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n== bench {case.number} {case.title}", flush=True)
    with connect() as conn:
        if case.sql("setup.sql"):
            run_script(conn, case.sql("setup.sql"), {}, "setup")
        params = resolve_params(conn, case)
        before = render(case.sql(case.before_file), params, conn) + ";"
        res = {"before": pgbench(before, clients, threads, duration, outdir, "bench-before")}
        run_script(conn, case.sql("fix.sql"), params, "fix")
        after = render(case.sql(case.after_file), params, conn) + ";"
        res["after"] = pgbench(after, clients, threads, duration, outdir, "bench-after")
        if case.sql("teardown.sql"):
            run_script(conn, case.sql("teardown.sql"), params, "teardown")
    for k in ("before", "after"):
        print(f"  {k}: {res[k]['tps']:,.1f} TPS, p50 {res[k]['latency_ms']['p50']} ms, "
              f"p99 {res[k]['latency_ms']['p99']} ms", flush=True)
    (outdir / "bench.json").write_text(json.dumps(res, indent=2))
    return res
