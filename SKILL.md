---
name: project-state-spec
description: "Three-stage spec authoring workflow (Requirement → Design → Task) that produces PST-conformant artifacts (R + D + Plan + LP + TP) on disk and registers them via apply_changes.py. Use when the user wants to author a new spec for a feature in a PST-managed project, or to resume an in-progress spec."
---

# project-state-spec — Three-Stage Spec Workflow

Guide the user through Requirement → Design → Task three stages, producing a complete PST artifact set (R + D + Plan + LP[] + TP[]) for one feature.

## Invocation

```text
Skill project-state-spec + new <topic>
Skill project-state-spec + continue <topic>
```

`<topic>` is a kebab-case slug uniquely naming this spec, e.g. `readme-guide-button`.

On `new`: start at Stage 1.
On `continue`: query `tools/scaffold_spec.py --stage status` and resume at `next_stage`. If `next_stage == done`, ask which stage the user wants to revise.

## Hard Preconditions

Before starting any stage:
1. The current workspace must contain `<pst_root>/status/status.yaml`. If missing, tell the user to run PST INIT first and STOP.
2. Resolve `<pst_root>` from the user's invocation context. If ambiguous, ask the user.

## Stage 1 — Requirement (produces R + D)

### Step 1.1 — Probe
Ask the user, ONE AT A TIME:
1. One-sentence goal of this feature?
2. Trigger / pain point — why now?
3. Who is the user, in what scenario?
4. Existing constraints (tech stack, performance targets, compliance)?

Do not write anything yet.

### Step 1.2 — Draft R
Read `templates/r_template.md`. Compose a markdown body following that structure.
- `## Background`: trigger and context
- `## Current State`: facts about the existing system, code/artifact pointers
- `## Constraints`: tech / performance / compliance / style
- `## References`: external links + internal artifact IDs

Show the draft to the user and ask for confirmation. Iterate until they approve.

### Step 1.3 — Draft D
Read `templates/d_template.yaml`. Compose a yaml body. **Critical:** every Acceptance Criterion's `statements` MUST use EARS keywords (SHALL, WHEN, IF, WHILE). Self-check before showing the draft. If a statement does not contain SHALL, rewrite it.

**Important:** Do NOT include the top-level keys `id`, `title`, `status`, or `based_on` in your draft. The scaffold script auto-prepends these (e.g. `id: D-001\ntitle: "..."\nstatus: draft\nbased_on: [R-001]\n`). Including them yourself produces a YAML file with duplicate top-level keys, which is invalid.

Show the draft to the user. Iterate until approved.

### Step 1.4 — Scaffold
Write the approved R body to a tmpfile and the approved D body to a tmpfile, then invoke:

```text
python <pst_root>/Tools/Skills/project-state-spec/tools/scaffold_spec.py \
    --stage requirement \
    --topic <topic> \
    --pst-root <pst_root> \
    --r-content <r-tmpfile> \
    --d-content <d-tmpfile> \
    [--source-root <absolute-path-to-code-root>]
```

`--source-root` is optional but **strongly recommended on first invocation** of a project. It persists into `meta.source_root` so Execute-LandingPrompt can find the codebase. Without it, `prompts/landing/README.md` will be written with `source_root: "<未配置>"` and ELP will refuse to run. You can also set it later with `--stage status --source-root <path>`.

(If the script lives in a different absolute path because the skill was installed via `install_skills.py`, use that path. The skill installation directory is typically `~/.kiro/skills/project-state-spec/`.)

Parse the JSON output. Tell the user the allocated `R-NNN` and `D-NNN` ids.

### Step 1.5 — Pause Gate
Ask: **"Requirement stage complete (R-NNN + D-NNN registered as draft). Continue to Design? Or pause here?"**

If pause → STOP. Tell the user they can resume with `Skill project-state-spec + continue <topic>`.

## Stage 2 — Design (produces Plan)

### Step 2.1 — Load context
Read the R-NNN and D-NNN files just written. Read relevant code regions in `meta.source_root` to understand the current implementation surface.

### Step 2.2 — Propose architectures
Present 2–3 architecture options. For each: trade-offs, recommendation, reasoning. User picks one.

### Step 2.3 — Draft Plan
Read `templates/plan_template.md`. Compose a markdown body following that structure. Every Property MUST include a `**Validates: AC-N**` line referencing a specific AC from D.

Show the draft. Iterate until approved.

### Step 2.4 — Scaffold
Write the approved Plan body to a tmpfile, then:

```text
python <scaffold_spec.py> --stage design --topic <topic> --pst-root <pst_root> --plan-content <plan-tmpfile>
```

Parse JSON, tell user the `Plan.<topic>` id.

### Step 2.5 — Pause Gate
Ask: **"Design stage complete. Continue to Tasks? Or pause here?"**

## Stage 3 — Tasks (produces LP[] + TP[])

### Step 3.1 — Decompose
Read the Plan. Propose a task list. Each task is one cohesive unit of work with clear file boundaries. For each task, list:
- slug (kebab-case)
- one-sentence description
- which AC ids it validates
- which Property ids it validates

