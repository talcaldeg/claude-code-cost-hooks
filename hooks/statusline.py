"""Claude Code status line: folder, git branch (with a dirty mark), model and context size.

Claude Code passes the session as JSON on stdin and expects one line on stdout. It runs on
every refresh, so everything here is cheap and can never fail: any error is swallowed and the
line just gets shorter. The branch is read from .git/HEAD instead of running `git branch`,
and the context size from the tail of the transcript (the last API call's input tokens),
which is the number that every next call pays again.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

TAIL_BYTES = 256 * 1024


def git_root(path):
    """Walk up looking for .git. Returns the repo root or None."""
    cur = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def branch_name(root):
    """Reads .git/HEAD. On a detached HEAD returns the short hash."""
    try:
        with open(os.path.join(root, ".git", "HEAD"), "r", encoding="utf-8") as fh:
            ref = fh.read().strip()
    except OSError:
        return None
    if ref.startswith("ref: refs/heads/"):
        return ref[len("ref: refs/heads/"):]
    return ref[:7] if ref else None


def is_dirty(root, ignore_untracked):
    """True if tracked files have uncommitted changes (untracked ones optional)."""
    cmd = ["git", "-C", root, "status", "--porcelain"]
    if ignore_untracked:
        cmd.append("--untracked-files=no")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def last_context(transcript):
    """Input tokens of the last API call in the transcript, or 0."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        if b'"usage"' not in line:
            continue
        try:
            u = json.loads(line)["message"]["usage"]
        except Exception:
            continue
        return ((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                + (u.get("cache_read_input_tokens") or 0))
    return 0


def main():
    cfg = c.config("statusline")
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    cwd = (data.get("workspace") or {}).get("current_dir") or os.getcwd()
    model = (data.get("model") or {}).get("display_name") or ""

    # More than one segment: many work folders end with the same name.
    n = max(1, int(cfg.get("path_segments") or 2))
    segments = [s for s in cwd.replace("\\", "/").rstrip("/").split("/") if s]
    label = "/".join(segments[-n:]) if segments else cwd
    parts = ["{}{}{}".format(CYAN, label, RESET)]

    root = git_root(cwd)
    if root:
        branch = branch_name(root)
        if branch:
            color = YELLOW if is_dirty(root, cfg.get("ignore_untracked", True)) else GREEN
            mark = "*" if color == YELLOW else ""
            parts.append("{}{}{}{}".format(color, branch, mark, RESET))

    if model:
        parts.append("{}{}{}".format(DIM, model, RESET))

    if cfg.get("show_context", True) and data.get("transcript_path"):
        ctx = last_context(data["transcript_path"])
        if ctx:
            parts.append("{}ctx {}k{}".format(DIM, int(round(ctx / 1000.0)), RESET))

    sys.stdout.write(" {}|{} ".format(DIM, RESET).join(parts))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A broken status line must not dirty the screen or abort the session.
        sys.exit(0)
