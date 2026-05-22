"""Pure utility functions for project-state-spec scaffold script.

These functions have NO side effects (other than ``safe_write``, which is
explicit) and are unit-tested in tests/test_spec_helpers.py.
"""

from __future__ import annotations

import datetime as _dt
import re as _re
from pathlib import Path

import yaml  # PyYAML, already in requirements.txt


class StatusYamlMissingError(FileNotFoundError):
    """Raised when ``<pst_root>/status/status.yaml`` does not exist."""


class FileExistsRefuseError(FileExistsError):
    """Raised by safe_write when the target file exists and force=False."""


def slugify(text: str) -> str:
    """Lowercase, replace non-ASCII alphanumerics with ``-``, collapse repeats.

    Raises ``ValueError`` if the result would be empty.
    """
    lowered = text.lower()
    # Replace every char that is not [a-z0-9] with a hyphen.
    replaced = _re.sub(r"[^a-z0-9]+", "-", lowered)
    # Collapse runs of hyphens, strip leading/trailing.
    collapsed = _re.sub(r"-+", "-", replaced).strip("-")
    if not collapsed:
        raise ValueError(f"slugify produced empty string from input: {text!r}")
    return collapsed


def today_iso() -> str:
    """Return current UTC date as ``YYYY-MM-DD``."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def load_status(pst_root: Path | str) -> dict:
    """Load and return ``<pst_root>/status/status.yaml`` as a dict.

    Raises ``StatusYamlMissingError`` if the file does not exist.
    """
    pst_root = Path(pst_root)
    status_path = pst_root / "status" / "status.yaml"
    if not status_path.is_file():
        raise StatusYamlMissingError(
            f"status.yaml not found at {status_path}. "
            "Run PST INIT first."
        )
    with status_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"status.yaml at {status_path} must be a mapping at top level")
    return data


def set_source_root(pst_root: Path | str, source_root: str) -> str:
    """Persist ``meta.source_root`` into ``<pst_root>/status/status.yaml``.

    Returns the absolute path that was written. Raises StatusYamlMissingError
    if status.yaml is absent. Raises ValueError if source_root does not exist
    on disk (use --allow-missing-source-root to bypass — not exposed today).

    This is a small, direct YAML edit (not a transition) because meta keys are
    project configuration, not artifact state. apply_changes.py preserves the
    key through refresh_meta on subsequent runs.
    """
    pst_root = Path(pst_root)
    status_path = pst_root / "status" / "status.yaml"
    if not status_path.is_file():
        raise StatusYamlMissingError(
            f"status.yaml not found at {status_path}. Run PST INIT first."
        )

    resolved = Path(source_root).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(
            f"source_root path does not exist: {resolved}. "
            "Pass an existing directory."
        )

    with status_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"status.yaml at {status_path} must be a mapping at top level")

    meta = data.setdefault("meta", {})
    meta["source_root"] = str(resolved)

    tmp = status_path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(status_path)
    return str(resolved)


_VALID_PREFIXES = {"R", "D", "LP", "TP"}


def next_id(status: dict, prefix: str) -> str:
    """Return the next sequential id for ``prefix`` (one of R, D, LP, TP).

    Scans ``status`` for any string id matching ``<prefix>-<NNN>`` across the
    sections that may contain it (``artifacts``, plus the dedicated
    ``research_findings`` / ``decisions`` lists). Returns the next number
    zero-padded to 3 digits.
    """
    if prefix not in _VALID_PREFIXES:
        raise ValueError(
            f"Unknown id prefix: {prefix!r}. Expected one of {sorted(_VALID_PREFIXES)}."
        )

    pattern = _re.compile(rf"^{_re.escape(prefix)}-(\d+)$")
    seen: list[int] = []

    sections = ["artifacts"]
    if prefix == "R":
        sections.append("research_findings")
    if prefix == "D":
        sections.append("decisions")

    for section in sections:
        for entry in status.get(section, []) or []:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if not isinstance(entry_id, str):
                continue
            m = pattern.match(entry_id)
            if m:
                seen.append(int(m.group(1)))

    next_num = (max(seen) + 1) if seen else 1
    return f"{prefix}-{next_num:03d}"


def find_artifact_by_topic(
    status: dict, topic: str, artifact_type: str
) -> dict | None:
    """Return the first artifact of ``artifact_type`` whose path matches ``topic``.

    Path-shape matching rules (anchored, full-path):
      - research_finding : ``research/R-<digits>-<topic>.md``
      - decision         : ``decisions/D-<digits>-<topic>.yaml``
      - plan             : ``plan/<YYYY-MM-DD>-<topic>-design.md``

    The full-path anchor prevents false matches when ``topic`` is itself a
    hyphen-containing tail of another topic (e.g. ``guide-button`` must not
    match a path ending in ``readme-guide-button.md``).

    Lookup sections per artifact type (PST stores R in ``research_findings[]``
    and D in ``decisions[]``, not in ``artifacts[]``):
      - research_finding → ``research_findings`` then ``artifacts``
      - decision         → ``decisions`` then ``artifacts``
      - plan             → ``artifacts``

    Returns the first matching entry, or ``None`` if none match.
    Raises ``ValueError`` if ``artifact_type`` is unknown.
    """
    pattern_map = {
        "research_finding": rf"^research/R-\d+-{_re.escape(topic)}\.md$",
        "decision":         rf"^decisions/D-\d+-{_re.escape(topic)}\.yaml$",
        "plan":             rf"^plan/\d{{4}}-\d{{2}}-\d{{2}}-{_re.escape(topic)}-design\.md$",
    }
    if artifact_type not in pattern_map:
        raise ValueError(f"Unknown artifact_type: {artifact_type!r}")
    pattern = _re.compile(pattern_map[artifact_type])

    section_map = {
        "research_finding": ["research_findings", "artifacts"],
        "decision":         ["decisions", "artifacts"],
        "plan":             ["artifacts"],
    }
    for section in section_map[artifact_type]:
        for entry in status.get(section, []) or []:
            if not isinstance(entry, dict):
                continue
            # In dedicated sections (research_findings/decisions) the type
            # field may be absent or differ ("finding" in research_findings);
            # don't filter by type when iterating those sections.
            if section == "artifacts" and entry.get("type") != artifact_type:
                continue
            path = entry.get("path", "")
            if isinstance(path, str) and pattern.match(path):
                return entry
    return None


def safe_write(path: Path | str, content: str, force: bool) -> None:
    """Write ``content`` (UTF-8, LF) to ``path``.

    Creates parent directories. If ``path`` already exists and ``force`` is
    False, raises ``FileExistsRefuseError`` and writes nothing.
    """
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsRefuseError(
            f"Refusing to overwrite existing file: {path} (pass --force to override)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def ensure_group_exists(status: dict, group_name: str) -> None:
    """Ensure a group entry exists in status['groups']. Creates the list if absent.

    Does nothing if the group already exists. Only adds active groups.
    """
    if not group_name:
        return
    groups = status.setdefault("groups", [])
    if any(g.get("name") == group_name for g in groups):
        return
    groups.append({
        "name": group_name,
        "status": "active",
        "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "description": "",
    })
