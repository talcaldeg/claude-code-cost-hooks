"""PreToolUse hook (Bash|PowerShell): blocks sending processes to the background outside the harness.

Why it exists: a subagent once launched five `python script.py > log.txt 2>&1 &` inside a
normal Bash call. That `&` leaves the process loose: the harness gives it no task id, the
task-stop tool cannot reach it, and when the session was interrupted nobody killed it. The
five ran for 20 hours at 100 % CPU without writing a line, and were found by chance.

What is blocked:
- Bash: a `&` that sends a command to the background (not `&&`, `2>&1`, `&>` or `>&2`),
  `nohup`, `disown`, `setsid`.
- PowerShell: `Start-Job`, and `Start-Process` of an interpreter (python, powershell, node,
  cmd) without `-Wait`. `Start-Process` on a document (opening an .xlsx or .html) passes.

The right way is `run_in_background: true` on the tool, one process per call: it gets an id,
a completion notice and can be stopped. Any error exits silently.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

# A bare & ending a command: not preceded by & > < |, not followed by & or >.
BASH_AMP = re.compile(r"(?<![&>|<])&(?![&>])\s*(?:$|;|\n|\)|\}|done\b|wait\b|sleep\b|echo\b|cat\b|ls\b)", re.M)
BASH_DET = re.compile(r"(?:^|[\s;&|(])(?:nohup|disown|setsid)\b")
PS_JOB = re.compile(r"(?i)\bStart-Job\b")

MSG = (
    "BLOCKED: background process outside the harness ({what}). A `&` like this once left five "
    "processes running for 20 hours at 100 % CPU that nobody could see or stop: it gets no task "
    "id, the task-stop tool cannot reach it, and interrupting the session does not kill it. Use "
    "`run_in_background: true` on the tool, one process per call (several calls in parallel if "
    "you need several), and give long scripts a per-step timeout and a progress line in their "
    "log.")


def ps_proc_pattern(interpreters):
    names = "|".join(re.escape(n) for n in interpreters) or "python"
    return re.compile(
        r"""(?i)\b(?:Start-Process|saps|start)\s+(?:-FilePath\s+)?["']?[^\s"']*?"""
        r"""(?:%s)(?:\.exe)?["']?(?=[\s;]|$)[^;\n]*""" % names)


def strip_quotes(cmd):
    """Blank out quoted text so a & inside a string is not blocked."""
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", cmd)


def reason(tool, cmd, interpreters):
    clean = strip_quotes(cmd)
    if tool == "Bash":
        if BASH_AMP.search(clean):
            return "`&` in Bash"
        if BASH_DET.search(clean):
            return "nohup/disown/setsid"
        return None
    if PS_JOB.search(clean):
        return "Start-Job"
    for m in ps_proc_pattern(interpreters).finditer(cmd):
        if not re.search(r"(?i)\s-Wait\b", m.group(0)):
            return "Start-Process of an interpreter without -Wait"
    return None


def main():
    cfg = c.config("background")
    if not cfg.get("enabled", True):
        return
    data = c.read_payload()
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str) or not cmd:
        return
    what = reason(data.get("tool_name"), cmd, cfg.get("interpreters") or [])
    if what:
        c.pre_tool("deny", MSG.format(what=what))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