Self-check: every task references at least one AC. If not, ask the user; possibly the AC is missing from D and should be added (in which case go back to Stage 1 in --force mode).

User confirms task list.

### Step 3.2 — Draft LP + TP for each task
For each task, read `templates/lp_template.md` and `templates/tp_template.md`. Draft both. Show drafts in batches. Iterate until approved.

### Step 3.3 — Compose tasks manifest
Build a JSON manifest:

```json
{
  "tasks": [
    {"slug": "...", "lp_content": "<tmpfile>", "tp_content": "<tmpfile>",
     "validates_ac": [...], "validates_property": [...]},
    ...
  ],
  "lp_sequence": ["slug1", "slug2", ...],
  "lp_sequence_source": "auto",
  "coding_standards": "<optional>"
}
```

If the user has project-wide coding standards (check `.kiro/steering/` or ask), include them.

`lp_sequence_source: "auto"` tells PST render that this initial topo-sort came from PSS scaffold and may be regenerated until a human edits `## LP 序列` (which flips it to `"user"`). Continue passing `"auto"` on every PSS-driven scaffold; only hand-edits should ever produce `"user"`.

### Step 3.4 — Scaffold
```text
python <scaffold_spec.py> --stage tasks --topic <topic> --pst-root <pst_root> --tasks-manifest <manifest-tmpfile>
```

Parse JSON. Tell the user every LP and TP id allocated.

### Step 3.5 — Closing
Tell the user:
- Spec is complete and registered in `status.yaml` as `draft` artifacts.
- To execute: `Skill Execute-LandingPrompt + <pst_root>/prompts/landing/LP-001-<slug>.md`.
- After hand-edits to any artifact, run PST AUDIT to reconcile.

## Continue Command

When the user invokes `continue <topic>`:

1. Run `--stage status` and parse JSON.
2. Look at `next_stage`:
   - `requirement` → start at Stage 1.1
   - `design` → start at Stage 2.1
   - `tasks` → start at Stage 3.1
   - `done` → tell the user the spec is complete, ask if they want to revise a stage. If yes, use `--force` for that stage.

## Self-Checks (before invoking the script)

- All Acceptance Criteria use EARS keywords (SHALL, WHEN, IF, WHILE).
- Every Property has `**Validates: AC-N**`.
- Every task references at least one AC.
- LP file count == TP file count (each LP has its TP).
- No placeholders in drafts (`TBD`, `TODO`, `<...>` left unfilled).

## Error Handling

| Scenario | Behavior |
|---|---|
| status.yaml missing | Tell user to run PST INIT, STOP. |
| Script returns exit code 2 | Print stderr to user, ask how to proceed. |
| Script returns exit code 3 (apply_changes failure) | Tell user files were written but status.yaml is out of sync; run PST AUDIT. |
| User says "rewrite" at confirmation | Re-draft within stage; do NOT invoke script. |
| User wants to abort mid-stage | Acknowledge; what's been written remains; resume with `continue`. |

## Known Limitations

- **Tasks --force regeneration orphans previous LP/TP files.** Re-running `--stage tasks --force` allocates fresh LP-NNN/TP-NNN ids (because `next_id` reads status.yaml, which already contains the previous LP/TP entries). The new ids do not collide with the old paths, so the previous files remain on disk while status.yaml gains new artifacts. PST AUDIT will flag the orphans on its next run. Workaround: hand-delete the old `prompts/landing/LP-NNN-*.md` and `prompts/test/TP-NNN-*.md` files before re-running.

- **Design --force may accumulate duplicate Plan transitions in status.yaml.** Each design invocation emits a `from: null → to: draft` transition for the same `Plan.<topic>` id. Whether `apply_changes.py` deduplicates these (versus appending a new artifact entry) depends on its implementation. PST AUDIT can reconcile, but if you re-run design more than once, run `Skill project-state-tracker + audit` to clean up.

- **Distribution: only Kiro and Claude installs include the scaffold script.** `install_skills.py` deploys folder-style skills to Kiro (`~/.kiro/skills/`) and Claude (`~/.claude/skills/`) including all subdirectories (`tools/`, `templates/`). Cursor (single `.mdc` file), Copilot, and Codex (shared concat files) only receive the SKILL.md body — they do NOT receive `tools/scaffold_spec.py` or the templates. To use this skill from those agents, either install via Kiro/Claude on the same machine, or invoke the script directly from the source repo.

- **EARS validation is agent-side only.** The scaffold script does not parse or validate AC statements. If the agent's EARS self-check is skipped or wrong, malformed acceptance criteria will land in `decisions/D-NNN-*.yaml` unchecked. The Self-Checks section above is the only enforcement.

## What This Skill Does NOT Do

- Does not execute LPs (use `Execute-LandingPrompt`).
- Does not run PST AUDIT (run it manually or let the next AUDIT pick up changes).
- Does not edit `status.yaml` directly — only via `apply_changes.py`.
