"""Shared helpers for the context-economy hooks: config, token estimate, state and output.

Standard library only, Python 3.8+. Every hook adds its own folder to sys.path and imports
this module, so the folder can live anywhere.

Config is read from `config.json` next to the `hooks/` folder (the installer copies
`config.example.json` there), or from the file named in CONTEXT_ECONOMY_CONFIG. Missing keys
fall back to DEFAULTS, so a config file only needs the values you want to change.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE_DIR = HERE.parent

# Keep in sync with config.example.json (the test suite checks they match).
DEFAULTS = {
    # Characters per token used for every estimate. 2.1 was calibrated on Claude Code
    # context growth (see README, "Why 2.1 characters per token"); 4.0 is the textbook rule
    # and undercounts tool results by about 1.9x.
    "chars_per_token": 2.1,
    "large_read": {
        "enabled": True,
        "warn_tokens": 5000,
        "reread_tokens": 1500,
        "skip_extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf", ".ipynb"],
        "skip_dirs": [],
        "dump_dir_markers": ["scratchpad"],
        "state_days": 3,
    },
    "session_cost": {
        "enabled": True,
        "switch_factor": 10,
        "fallback_threshold": 600000,
        "min_startup": 20000,
        "warn_calls_ahead": [10, 5, 3],
        "pause_seconds": 3600,
        "pause_excess_k": 20,
        "long_session_calls": 60,
        "long_session_excess_k": 100,
        "extra_instruction": "",
        "state_days": 14,
        "prices": {"input_tokens": 1.0, "cache_creation_input_tokens": 1.25,
                   "cache_read_input_tokens": 0.1, "output_tokens": 5.0,
                   "cache_creation_1h": 2.0},
    },
    "skill_cost": {
        "enabled": True,
        "total_cap": 3500,
        "per_skill_cap": 400,
        "usage_window_days": None,
        "min_age_days": 30,
        "repeat_days": 7,
    },
    "inline_python": {
        "enabled": True,
        "block_dash_c": True,
        "block_heredoc": True,
        "block_stdin": True,
        "fix_select_first": True,
    },
    "background": {
        "enabled": True,
        "interpreters": ["python", "pythonw", "py", "powershell", "pwsh", "node", "cmd"],
    },
    "subagent_model": {
        "enabled": True,
        "default_model": None,
        "exempt_types": ["fork", "claude-code-guide", "statusline-setup"],
    },
    "browser": {
        "enabled": True,
        "tool_prefix": "mcp__Claude_Browser__",
        "batch_tool": "browser_batch",
        "chainable": ["navigate", "computer", "find", "form_input", "resize_window",
                      "tabs_select", "javascript_tool"],
        "streak": 3,
        "require_scale": True,
        "suggested_scale": 0.5,
        "read_tools": ["get_page_text", "read_page"],
        "warn_reads_without_max_chars": True,
        "block_capture_when_hidden": True,
        "state_days": 7,
    },
    "statusline": {
        "path_segments": 2,
        "ignore_untracked": True,
        "show_context": True,
    },
}


def claude_dir():
    """The Claude Code config folder: CLAUDE_CONFIG_DIR if set, else ~/.claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def config_path():
    env = os.environ.get("CONTEXT_ECONOMY_CONFIG")
    return Path(env).expanduser() if env else PACKAGE_DIR / "config.json"


def _strip_comments(d):
    if isinstance(d, dict):
        return {k: _strip_comments(v) for k, v in d.items() if not str(k).startswith("_")}
    return d


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_CONFIG = None


def config(section=None):
    """The merged config, or one section of it. A broken config file falls back to DEFAULTS."""
    global _CONFIG
    if _CONFIG is None:
        user = {}
        try:
            with open(config_path(), encoding="utf-8-sig") as fh:
                user = _strip_comments(json.load(fh))
        except Exception:
            user = {}
        _CONFIG = _merge(DEFAULTS, user if isinstance(user, dict) else {})
    return _CONFIG.get(section, {}) if section else _CONFIG


def chars_per_token():
    try:
        v = float(config().get("chars_per_token") or 2.1)
        return v if v > 0 else 2.1
    except Exception:
        return 2.1


def tokens(text):
    """Estimated tokens of a text."""
    return int(len(text or "") / chars_per_token())


def chars_to_tokens(chars):
    return int((chars or 0) / chars_per_token())


def state_dir():
    """Per-session state lives next to the config file, in state/."""
    d = config_path().parent / "state"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def safe_id(value):
    s = "".join(ch for ch in str(value or "no_session") if ch.isalnum() or ch in "-_")
    return s[:80] or "no_session"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


def write_json(path, data):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(str(tmp), str(path))
    except Exception:
        pass


def prune(folder, prefix, days):
    """Delete state files older than `days`: there is one per session."""
    try:
        cut = time.time() - days * 86400
        for p in Path(folder).iterdir():
            if p.name.startswith(prefix) and p.stat().st_mtime < cut:
                p.unlink()
    except Exception:
        pass


def read_payload():
    """The hook JSON from stdin, or {} if it cannot be parsed."""
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def emit(obj):
    out = json.dumps(obj, ensure_ascii=False)
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.flush()
    else:
        sys.stdout.write(out)


def pre_tool(decision=None, reason=None, context=None, updated_input=None):
    """Emit a PreToolUse answer. With no decision it only adds context for Claude."""
    out = {"hookEventName": "PreToolUse"}
    if decision:
        out["permissionDecision"] = decision
    if reason:
        out["permissionDecisionReason"] = reason
    if context:
        out["additionalContext"] = context
    if updated_input is not None:
        out["updatedInput"] = updated_input
    emit({"hookSpecificOutput": out})


def add_context(event, text):
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def parse_ts(ts):
    """Epoch seconds from an ISO timestamp of the transcript, or 0."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def last_compaction(transcript):
    """Epoch of the last compact_boundary in the transcript, or 0.

    Lines are filtered by substring before parsing: a transcript can weigh tens of MB.
    """
    last = 0.0
    try:
        with open(transcript, "rb") as fh:
            for line in fh:
                if b"compact_boundary" not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                    last = max(last, parse_ts(d.get("timestamp")))
    except Exception:
        return 0.0
    return last


def drop_compacted(state, transcript):
    """Forget reads older than the last compaction: the summary replaced that text."""
    if not state or not transcript:
        return state
    cut = last_compaction(transcript)
    if not cut:
        return state
    return {k: v for k, v in state.items() if isinstance(v, dict) and v.get("ts", 0) > cut}
