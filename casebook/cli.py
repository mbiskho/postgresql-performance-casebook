"""PostgreSQL Performance Casebook: seed the dataset, run cases, build reports."""

from __future__ import annotations

import argparse
import json

from . import cases as casemod
from . import measure, report, seed
from .db import connect, scalar


def cmd_list(_args) -> None:
    for c in casemod.all_cases():
        done = (measure.RESULTS / c.slug / "result.json").exists()
        print(f"{c.number}  {c.slug:<40} {c.category:<22} {c.driver:<10} {'measured' if done else ''}")


def cmd_check(_args) -> None:
    with connect() as conn:
        print("server     ", scalar(conn, "SHOW server_version"))
        exts = [r[0] for r in conn.execute("SELECT extname FROM pg_extension ORDER BY 1").fetchall()]
        print("extensions ", ", ".join(exts))
        preload = scalar(conn, "SHOW shared_preload_libraries")
        print("preloaded  ", preload)
        missing = {"pg_stat_statements", "hypopg", "pg_trgm"} - set(exts)
        if missing or "auto_explain" not in preload:
            raise SystemExit(f"environment incomplete: missing {sorted(missing)} / auto_explain")
        seeded = seed.is_seeded(conn)
        if seeded:
            rows = {k[5:]: int(v) for k, v in seeded.items() if k.startswith("rows.")}
            print("seed       ", f"scale {seeded['scale']}, {sum(rows.values()):,} rows")
        else:
            print("seed        not seeded (make seed)")


def cmd_run(args) -> None:
    selected = casemod.select(args.cases)
    results = []
    for c in selected:
        results.append(measure.run_case(c, keep_fixed=args.keep_fixed))
        if args.bench and c.driver == "plan":
            measure.bench_case(c, args.clients, args.threads, args.duration)
    report.build()
    print()
    for r in results:
        o = r["outcome"]
        print(f"{r['case']}  {r['title']:<48} {o['speedup']:>8}x  {'PASS' if o['passed'] else 'FAIL'}")
    failed = [r["case"] for r in results if not r["outcome"]["passed"]]
    if failed and args.strict:
        raise SystemExit(f"cases below target: {', '.join(failed)}")


def cmd_bench(args) -> None:
    for c in casemod.select(args.cases):
        if c.driver == "plan":
            measure.bench_case(c, args.clients, args.threads, args.duration)
    report.build()


def cmd_report(_args) -> None:
    out = report.build()
    print(json.dumps(out, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casebook", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="generate the dataset")
    p.add_argument("--scale", type=float, default=1.0,
                   help="1.0 = ~64M rows; 0.1 is handy while editing cases")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--reset", action="store_true", help="drop and rebuild an existing seed")
    p.set_defaults(func=lambda a: seed.run(a.scale, workers=a.workers, reset=a.reset))

    sub.add_parser("list", help="list cases").set_defaults(func=cmd_list)
    sub.add_parser("check", help="verify extensions, settings and seed").set_defaults(func=cmd_check)

    def bench_opts(p):
        p.add_argument("--clients", type=int, default=8)
        p.add_argument("--threads", type=int, default=4)
        p.add_argument("--duration", type=int, default=30, help="seconds per side")

    p = sub.add_parser("run", help="run cases (all by default) and rebuild the report")
    p.add_argument("cases", nargs="*", help="case numbers or slugs, e.g. 01 05 or stale")
    p.add_argument("--keep-fixed", action="store_true", help="skip teardown; leave the fix applied")
    p.add_argument("--bench", action="store_true", help="also replay each case under pgbench")
    p.add_argument("--strict", action="store_true", help="exit non-zero if any case misses 5x")
    bench_opts(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("bench", help="replay before/after statements under concurrency with pgbench")
    p.add_argument("cases", nargs="*")
    bench_opts(p)
    p.set_defaults(func=cmd_bench)

    sub.add_parser("report", help="rebuild reports/casebook.md and index.html from results/") \
        .set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)
    return 0
