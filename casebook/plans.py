"""Reading EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) output.

The numbers the casebook reports per plan:

* execution and planning time as PostgreSQL measured them;
* shared buffer hits and reads, temp blocks, I/O wait (track_io_timing);
* the node types in the tree, so a reader can see "Seq Scan" turn into
  "Index Only Scan" without opening the plan;
* planner estimate accuracy as a q-error per node: max(est/actual, actual/est),
  with both sides clamped to at least one row. 1.0 is a perfect estimate; the
  worst node is usually where the planner went wrong.
"""

from __future__ import annotations

from typing import Any, Iterator

BLOCK_BYTES = 8192


def _walk(node: dict, depth: int = 0) -> Iterator[tuple[int, dict]]:
    yield depth, node
    for child in node.get("Plans", []) or []:
        yield from _walk(child, depth + 1)


def _label(node: dict) -> str:
    parts = [node.get("Node Type", "?")]
    if node.get("Strategy") and node["Node Type"] in ("Aggregate", "SetOp"):
        parts.insert(0, node["Strategy"])
    if "Index Name" in node:
        parts.append(f"using {node['Index Name']}")
    if "Relation Name" in node:
        parts.append(f"on {node['Relation Name']}")
    if node.get("Scan Direction") == "Backward":
        parts.insert(1, "Backward")
    return " ".join(parts)


def q_error(estimated: float, actual: float) -> float:
    e, a = max(estimated, 1.0), max(actual, 1.0)
    return max(e / a, a / e)


def summarize(explain_json: Any) -> dict:
    """Condense one EXPLAIN JSON document into the numbers the report uses."""
    doc = explain_json[0] if isinstance(explain_json, list) else explain_json
    root = doc["Plan"]

    nodes, worst = [], None
    for depth, node in _walk(root):
        loops = node.get("Actual Loops", 0) or 0
        entry = {
            "depth": depth,
            "label": _label(node),
            "estimated_rows": node.get("Plan Rows"),
            "actual_rows": node.get("Actual Rows"),
            "loops": loops,
        }
        if loops:
            qe = q_error(node["Plan Rows"], node["Actual Rows"])
            entry["q_error"] = round(qe, 2)
            # Ignore nodes below a LIMIT that stopped early: their estimate is
            # for the full scan, the actual count is truncated by design.
            if not node.get("_under_limit") and (worst is None or qe > worst["q_error"]):
                worst = {"label": entry["label"], "estimated_rows": node["Plan Rows"],
                         "actual_rows": node["Actual Rows"], "loops": loops,
                         "q_error": round(qe, 2)}
        else:
            entry["never_executed"] = True
        nodes.append(entry)
        if node.get("Node Type") == "Limit":
            for _, below in _walk(node):
                if below is not node:
                    below["_under_limit"] = True

    # Rows a node read and then threw away: the clearest single sign of work a
    # better access path would not have done.
    removed = sum(
        (n.get("Rows Removed by Filter", 0) + n.get("Rows Removed by Index Recheck", 0)
         + n.get("Rows Removed by Join Filter", 0)) * max(n.get("Actual Loops", 1) or 1, 1)
        for _, n in _walk(root)
    )

    shared_hit = root.get("Shared Hit Blocks", 0)
    shared_read = root.get("Shared Read Blocks", 0)
    return {
        "rows_removed": int(removed),
        "execution_ms": round(doc.get("Execution Time", 0.0), 3),
        "planning_ms": round(doc.get("Planning Time", 0.0), 3),
        "rows": root.get("Actual Rows"),
        "total_cost": root.get("Total Cost"),
        "shared_hit_blocks": shared_hit,
        "shared_read_blocks": shared_read,
        "buffers_touched_mb": round((shared_hit + shared_read) * BLOCK_BYTES / 2**20, 2),
        "temp_read_blocks": root.get("Temp Read Blocks", 0),
        "temp_written_blocks": root.get("Temp Written Blocks", 0),
        "io_read_ms": round(root.get("I/O Read Time", 0.0), 3),
        "root_q_error": round(q_error(root.get("Plan Rows", 1), root.get("Actual Rows", 1)), 2),
        "worst_estimate": worst,
        "node_types": sorted({n["Node Type"] for _, n in _walk(root)}),
        "nodes": nodes,
        "workers_launched": max((n.get("Workers Launched", 0) for _, n in _walk(root)), default=0),
    }


def summarize_cost(explain_json: Any) -> float:
    doc = explain_json[0] if isinstance(explain_json, list) else explain_json
    return float(doc["Plan"]["Total Cost"])


def uses_index(explain_json: Any, index_fragment: str) -> bool:
    doc = explain_json[0] if isinstance(explain_json, list) else explain_json
    return any(index_fragment in n.get("Index Name", "") for _, n in _walk(doc["Plan"]))


def tree_text(summary: dict, limit: int = 40) -> str:
    """Compact one-line-per-node outline used in reports."""
    lines = []
    for n in summary["nodes"][:limit]:
        if n.get("never_executed"):
            rows = "never executed"
        else:
            rows = f"est {n['estimated_rows']:,} / actual {n['actual_rows']:,}"
            if n["loops"] > 1:
                rows += f" x {n['loops']:,} loops"
        lines.append("  " * n["depth"] + f"{n['label']}  ({rows})")
    if len(summary["nodes"]) > limit:
        lines.append(f"... {len(summary['nodes']) - limit} more nodes")
    return "\n".join(lines)
