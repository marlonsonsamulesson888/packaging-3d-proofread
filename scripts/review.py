"""Local packaging review records. Does not inspect pixels or certify artwork."""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

GATES = ("orientation", "placement", "copy", "integrity", "geometry", "protected")
DEFAULT_LIBRARY = ""  # Optional, explicitly supplied by the user


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def local_path(value, base, must_exist=True):
    require(nonblank(value), "Path must be a nonempty local path")
    normalized = value.replace("\\", "/")
    require(not normalized.startswith("//"), "Network paths are not accepted")
    require(not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value), "URLs are not accepted")
    p = Path(value).expanduser()
    p = (p if p.is_absolute() else base / p).resolve()
    require(not str(p).replace("\\", "/").startswith("//"), "Resolved network path")
    if must_exist:
        require(p.is_file(), f"Missing local file: {p}")
    return p


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    require(path.stat().st_size <= 8 * 1024 * 1024, "JSON exceeds 8 MiB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, data):
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def indexed(items, label):
    require(isinstance(items, list) and items, f"{label} must be a nonempty array")
    result = {}
    for item in items:
        require(isinstance(item, dict), f"Invalid {label} entry")
        id_ = item.get("id")
        require(isinstance(id_, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", id_),
                f"Invalid {label} id")
        require(id_ not in result, f"Duplicate {label} id: {id_}")
        result[id_] = item
    return result


def string_list(value, label, allow_empty=True):
    require(isinstance(value, list) and all(nonblank(x) for x in value), f"Invalid {label}")
    require(len(value) == len(set(value)), f"Duplicate {label}")
    require(allow_empty or bool(value), f"Empty {label}")
    return value


def expected_checks(plan):
    checks = []
    for view in plan["views"]:
        for panel in view["panels"]:
            for gate in GATES:
                checks.append((f"{view['id']}:{panel}:{gate}", view["id"], panel, gate))
    checks.append(("cross_view_consistency", None, None, "cross_view_consistency"))
    return checks


def validate_plan(plan):
    require(plan.get("schema_version") == 1, "Unsupported plan version")
    require(nonblank(plan.get("job_id")), "Missing job_id")
    masters = indexed(plan.get("masters"), "master")
    panels = indexed(plan.get("panels"), "panel")
    views = indexed(plan.get("views"), "view")
    string_list(plan.get("protected"), "protected")
    for panel in panels.values():
        require(panel.get("master_id") in masters, "Unknown panel master")
        require(panel.get("baseline_status") in ("confirmed", "unknown"), "Invalid baseline status")
        for field in ("reading_direction", "placement_rule"):
            require(nonblank(panel.get(field)), f"Missing panel {field}; use unknown if unresolved")
        if panel["baseline_status"] == "confirmed":
            require(nonblank(panel.get("baseline_evidence")), "Confirmed baseline needs evidence")
    for view in views.values():
        vp = string_list(view.get("panels"), "view panels", False)
        vm = string_list(view.get("master_ids"), "view master_ids", False)
        require(set(vp) <= set(panels), "Unknown view panel")
        require(set(vm) <= set(masters), "Unknown view master")
        require({panels[p]["master_id"] for p in vp} <= set(vm), "Missing view master binding")
    for item in list(masters.values()) + list(views.values()):
        require(nonblank(item.get("path")), "Missing asset path")
        require(re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")), "Missing asset SHA-256")
    return masters, panels, views


def prepare(spec_path, out):
    spec_path = local_path(str(spec_path), Path.cwd())
    spec = load_json(spec_path)
    require(isinstance(spec, dict), "Spec must be an object")
    base = spec_path.parent
    plan = {"schema_version": 1, "job_id": spec.get("job_id"),
            "protected": spec.get("protected", []), "panels": spec.get("panels")}
    for name in ("masters", "views"):
        items = indexed(spec.get(name), name)
        plan[name] = []
        for item in items.values():
            p = local_path(item.get("path"), base)
            new = dict(item, path=str(p), sha256=digest(p))
            plan[name].append(new)
    validate_plan(plan)
    ids = string_list(spec.get("error_ids", []), "error_ids")
    warnings = []
    history = {"selected": [], "source": None}
    library_value = spec.get("library_path", DEFAULT_LIBRARY)
    try:
        library = local_path(library_value, base)
        records = load_json(library)
        require(isinstance(records, list), "Library must be an array")
        lookup = {}
        for row in records:
            require(isinstance(row, dict) and nonblank(row.get("id")), "Invalid history record")
            require(row["id"] not in lookup, "Duplicate history ID")
            lookup[row["id"]] = row
        require(set(ids) <= set(lookup), "Requested error ID missing from library")
        history["selected"] = [lookup[id_] for id_ in ids]
        history["source"] = {"path": str(library), "sha256": digest(library)}
    except (OSError, ValueError) as exc:
        if ids:
            raise ValueError(f"Cannot load requested historical cases: {exc}") from exc
        warnings.append(f"Historical library not loaded: {exc}")
    plan["warnings"] = warnings
    plan["history_ids"] = ids
    plan["history_source"] = history["source"] if ids else None
    plan["spec_source"] = {"path": str(spec_path), "sha256": digest(spec_path)}
    out = local_path(str(out), Path.cwd(), must_exist=False)
    require(not out.exists(), "Output already exists; choose a new directory")
    out.mkdir(parents=True)
    write_json(out / "plan.json", plan)
    write_json(out / "history.json", history)
    review = {"schema_version": 1, "plan": "plan.json", "plan_sha256": digest(out / "plan.json"),
              "checks": [{"id": id_, "view_id": v, "panel_id": p, "gate": g,
                          "status": "unverified", "reviewer": "", "reviewed_at": "",
                          "observation": "", "evidence": []}
                         for id_, v, p, g in expected_checks(plan)]}
    write_json(out / "review.json", review)
    lines = ["# 包装3D图文复查", "", f"订单：{plan['job_id']}",
             "所有项目初始未检查。脚本不查看图像，不证明视觉正确。", ""]
    for panel in plan["panels"]:
        lines += [f"面板 {panel['id']}：母版 {panel['master_id']}；基准 {panel['baseline_status']}",
                  f"- 阅读方向：{panel['reading_direction']}",
                  f"- 位置规则：{panel['placement_rule']}"]
    lines += ["", "历史相关编号：" + (", ".join(ids) if ids else "未指定"), ""]
    lines += [f"- [ ] {id_}" for id_, _, _, _ in expected_checks(plan)]
    if warnings:
        lines += ["", "## 限制"] + warnings
    (out / "checklist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"output": str(out), "checks": len(review["checks"]), "warnings": warnings,
            "visual_verdict": "not_evaluated"}


def check(review_path):
    review_path = local_path(str(review_path), Path.cwd())
    review = load_json(review_path)
    require(isinstance(review, dict) and review.get("schema_version") == 1, "Invalid review")
    base = review_path.parent
    plan_path = local_path(review.get("plan"), base)
    require(digest(plan_path) == review.get("plan_sha256"), "Plan hash changed")
    plan = load_json(plan_path)
    masters, panels, views = validate_plan(plan)
    for asset in list(masters.values()) + list(views.values()):
        p = local_path(asset["path"], plan_path.parent)
        require(digest(p) == asset["sha256"], f"Asset changed: {asset['id']}")
    if plan.get("history_source"):
        src = plan["history_source"]
        p = local_path(src["path"], plan_path.parent)
        require(digest(p) == src["sha256"], "Historical library changed; reconcile before reuse")
    checks = review.get("checks")
    require(isinstance(checks, list), "Checks must be an array")
    by_id = {}
    for row in checks:
        require(isinstance(row, dict) and nonblank(row.get("id")), "Invalid check")
        require(row["id"] not in by_id, "Duplicate check")
        by_id[row["id"]] = row
    expected = expected_checks(plan)
    require(set(by_id) == {x[0] for x in expected}, "Missing or unexpected checks")
    pending, failed = [], []
    for id_, v, p, g in expected:
        row = by_id[id_]
        require((row.get("view_id"), row.get("panel_id"), row.get("gate")) == (v, p, g),
                f"Changed check binding: {id_}")
        status = row.get("status")
        require(status in ("pass", "fail", "unverified", "na"), f"Unknown status: {id_}")
        if status == "unverified":
            pending.append(id_)
            continue
        require(nonblank(row.get("reviewer")) and nonblank(row.get("observation")),
                f"Reviewer and actual observation required: {id_}")
        stamp = row.get("reviewed_at")
        require(nonblank(stamp), f"Review timestamp missing: {id_}")
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        require(dt.tzinfo is not None, f"Timestamp timezone missing: {id_}")
        if status == "na":
            require(g in ("copy", "protected"), f"Core gate cannot be skipped: {id_}")
            continue
        ev = row.get("evidence")
        require(isinstance(ev, list) and ev, f"Evidence required: {id_}")
        for e in ev:
            require(isinstance(e, dict), f"Invalid evidence: {id_}")
            file = local_path(e.get("path"), base)
            require(digest(file) == e.get("sha256"), f"Evidence missing/changed: {id_}")
        if status == "fail":
            failed.append(id_)
    unknown = [p["id"] for p in panels.values()
               if p["baseline_status"] != "confirmed"
               or p["reading_direction"].strip().lower() in ("unknown", "未知", "待确认")
               or p["placement_rule"].strip().lower() in ("unknown", "未知", "待确认")]
    complete = not pending and not failed and not unknown
    return {"record_status": "complete" if complete else "needs_review",
            "unverified_checks": pending, "failed_checks": failed, "unknown_panels": unknown,
            "warnings": plan.get("warnings", []), "visual_verdict": "not_automatically_evaluated",
            "production_approval": False}, 0 if complete else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check")
    c.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result, code = prepare(args.spec, args.out), 0
        else:
            result, code = check(args.review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "visual_verdict": "not_evaluated"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())