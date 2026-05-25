#!/usr/bin/env python3
"""project-state-spec scaffold script.

Owns all side effects for the project-state-spec skill:
  - ID allocation (R-NNN, D-NNN, LP-NNN, TP-NNN, Plan.<topic>)
  - File writes under PST conventions (research/, decisions/, plan/, prompts/)
  - approved_transitions.json construction
  - tools/apply_changes.py invocation

Usage: see argparse help.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

# Make sibling _spec_helpers importable when the script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spec_helpers import (  # noqa: E402
    FileExistsRefuseError,
    StatusYamlMissingError,
    ensure_group_exists,
    find_artifact_by_topic,
    load_status,
    next_id,
    safe_write,
    set_source_root,
    slugify,
    today_iso,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _topic_title(topic: str) -> str:
    """Convert kebab-case slug to Title Case for display."""
    return " ".join(word.capitalize() for word in topic.split("-"))


def _write_transitions_and_apply(
    pst_root: Path,
    transitions: list[dict],
    event_summary: str,
    event_type: str,
) -> None:
    """Write approved_transitions.json then invoke apply_changes.py.

    Raises subprocess.CalledProcessError on apply_changes failure.
    """
    cache_dir = pst_root / "status" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_summary": event_summary,
        "event_type": event_type,
        "transitions": transitions,
    }
    (cache_dir / "approved_transitions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    apply_script = pst_root / "tools" / "apply_changes.py"
    if not apply_script.is_file():
        raise FileNotFoundError(
            f"apply_changes.py not found at {apply_script}. "
            "Run PST INIT to create the tools/ directory."
        )
    subprocess.run(
        [sys.executable, str(apply_script), "--project", str(pst_root)],
        check=True,
    )


def _apply_patch(pst_root: Path, upd: dict) -> None:
    """In-place patch of LP file sections based on patch_sections dict."""
    import re as _re_p
    lp_path = pst_root / upd["lp_path"]
    if not lp_path.is_file():
        raise FileNotFoundError(f"LP file not found: {lp_path}")
    content = lp_path.read_text(encoding="utf-8")

    heading_map = {
        "acceptance_gates": "# Acceptance Gates",
        "steps": "# Steps",
        "allowed_files": "# Allowed Files",
        "handoff_plan": "# Handoff Plan",
    }

    for section_name, new_content in (upd.get("patch_sections") or {}).items():
        heading = heading_map.get(section_name)
        if not heading:
            continue
        escaped = _re_p.escape(heading)
        pattern = rf"({escaped}\s*\n)(.*?)(?=\n# |\Z)"
        replacement = r"\g<1>" + new_content + "\n"
        content, count = _re_p.subn(pattern, replacement, content, flags=_re_p.DOTALL)
        if count == 0:
            content = content.rstrip() + f"\n\n{heading}\n{new_content}\n"

    safe_write(lp_path, content, force=True)


def _next_version_suffix(pst_root: Path, lp_path: str) -> str:
    """Determine next version suffix by scanning existing versioned files."""
    import re as _re_v
    base_path = pst_root / lp_path
    stem = base_path.stem
    base_stem = _re_v.sub(r"_v\d+$", "", stem)
    parent = base_path.parent

    max_version = 1
    if parent.is_dir():
        for f in parent.glob(f"{base_stem}_v*.md"):
            m = _re_v.search(r"_v(\d+)\.md$", f.name)
            if m:
                max_version = max(max_version, int(m.group(1)))

    return f"v{max_version + 1}"


def _find_tp_for_lp(status: dict, lp_id: str) -> dict | None:
    """Find the TestPrompt artifact that depends_on the given LP id."""
    for art in status.get("artifacts", []) or []:
        if not isinstance(art, dict):
            continue
        if art.get("type") == "test_prompt" and lp_id in (art.get("depends_on") or []):
            return art
    return None


def _apply_rewrite(pst_root: Path, status: dict, upd: dict, topic: str) -> dict:
    """Generate versioned LP file, deprecate old one. Returns transitions + new IDs."""
    import re as _re_rw

    old_lp_id = upd["lp_id"]
    version_suffix = upd.get("version_suffix") or _next_version_suffix(pst_root, upd["lp_path"])
    old_path = pst_root / upd["lp_path"]

    old_stem = old_path.stem
    base_stem = _re_rw.sub(r"_v\d+$", "", old_stem)
    new_stem = f"{base_stem}_{version_suffix}"
    new_path = old_path.parent / f"{new_stem}.md"
    new_path_rel = str(new_path.relative_to(pst_root)).replace("\\", "/")

    new_lp_body = Path(upd["new_lp_content"]).read_text(encoding="utf-8")

    new_lp_id = next_id(status, "LP")

    validates_ac = upd.get("validates_ac", [])
    validates_property = upd.get("validates_property", [])
    meta_block = (
        f"<!-- validates_ac: {validates_ac} -->\n"
        f"<!-- validates_property: {validates_property} -->\n"
    )
    new_full = f"# {new_lp_id}: {new_stem}\n\n{meta_block}\n{new_lp_body.lstrip()}"
    safe_write(new_path, new_full, force=True)

    old_art = next(
        (a for a in status.get("artifacts", [])
         if isinstance(a, dict) and a.get("id") == old_lp_id),
        {}
    )
    inherited_deps = old_art.get("depends_on", [])
    from_status = old_art.get("status", "needs_update")

    transitions = [
        {
            "artifact": old_lp_id,
            "type": "landing_prompt",
            "from": from_status,
            "to": "deprecated",
            "reason": f"Superseded by {new_lp_id} ({new_stem}, AC rewrite for {topic})",
            "source": "project-state-spec",
        },
        {
            "artifact": new_lp_id,
            "new_file": True,
            "proposed_id": new_lp_id,
            "type": "landing_prompt",
            "from": None,
            "to": "draft",
            "path": new_path_rel,
            "depends_on": inherited_deps,
            "supersedes": old_lp_id,
            "reason": f"PSS update rewrite: {new_stem} for {topic}",
            "source": "project-state-spec",
        },
    ]

    if "new_tp_content" in upd:
        old_tp = _find_tp_for_lp(status, old_lp_id)
        if old_tp:
            tp_body = Path(upd["new_tp_content"]).read_text(encoding="utf-8")
            new_tp_id = next_id(status, "TP")
            old_tp_path = pst_root / old_tp["path"]
            tp_base_stem = _re_rw.sub(r"_v\d+$", "", old_tp_path.stem)
            new_tp_stem = f"{tp_base_stem}_{version_suffix}"
            new_tp_path = old_tp_path.parent / f"{new_tp_stem}.md"
            new_tp_path_rel = str(new_tp_path.relative_to(pst_root)).replace("\\", "/")
            safe_write(new_tp_path, tp_body, force=True)
            transitions.append({
                "artifact": old_tp["id"],
                "type": "test_prompt",
                "from": old_tp.get("status"),
                "to": "deprecated",
                "reason": f"Superseded by {new_tp_id} ({new_tp_stem}, AC rewrite)",
                "source": "project-state-spec",
            })
            transitions.append({
                "artifact": new_tp_id,
                "new_file": True,
                "proposed_id": new_tp_id,
                "type": "test_prompt",
                "from": None,
                "to": "draft",
                "path": new_tp_path_rel,
                "depends_on": [new_lp_id],
                "reason": f"PSS update rewrite: {new_tp_stem} for {topic}",
                "source": "project-state-spec",
            })

    return {
        "transitions": transitions,
        "new_lp_id": new_lp_id,
        "new_lp_path": new_path_rel,
    }


def _update_lp_sequence(pst_root: Path, manifest: dict, updated_pairs: list) -> None:
    """Update LP sequence in README if lp_sequence_source is 'auto'.

    For rewrites: replace old token with new token.
    For new_lps: insert after the specified token.
    """
    readme_path = pst_root / "prompts" / "landing" / "README.md"
    if not readme_path.is_file():
        return

    content = readme_path.read_text(encoding="utf-8")

    # Check lp_sequence_source — if "user", don't touch
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            front_matter = content[:end]
            if 'lp_sequence_source:' in front_matter and '"user"' in front_matter:
                return

    # Apply token replacements for rewrites
    for pair in updated_pairs:
        if pair.get("mode") == "rewrite" and pair.get("new_lp_path"):
            from pathlib import PurePosixPath
            old_stem = PurePosixPath(pair.get("lp_path", pair.get("lp_id", ""))).stem
            new_stem = PurePosixPath(pair["new_lp_path"]).stem
            if old_stem and new_stem and old_stem != new_stem:
                content = content.replace(old_stem, new_stem)

    # Insert new LP tokens after specified position
    for new_lp in manifest.get("new_lps", []):
        insert_after = new_lp.get("insert_after", "")
        new_pair = next(
            (p for p in updated_pairs
             if p.get("mode") == "new" and p.get("slug") == slugify(new_lp["slug"])),
            None
        )
        if not new_pair or not insert_after:
            continue
        new_token = f"{new_pair['lp_id']}-{new_pair.get('slug', '')}"
        if insert_after in content:
            content = content.replace(insert_after, f"{insert_after} -> {new_token}")

    safe_write(readme_path, content, force=True)


# ---------------------------------------------------------------------------
# Stage handlers
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    pst_root = Path(args.pst_root)
    try:
        status = load_status(pst_root)
    except StatusYamlMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    r = find_artifact_by_topic(status, args.topic, "research_finding")
    d = find_artifact_by_topic(status, args.topic, "decision")
    p = find_artifact_by_topic(status, args.topic, "plan")

    lp_count = 0
    tp_count = 0
    if p is not None:
        plan_id = p["id"]
        for entry in status.get("artifacts", []) or []:
            if not isinstance(entry, dict):
                continue
            depends = entry.get("depends_on") or []
            if entry.get("type") == "landing_prompt" and plan_id in depends:
                lp_count += 1
            if entry.get("type") == "test_prompt":
                # TP depends on its LP; check whether ANY LP under this plan is in chain.
                for dep in depends:
                    parent = next(
                        (a for a in status.get("artifacts", []) or []
                         if isinstance(a, dict) and a.get("id") == dep
                         and a.get("type") == "landing_prompt"
                         and plan_id in (a.get("depends_on") or [])),
                        None,
                    )
                    if parent is not None:
                        tp_count += 1
                        break

    req_complete = r is not None and d is not None
    design_complete = req_complete and p is not None
    tasks_complete = (
        design_complete and lp_count > 0 and lp_count == tp_count
    )

    if not req_complete:
        next_stage = "requirement"
    elif not design_complete:
        next_stage = "design"
    elif not tasks_complete:
        next_stage = "tasks"
    else:
        next_stage = "done"

    out = {
        "topic": args.topic,
        "stages": {
            "requirement": {
                "complete": req_complete,
                "r_id": r["id"] if r else None,
                "d_id": d["id"] if d else None,
            },
            "design": {
                "complete": design_complete,
                "plan_id": p["id"] if p else None,
            },
            "tasks": {
                "complete": tasks_complete,
                "lp_count": lp_count,
                "tp_count": tp_count,
            },
        },
        "next_stage": next_stage,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_requirement(args: argparse.Namespace) -> int:
    if not args.r_content or not args.d_content:
        print("ERROR: --stage requirement requires --r-content and --d-content",
              file=sys.stderr)
        return 2

    pst_root = Path(args.pst_root)
    try:
        status = load_status(pst_root)
    except StatusYamlMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # G4 fix: enforce source_root presence at the FIRST stage (requirement) so users
    # discover the misconfiguration immediately rather than after walking three stages
    # and then having Execute-LandingPrompt refuse to run. Acceptable inputs:
    #   - meta.source_root already set to a non-placeholder path, OR
    #   - this invocation passed --source-root <existing path> (set_source_root
    #     in main() already persisted it before we reach here).
    meta = status.get("meta") if isinstance(status, dict) else {}
    meta = meta or {}
    existing_root = (meta.get("source_root") or "").strip()
    invalid_placeholders = {"", "<未配置>", "<unset>", "TBD"}
    if existing_root in invalid_placeholders:
        print(
            "ERROR: meta.source_root is missing or a placeholder, and no "
            "--source-root was supplied for this invocation. Stage 1 (requirement) "
            "MUST establish source_root so Execute-LandingPrompt can later locate "
            "the codebase. Re-run with `--source-root <absolute path>`.",
            file=sys.stderr,
        )
        return 2

    topic = slugify(args.topic)

    # Reuse existing ids if topic already has artifacts so safe_write detects
    # the collision and refuses (Property: idempotent without --force).
    existing_r = find_artifact_by_topic(status, topic, "research_finding")
    existing_d = find_artifact_by_topic(status, topic, "decision")
    r_id = existing_r["id"] if existing_r else next_id(status, "R")
    d_id = existing_d["id"] if existing_d else next_id(status, "D")
    title = _topic_title(topic)

    r_path_rel = f"research/{r_id}-{topic}.md"
    d_path_rel = f"decisions/{d_id}-{topic}.yaml"
    r_path = pst_root / r_path_rel
    d_path = pst_root / d_path_rel

    r_body = Path(args.r_content).read_text(encoding="utf-8")
    d_body = Path(args.d_content).read_text(encoding="utf-8")

    r_full = f"# {r_id}: {title}\n\n{r_body.lstrip()}"
    d_full = (
        f"id: {d_id}\n"
        f"title: \"{title}\"\n"
        f"status: draft\n"
        f"based_on: [{r_id}]\n"
        f"{d_body.lstrip()}"
    )

    try:
        safe_write(r_path, r_full, force=args.force)
        safe_write(d_path, d_full, force=args.force)
    except FileExistsRefuseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    transitions = [
        {"artifact": r_id, "new_file": True, "proposed_id": r_id,
         "type": "research_finding", "title": title,
         "from": None, "to": "draft",
         "path": r_path_rel,
         "reason": f"project-state-spec scaffold: requirement stage for {topic}",
         "source": "project-state-spec"},
        {"artifact": d_id, "new_file": True, "proposed_id": d_id,
         "type": "decision", "title": title,
         "from": None, "to": "draft",
         "path": d_path_rel,
         "based_on": [r_id],
         "depends_on": [r_id],
         "reason": f"project-state-spec scaffold: requirement stage for {topic}",
         "source": "project-state-spec"},
    ]
    try:
        _write_transitions_and_apply(
            pst_root,
            transitions,
            event_summary=f"project-state-spec scaffold: requirement stage for {topic}",
            event_type="spec_scaffold",
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: apply_changes.py failed (exit {exc.returncode}): {exc}",
              file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3

    print(json.dumps({
        "r_id": r_id, "d_id": d_id,
        "r_path": r_path_rel, "d_path": d_path_rel,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_design(args: argparse.Namespace) -> int:
    if not args.plan_content:
        print("ERROR: --stage design requires --plan-content", file=sys.stderr)
        return 2

    pst_root = Path(args.pst_root)
    try:
        status = load_status(pst_root)
    except StatusYamlMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    topic = slugify(args.topic)

    r = find_artifact_by_topic(status, topic, "research_finding")
    d = find_artifact_by_topic(status, topic, "decision")
    if r is None or d is None:
        print(
            f"ERROR: No R/D found for topic {topic!r}. "
            "Run --stage requirement first.",
            file=sys.stderr,
        )
        return 2

    plan_id = f"Plan.{topic}"
    # Reuse existing Plan path on regeneration so cross-day --force does not
    # orphan the original file under a new date prefix.
    existing_plan = find_artifact_by_topic(status, topic, "plan")
    if existing_plan and isinstance(existing_plan.get("path"), str):
        plan_path_rel = existing_plan["path"]
    else:
        plan_path_rel = f"plan/{today_iso()}-{topic}-design.md"
    plan_path = pst_root / plan_path_rel

    title = _topic_title(topic)
    body = Path(args.plan_content).read_text(encoding="utf-8")
    full = (
        f"# {plan_id}: {title}\n\n"
        f"<!-- based_on: [{r['id']}, {d['id']}] -->\n\n"
        f"{body.lstrip()}"
    )

    try:
        safe_write(plan_path, full, force=args.force)
    except FileExistsRefuseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    transitions = [{
        "artifact": plan_id, "new_file": True, "proposed_id": plan_id,
        "type": "plan",
        "from": None, "to": "draft",
        "path": plan_path_rel,
        "depends_on": [r["id"], d["id"]],
        "reason": f"project-state-spec scaffold: design stage for {topic}",
        "source": "project-state-spec",
    }]
    try:
        _write_transitions_and_apply(
            pst_root,
            transitions,
            event_summary=f"project-state-spec scaffold: design stage for {topic}",
            event_type="spec_scaffold",
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: apply_changes.py failed (exit {exc.returncode}): {exc}",
              file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3

    print(json.dumps({
        "plan_id": plan_id, "plan_path": plan_path_rel,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    if not args.tasks_manifest:
        print("ERROR: --stage tasks requires --tasks-manifest", file=sys.stderr)
        return 2

    pst_root = Path(args.pst_root)
    try:
        status = load_status(pst_root)
    except StatusYamlMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    topic = slugify(args.topic)
    plan = find_artifact_by_topic(status, topic, "plan")
    if plan is None:
        print(
            f"ERROR: No Plan found for topic {topic!r}. "
            "Run --stage design first.",
            file=sys.stderr,
        )
        return 2
    plan_id = plan["id"]

    manifest = json.loads(Path(args.tasks_manifest).read_text(encoding="utf-8"))
    tasks: list[dict] = manifest.get("tasks", [])
    if not tasks:
        print("ERROR: tasks manifest contains no tasks", file=sys.stderr)
        return 2
    lp_sequence: list[str] = manifest.get("lp_sequence", [t["slug"] for t in tasks])
    coding_standards: str | None = manifest.get("coding_standards")

    group: str | None = manifest.get("group") or args.group

    if group:
        ensure_group_exists(status, group)
        status_path = pst_root / "status" / "status.yaml"
        with status_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(status, fh, sort_keys=False, allow_unicode=True)

    # Allocate sequential ids in manifest order.
    base_lp = next_id(status, "LP")  # e.g. "LP-001"
    base_tp = next_id(status, "TP")
    base_lp_n = int(base_lp.split("-")[1])
    base_tp_n = int(base_tp.split("-")[1])

    written_pairs = []
    transitions: list[dict] = []
    for i, t in enumerate(tasks):
        lp_id = f"LP-{base_lp_n + i:03d}"
        tp_id = f"TP-{base_tp_n + i:03d}"
        slug = slugify(t["slug"])

        lp_path_rel = f"prompts/landing/{lp_id}-{slug}.md"
        tp_path_rel = f"prompts/test/{tp_id}-{slug}.md"
        lp_path = pst_root / lp_path_rel
        tp_path = pst_root / tp_path_rel

        if "lp_content" not in t or "tp_content" not in t:
            print(
                f"ERROR: task #{i} (slug={t.get('slug')!r}) missing "
                "'lp_content' and/or 'tp_content' in tasks manifest. "
                "Each task entry must include both fields pointing to a "
                "tmpfile containing the LP/TP body.",
                file=sys.stderr,
            )
            return 2
        try:
            lp_body = Path(t["lp_content"]).read_text(encoding="utf-8")
            tp_body = Path(t["tp_content"]).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            print(
                f"ERROR: task #{i} (slug={t.get('slug')!r}) refers to "
                f"missing file: {exc.filename}",
                file=sys.stderr,
            )
            return 2

        validates_ac = t.get("validates_ac", [])
        validates_property = t.get("validates_property", [])
        meta_block = (
            f"<!-- validates_ac: {validates_ac} -->\n"
            f"<!-- validates_property: {validates_property} -->\n"
        )

        lp_full = f"# {lp_id}: {slug}\n\n{meta_block}\n{lp_body.lstrip()}"
        tp_full = f"# {tp_id}: {slug}\n\n{meta_block}\n{tp_body.lstrip()}"

        try:
            safe_write(lp_path, lp_full, force=args.force)
            safe_write(tp_path, tp_full, force=args.force)
        except FileExistsRefuseError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        transitions.append({
            "artifact": lp_id, "new_file": True, "proposed_id": lp_id,
            "type": "landing_prompt",
            "from": None, "to": "draft",
            "path": lp_path_rel,
            "depends_on": [plan_id],
            "reason": f"project-state-spec scaffold: tasks stage for {topic}",
            "source": "project-state-spec",
            **({"group": group} if group else {}),
        })
        transitions.append({
            "artifact": tp_id, "new_file": True, "proposed_id": tp_id,
            "type": "test_prompt",
            "from": None, "to": "draft",
            "path": tp_path_rel,
            "depends_on": [lp_id],
            "reason": f"project-state-spec scaffold: tasks stage for {topic}",
            "source": "project-state-spec",
            **({"group": group} if group else {}),
        })
        # H4 fix: auto-register preconditions per PST §6C. Each Landing Prompt
        # requires its upstream Plan to be approved-or-ready before execution.
        transitions.append({
            "op": "precondition_register",
            "target": lp_id,
            "requires": [{
                "artifact": plan_id,
                "field": "status",
                "condition": "in [approved, ready]",
            }],
            "status": "pending",
            "reason": f"PSS tasks scaffold: {lp_id} requires upstream {plan_id}",
            "source": "project-state-spec",
        })
        written_pairs.append({
            "slug": slug, "lp_id": lp_id, "tp_id": tp_id,
            "lp_path": lp_path_rel, "tp_path": tp_path_rel,
        })

    # Landing README. Preserve user-edited front-matter and any user-edited
    # `## LP 序列` section per PST §7 README generation contract. Default to
    # generated values only when no existing README is present.
    meta = status.get("meta", {}) or {}
    fm_source_root = meta.get("source_root") or "<未配置>"
    fm_scope = meta.get("scope")
    fm_pst_root = meta.get("pst_root")

    fm_lines = ["---", f'source_root: "{fm_source_root}"']
    if fm_scope:
        fm_lines.append("scope:")
        for s in fm_scope:
            fm_lines.append(f'  - "{s}"')
    if fm_pst_root:
        fm_lines.append(f'pst_root: "{fm_pst_root}"')
    lp_seq_source = manifest.get("lp_sequence_source", "auto")
    fm_lines.append(f'lp_sequence_source: "{lp_seq_source}"')
    fm_lines.append("---")
    default_front_matter = "\n".join(fm_lines)

    sequence_tokens = []
    for slug in lp_sequence:
        match = next((p for p in written_pairs if p["slug"] == slugify(slug)), None)
        if match:
            sequence_tokens.append(f"{match['lp_id']}-{match['slug']}")
    default_lp_seq_line = " -> ".join(sequence_tokens)

    standards_section = ""
    if coding_standards:
        standards_section = f"\n## Coding Standards\n\n{coding_standards.strip()}\n"

    readme_path = pst_root / "prompts" / "landing" / "README.md"

    readme_written = False
    if group and readme_path.exists():
        existing = readme_path.read_text(encoding="utf-8")
        if "### " in existing and "## LP 序列" in existing:
            import re as _re2
            group_heading = f"### {group}"
            if group_heading in existing:
                # Replace existing group section's sequence line.
                pattern = rf"(### {_re2.escape(group)}\s*\n\s*)(.*?)(\n###|\n## |\Z)"
                def _repl(m):
                    return m.group(1) + default_lp_seq_line + "\n" + (m.group(3) if m.group(3) else "")
                updated = _re2.sub(pattern, _repl, existing, count=1, flags=_re2.DOTALL)
                safe_write(readme_path, updated, force=True)
            else:
                # Append new group section before the next ## heading after LP 序列.
                lp_seq_start = existing.find("## LP 序列")
                rest_after_lp = existing[lp_seq_start:]
                # Find the next ## heading after LP 序列 (but not ### headings).
                next_h2 = _re2.search(r"\n## (?!LP 序列)", rest_after_lp)
                if next_h2:
                    insert_pos = lp_seq_start + next_h2.start()
                else:
                    insert_pos = len(existing)
                new_section = f"\n### {group}\n\n{default_lp_seq_line}\n"
                updated = existing[:insert_pos] + new_section + existing[insert_pos:]
                safe_write(readme_path, updated, force=True)
            readme_written = True

    if not readme_written:
        front_matter = default_front_matter
        lp_seq_line = default_lp_seq_line
        existing_body_tail = ""
        if readme_path.exists():
            existing = readme_path.read_text(encoding="utf-8")
            # Preserve user-edited YAML front-matter if present.
            if existing.startswith("---\n"):
                end = existing.find("\n---", 4)
                if end != -1:
                    front_matter = existing[: end + len("\n---")]
                    rest = existing[end + len("\n---"):].lstrip("\n")
                else:
                    rest = existing
            else:
                rest = existing
            # Preserve user-edited `## LP 序列` content if lp_sequence_source == "user".
            # When "auto", allow PSS to overwrite with the newly generated sequence.
            import re as _re
            lp_seq_source_is_user = "lp_sequence_source:" in front_matter and '"user"' in front_matter
            m = _re.search(r"(?ms)^## LP 序列\s*\n(.*?)(?=^## |\Z)", rest)
            if m and lp_seq_source_is_user:
                existing_seq = m.group(1).strip()
                if existing_seq:
                    lp_seq_line = existing_seq
            # Preserve any sections after `## LP 序列` other than what we
            # regenerate (e.g. user notes appended to README body).
            tail_match = _re.search(r"(?ms)^## (?!LP 序列|Coding Standards)", rest)
            if tail_match:
                existing_body_tail = rest[tail_match.start():]

        readme_text = (
            f"{front_matter}\n\n"
            f"# Landing Prompts for {_topic_title(topic)}\n\n"
            f"## LP 序列\n\n"
            f"{lp_seq_line}\n"
            f"{standards_section}"
        )
        if existing_body_tail:
            readme_text = readme_text.rstrip() + "\n\n" + existing_body_tail
        try:
            safe_write(readme_path, readme_text, force=True)
        except FileExistsRefuseError as exc:  # pragma: no cover - force=True
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if fm_source_root == "<未配置>":
        print(
            "WARNING: meta.source_root is missing or placeholder. "
            "Re-run with `--source-root <absolute path>` (any stage) or set "
            "meta.source_root in status.yaml before running Execute-LandingPrompt.",
            file=sys.stderr,
        )

    try:
        _write_transitions_and_apply(
            pst_root,
            transitions,
            event_summary=f"project-state-spec scaffold: tasks stage for {topic}",
            event_type="spec_scaffold",
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: apply_changes.py failed (exit {exc.returncode}): {exc}",
              file=sys.stderr)
        print("Files were written but status.yaml was not updated. "
              "Run PST AUDIT to reconcile.", file=sys.stderr)
        return 3

    print(json.dumps({"tasks": written_pairs}, ensure_ascii=False, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Execute LP incremental update based on update-manifest."""
    if not args.update_manifest:
        print("ERROR: --stage update requires --update-manifest", file=sys.stderr)
        return 2

    pst_root = Path(args.pst_root)
    try:
        status = load_status(pst_root)
    except StatusYamlMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    topic = slugify(args.topic)
    manifest = json.loads(Path(args.update_manifest).read_text(encoding="utf-8"))

    transitions = []
    updated_pairs = []

    for upd in manifest.get("updates", []):
        if upd["mode"] == "patch":
            try:
                _apply_patch(pst_root, upd)
            except (FileNotFoundError, OSError) as exc:
                print(f"ERROR: patch failed for {upd.get('lp_id')}: {exc}",
                      file=sys.stderr)
                return 2
            lp_art = next(
                (a for a in status.get("artifacts", [])
                 if isinstance(a, dict) and a.get("id") == upd["lp_id"]),
                None
            )
            from_status = lp_art.get("status") if lp_art else "needs_update"
            transitions.append({
                "artifact": upd["lp_id"],
                "type": "landing_prompt",
                "from": from_status,
                "to": "draft",
                "reason": f"AC patch applied by PSS update for {topic}",
                "source": "project-state-spec",
            })
            updated_pairs.append({"lp_id": upd["lp_id"], "mode": "patch"})

        elif upd["mode"] == "rewrite":
            try:
                result = _apply_rewrite(pst_root, status, upd, topic)
            except (FileNotFoundError, OSError) as exc:
                print(f"ERROR: rewrite failed for {upd.get('lp_id')}: {exc}",
                      file=sys.stderr)
                return 2
            transitions.extend(result["transitions"])
            updated_pairs.append({
                "lp_id": upd["lp_id"],
                "mode": "rewrite",
                "new_lp_id": result["new_lp_id"],
                "new_lp_path": result["new_lp_path"],
            })

    # Process new LPs (for uncovered ACs)
    for new_lp in manifest.get("new_lps", []):
        slug = slugify(new_lp["slug"])
        new_lp_id = next_id(status, "LP")
        new_tp_id = next_id(status, "TP")

        lp_path_rel = f"prompts/landing/{new_lp_id}-{slug}.md"
        tp_path_rel = f"prompts/test/{new_tp_id}-{slug}.md"

        lp_body = Path(new_lp["lp_content"]).read_text(encoding="utf-8")
        tp_body = Path(new_lp["tp_content"]).read_text(encoding="utf-8")

        validates_ac = new_lp.get("validates_ac", [])
        validates_property = new_lp.get("validates_property", [])
        meta_block = (
            f"<!-- validates_ac: {validates_ac} -->\n"
            f"<!-- validates_property: {validates_property} -->\n"
        )

        lp_full = f"# {new_lp_id}: {slug}\n\n{meta_block}\n{lp_body.lstrip()}"
        tp_full = f"# {new_tp_id}: {slug}\n\n{meta_block}\n{tp_body.lstrip()}"

        safe_write(pst_root / lp_path_rel, lp_full, force=args.force)
        safe_write(pst_root / tp_path_rel, tp_full, force=args.force)

        plan = find_artifact_by_topic(status, topic, "plan")
        plan_id = plan["id"] if plan else f"Plan.{topic}"

        transitions.append({
            "artifact": new_lp_id, "new_file": True, "proposed_id": new_lp_id,
            "type": "landing_prompt", "from": None, "to": "draft",
            "path": lp_path_rel, "depends_on": [plan_id],
            "reason": f"PSS update: new LP for uncovered AC in {topic}",
            "source": "project-state-spec",
        })
        transitions.append({
            "artifact": new_tp_id, "new_file": True, "proposed_id": new_tp_id,
            "type": "test_prompt", "from": None, "to": "draft",
            "path": tp_path_rel, "depends_on": [new_lp_id],
            "reason": f"PSS update: new TP for {new_lp_id} in {topic}",
            "source": "project-state-spec",
        })
        updated_pairs.append({
            "slug": slug, "mode": "new",
            "lp_id": new_lp_id, "tp_id": new_tp_id,
        })

    if transitions:
        try:
            _write_transitions_and_apply(
                pst_root, transitions,
                event_summary=f"PSS update: incremental LP update for {topic}",
                event_type="spec_update",
            )
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: apply_changes.py failed: {exc}", file=sys.stderr)
            return 3

    # Update LP sequence in README
    _update_lp_sequence(pst_root, manifest, updated_pairs)

    print(json.dumps({"updated": updated_pairs}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scaffold_spec.py",
        description="project-state-spec scaffold script.",
    )
    p.add_argument("--stage", required=True,
                   choices=["requirement", "design", "tasks", "status", "update"])
    p.add_argument("--topic", required=True,
                   help="kebab-case slug uniquely identifying this spec")
    p.add_argument("--group", default=None,
                   help="Feature group name for artifact isolation (opt-in)")
    p.add_argument("--pst-root", required=True,
                   help="PST project root (directory containing status/status.yaml)")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing files for this stage")
    p.add_argument("--r-content", help="path to tmpfile with R markdown body (stage=requirement)")
    p.add_argument("--d-content", help="path to tmpfile with D yaml body (stage=requirement)")
    p.add_argument("--plan-content", help="path to tmpfile with Plan markdown body (stage=design)")
    p.add_argument("--tasks-manifest", help="path to tmpfile with tasks JSON manifest (stage=tasks)")
    p.add_argument("--update-manifest",
                   help="path to tmpfile with update JSON manifest (stage=update)")
    p.add_argument("--source-root",
                   help="Absolute path to source code root. Persisted to "
                        "meta.source_root so Execute-LandingPrompt can run.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # B1 fix: persist source_root early so subsequent stages (and ELP) can find
    # it. Allowed for every stage including 'status' so users can update it
    # post-hoc with `--stage status --source-root <path>`.
    if getattr(args, "source_root", None):
        try:
            resolved = set_source_root(args.pst_root, args.source_root)
            print(f"[scaffold_spec] meta.source_root set to {resolved}",
                  file=sys.stderr)
        except (StatusYamlMissingError, ValueError) as exc:
            print(f"ERROR: --source-root: {exc}", file=sys.stderr)
            return 2

    if args.stage == "status":
        return cmd_status(args)
    if args.stage == "requirement":
        return cmd_requirement(args)
    if args.stage == "design":
        return cmd_design(args)
    if args.stage == "tasks":
        return cmd_tasks(args)
    if args.stage == "update":
        return cmd_update(args)
    parser.error(f"unknown stage: {args.stage}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
