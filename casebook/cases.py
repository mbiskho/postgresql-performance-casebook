"""Case definitions on disk.

Each directory under cases/ is one case:

    case.toml       metadata, driver, parameters, extras
    README.md       symptom, diagnosis, fix, trade-offs (the prose)
    setup.sql       optional: put the database into the broken state (idempotent)
    before.sql      the slow statement
    fix.sql         the change (DDL, statistics, schema, rollup...)
    after.sql       optional: the statement after the fix (defaults to before.sql)
    teardown.sql    optional: undo fix.sql so the case can be rerun
    hypo.sql        optional: HypoPG what-if, run before the real fix

[[alternative]] entries in case.toml name other fixes that were considered.
Each is applied, measured with the after statement, and rolled back before
the chosen fix, so the report can show why it was not the one chosen.

SQL files use {{name}} placeholders, filled from [params] in case.toml. Each
param is a SQL query returning one value, resolved after setup.sql, so values
like "a customer with a typical number of orders" adapt to the seed scale.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .db import ROOT

CASES_DIR = ROOT / "cases"


@dataclass
class Case:
    path: Path
    slug: str
    number: str
    title: str
    category: str
    driver: str
    summary: str
    repeat: int
    warmup: int
    timeout_s: int
    params: dict[str, str]
    extras: list[dict] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    n_plus_one: dict = field(default_factory=dict)
    pgbench: dict = field(default_factory=dict)

    def file(self, name: str) -> Path | None:
        p = self.path / name
        return p if p.exists() else None

    def sql(self, name: str) -> str | None:
        p = self.file(name)
        return p.read_text() if p else None

    @property
    def before_file(self) -> str:
        return "before.sql"

    @property
    def after_file(self) -> str:
        return "after.sql" if self.file("after.sql") else "before.sql"

    @property
    def readme(self) -> str:
        p = self.file("README.md")
        return p.read_text() if p else ""


def load(path: Path) -> Case:
    meta = tomllib.loads((path / "case.toml").read_text())
    number, _, slug_tail = path.name.partition("-")
    return Case(
        path=path,
        slug=path.name,
        number=number,
        title=meta["title"],
        category=meta.get("category", ""),
        driver=meta.get("driver", "plan"),
        summary=meta.get("summary", ""),
        repeat=meta.get("repeat", 7),
        warmup=meta.get("warmup", 2),
        timeout_s=meta.get("timeout_s", 300),
        params=meta.get("params", {}),
        extras=meta.get("extra", []),
        alternatives=meta.get("alternative", []),
        n_plus_one=meta.get("n_plus_one", {}),
        pgbench=meta.get("pgbench", {}),
    )


def all_cases() -> list[Case]:
    return [load(p) for p in sorted(CASES_DIR.iterdir()) if (p / "case.toml").exists()]


def select(names: list[str]) -> list[Case]:
    cases = all_cases()
    if not names:
        return cases
    chosen = []
    for name in names:
        matches = [c for c in cases if c.slug == name or c.number == name.zfill(2)
                   or c.slug.startswith(name)]
        if not matches:
            raise SystemExit(f"No case matches {name!r}. Try `casebook list`.")
        chosen.extend(m for m in matches if m not in chosen)
    return chosen
