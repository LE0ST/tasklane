"""Deterministic bug fixture generator for isolated disposable workspace drills.

IMPORTANT: Defect code is NEVER injected into the main repository tree or production image.
It is injected strictly into temporary disposable workspaces for on-call remediation testing.
"""
import difflib
from pathlib import Path
from typing import Tuple

BUG_FIXTURE_MARKER_START = "# --- ONCALL-BUG-FIXTURE-START ---"
BUG_FIXTURE_MARKER_END = "# --- ONCALL-BUG-FIXTURE-END ---"

# Defect: unhandled RuntimeError on urgent task creation
BUG_CODE_BLOCK = f"""{BUG_FIXTURE_MARKER_START}
    if task.priority == "urgent":
        raise RuntimeError("UNCAUGHT REGRESSION: Failed to dispatch urgent task")
    {BUG_FIXTURE_MARKER_END}
"""


def get_target_file(workspace_dir: Path) -> Path:
    """Locate the target backend/main.py file within the disposable workspace."""
    target = workspace_dir / "backend" / "main.py"
    if not target.exists():
        # Fallback if workspace is backend directory directly
        target = workspace_dir / "main.py"
    return target


def inject_task_creation_defect(workspace_dir: Path) -> bool:
    """Inject the deterministic task-creation defect into the disposable workspace."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        raise FileNotFoundError(f"Target file not found in workspace: {target_path}")

    content = target_path.read_text(encoding="utf-8")
    if BUG_FIXTURE_MARKER_START in content:
        # Already injected
        return True

    # Find the create_task endpoint definition
    # Look for 'async def create_task(' or 'def create_task('
    anchor = "def create_task("
    pos = content.find(anchor)
    if pos == -1:
        raise ValueError("Could not find 'def create_task(' in target file")

    # Find the end of the def line and docstring/first statement
    colon_pos = content.find("):", pos)
    if colon_pos == -1:
        raise ValueError("Could not locate end of create_task signature")

    # Move past docstring if present
    docstring_end = colon_pos + 2
    triple_quote_pos = content.find('"""', colon_pos)
    if triple_quote_pos != -1 and triple_quote_pos - colon_pos < 100:
        closing_triple = content.find('"""', triple_quote_pos + 3)
        if closing_triple != -1:
            docstring_end = closing_triple + 3

    # Insert bug block
    new_content = (
        content[:docstring_end]
        + "\n"
        + BUG_CODE_BLOCK
        + content[docstring_end:]
    )
    target_path.write_text(new_content, encoding="utf-8")
    return True


def has_task_creation_defect(workspace_dir: Path) -> bool:
    """Check if the disposable workspace contains the defect."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        return False
    content = target_path.read_text(encoding="utf-8")
    return BUG_FIXTURE_MARKER_START in content


def remove_task_creation_defect(workspace_dir: Path) -> Tuple[str, bool]:
    """Remove the defect from the disposable workspace and return unified diff."""
    target_path = get_target_file(workspace_dir)
    if not target_path.exists():
        return "", False

    original_content = target_path.read_text(encoding="utf-8")
    if BUG_FIXTURE_MARKER_START not in original_content:
        return "", False

    # Extract lines and filter out bug block
    lines = original_content.splitlines(keepends=True)
    new_lines = []
    in_bug_block = False

    for line in lines:
        if BUG_FIXTURE_MARKER_START in line:
            in_bug_block = True
            continue
        if BUG_FIXTURE_MARKER_END in line:
            in_bug_block = False
            continue
        if not in_bug_block:
            new_lines.append(line)

    repaired_content = "".join(new_lines)

    diff_lines = list(difflib.unified_diff(
        lines,
        new_lines,
        fromfile=f"a/{target_path.name}",
        tofile=f"b/{target_path.name}",
        lineterm=""
    ))
    diff_text = "\n".join(diff_lines)

    target_path.write_text(repaired_content, encoding="utf-8")
    return diff_text, True
