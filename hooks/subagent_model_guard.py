"""PreToolUse hook (Agent): a subagent launched without `model` inherits the main model.

The main session usually runs the most capable and most expensive model, and every subagent
call without `model` inherits it. In the setup behind this package, with a written rule to
send search to Haiku and mechanical work to Sonnet, 90 of 126 subagent calls in 30 days set no
model and none went to Haiku. As with inline Python, a rule does not compete with the
shortcut; a block does, and the fix is one word.

Two modes, chosen with `default_model` in config.json:
- null (default): the call is blocked and Claude repeats it with a `model`.
- a model name, e.g. "sonnet": the call goes through with that model filled in.

Always allowed: the types in `exempt_types` (a fork always inherits; the small built-in types
bring their own model), and agent types whose definition in <config>/agents or
<cwd>/.claude/agents already sets `model:`. An explicit `model: "opus"` always passes.
Any error exits silently.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

MSG = (
    "BLOCKED: an Agent call without `model` inherits the main session's model, usually the most "
    "expensive one. Pick by task: `haiku` if it only locates or searches, `sonnet` for long "
    "mechanical work (edit, extract, transform), and an explicit `opus` only when the subagent "
    "plans, audits or reaches a conclusion that matters. In a measured setup, 90 of 126 "
    "subagents in 30 days inherited the top model and none went to Haiku. Repeat the call with "
    "`model`.")


def definition_sets_model(agent_type, cwd):
    for base in (c.claude_dir() / "agents", Path(cwd or ".") / ".claude" / "agents"):
        path = base / (agent_type + ".md")
        if path.is_file():
            with open(path, encoding="utf-8", errors="ignore") as fh:
                head = fh.read(3000)
            m = re.match(r"---\s*\n(.*?)\n---", head, re.S)
            if m and re.search(r"(?m)^model:\s*\S", m.group(1)):
                return True
    return False


def main():
    cfg = c.config("subagent_model")
    if not cfg.get("enabled", True):
        return
    data = c.read_payload()
    if data.get("tool_name") != "Agent":
        return
    entry = data.get("tool_input") or {}
    if entry.get("model"):
        return
    agent_type = (entry.get("subagent_type") or "").strip()
    if agent_type in set(cfg.get("exempt_types") or []):
        return
    if agent_type and definition_sets_model(agent_type, data.get("cwd")):
        return
    default = (cfg.get("default_model") or "").strip()
    if default:
        c.pre_tool("allow", "subagent model set to %s by context-economy" % default,
                   updated_input=dict(entry, model=default))
    else:
        c.pre_tool("deny", MSG)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
