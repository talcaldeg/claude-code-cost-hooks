#!/usr/bin/env python3
"""Install or remove the context-economy hooks and status line.

    python install.py                 install (or update) into ~/.claude or CLAUDE_CONFIG_DIR
    python install.py --dry-run       show what would change, write nothing
    python install.py --uninstall     remove the hook entries and the copied scripts
    python install.py --uninstall --purge   also delete config.json and state

Other options:
    --config-dir DIR        install into another Claude config folder
    --python CMD            interpreter for the hook commands (default: the one running this)
    --no-statusline         leave the status line alone
    --replace-statusline    replace an existing status line (it is restored on uninstall)

The scripts are copied to <config>/context-economy/. Existing settings.json entries are never
touched: only entries whose command points into context-economy/hooks/ are added, updated or
removed, so running it twice changes nothing. settings.json is backed up before any write.
Standard library only, Python 3.8+.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDER = "context-economy"
MARKER = "/%s/hooks/" % FOLDER

# (event, matcher or None, script, args, timeout in seconds)
BROWSER = "{prefix}"
HOOKS = [
    ("PreToolUse", "Read", "large_read_guard.py", "", 10),
    ("PreToolUse", "Bash|PowerShell", "inline_python_guard.py", "", 10),
    ("PreToolUse", "Bash|PowerShell", "background_process_guard.py", "", 10),
    ("PreToolUse", "Agent", "subagent_model_guard.py", "", 10),
    ("PreToolUse", BROWSER + ".*", "browser_cost_guard.py", "", 10),
    ("PostToolUse", BROWSER + "(tabs_context|preview_start|computer|browser_batch)",
     "browser_cost_guard.py", "", 10),
    ("PostToolUseFailure", BROWSER + ".*", "browser_cost_guard.py", "", 10),
    ("PostToolUse", "Write|Edit", "skill_cost_watch.py", "--post", 15),
    ("UserPromptSubmit", None, "session_cost_nudge.py", "--prompt", 20),
    ("SessionStart", None, "skill_cost_watch.py", "--session-start", 15),
]

# This folder may hold only some of the scripts: register the ones present.
HOOKS = [h for h in HOOKS if (HERE / "hooks" / h[2]).is_file()]

# The plugin registers the guards itself and cannot set a status line:
# --statusline-only installs just the status line next to it.
if "--statusline-only" in sys.argv:
    sys.argv.remove("--statusline-only")
    HOOKS = []


def claude_dir(arg):
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(env).expanduser() if env else Path.home() / ".claude").resolve()


def posix(p):
    return str(p).replace("\\", "/")


def is_ours(command):
    return isinstance(command, str) and MARKER in command.replace("\\", "/")


def command_for(python, target, script, args):
    cmd = '"%s" "%s"' % (posix(python), posix(target / "hooks" / script))
    return cmd + (" " + args if args else "")


def browser_prefix(target):
    for name in ("config.json", "config.example.json"):
        try:
            with open(target / name, encoding="utf-8-sig") as fh:
                prefix = (json.load(fh).get("browser") or {}).get("tool_prefix")
            if prefix:
                return prefix
        except Exception:
            continue
    return "mcp__Claude_Browser__"


def strip_ours(settings):
    """Remove every hook entry that points into context-economy/hooks/. Returns a count."""
    removed = 0
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept = [h for h in group["hooks"] if not (isinstance(h, dict) and is_ours(h.get("command")))]
            removed += len(group["hooks"]) - len(kept)
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def add_ours(settings, python, target):
    prefix = re.escape(browser_prefix(target))
    hooks = settings.setdefault("hooks", {})
    groups = {}  # (event, matcher) -> group we created
    for event, matcher, script, args, timeout in HOOKS:
        if matcher:
            matcher = matcher.replace(BROWSER, prefix)
        key = (event, matcher)
        if key not in groups:
            group = {"matcher": matcher} if matcher else {}
            group["hooks"] = []
            hooks.setdefault(event, []).append(group)
            groups[key] = group
        groups[key]["hooks"].append({"type": "command",
                                     "command": command_for(python, target, script, args),
                                     "timeout": timeout})


def set_statusline(settings, python, target, replace):
    """Returns (settings changed?, previous foreign status line to keep, message)."""
    ours = {"type": "command", "command": command_for(python, target, "statusline.py", ""),
            "padding": 0}
    current = settings.get("statusLine")
    if current is None or (isinstance(current, dict) and is_ours(current.get("command"))):
        settings["statusLine"] = ours
        return None, "status line: installed"
    if replace:
        settings["statusLine"] = ours
        return current, "status line: replaced (the previous one is restored on uninstall)"
    return None, "status line: kept yours (use --replace-statusline to use ours)"


def copy_files(target, dry):
    files = sorted((HERE / "hooks").glob("*.py"))
    extra = [HERE / "config.example.json", HERE / "README.md"]
    if dry:
        return len(files) + len(extra)
    (target / "hooks").mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(str(f), str(target / "hooks" / f.name))
    for f in extra:
        if f.is_file():
            shutil.copy2(str(f), str(target / f.name))
    config = target / "config.json"
    if not config.exists():
        shutil.copy2(str(HERE / "config.example.json"), str(config))
    return len(files) + len(extra)


def load_settings(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def save_settings(path, settings, original_exists):
    backup = None
    if original_exists:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name("%s.bak-%s" % (path.name, stamp))
        n = 1
        while backup.exists():  # two writes in the same second must not overwrite a backup
            backup = path.with_name("%s.bak-%s-%d" % (path.name, stamp, n))
            n += 1
        shutil.copy2(str(path), str(backup))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(str(tmp), str(path))
    return backup


def summary(before, after):
    def ours(s):
        out = []
        for event, groups in (s.get("hooks") or {}).items():
            for g in groups if isinstance(groups, list) else []:
                for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                    if isinstance(h, dict) and is_ours(h.get("command")):
                        script = h["command"].replace("\\", "/").split(MARKER, 1)[1]
                        out.append("%s [%s] %s" % (event, g.get("matcher", "*"),
                                                   script.replace('"', "")))
        return out
    return ours(before), ours(after)


def main():
    ap = argparse.ArgumentParser(description="Install the context-economy hooks for Claude Code.")
    ap.add_argument("--config-dir")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--no-statusline", action="store_true")
    ap.add_argument("--replace-statusline", action="store_true")
    a = ap.parse_args()

    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or newer is needed.")
    cdir = claude_dir(a.config_dir)
    target = cdir / FOLDER
    settings_path = cdir / "settings.json"
    existed = settings_path.exists()
    try:
        settings = load_settings(settings_path)
    except Exception as e:
        sys.exit("settings.json could not be read (%s). Nothing was changed." % e)
    before = json.loads(json.dumps(settings))
    tag = "[dry run] " if a.dry_run else ""
    previous_file = target / "statusline.previous.json"
    notes = []

    strip_ours(settings)
    if a.uninstall:
        sl = settings.get("statusLine")
        if isinstance(sl, dict) and is_ours(sl.get("command")):
            previous = None
            if previous_file.exists():
                try:
                    with open(previous_file, encoding="utf-8") as fh:
                        previous = json.load(fh)
                except Exception:
                    previous = None
            if previous:
                settings["statusLine"] = previous
                notes.append("status line: previous one restored")
            else:
                del settings["statusLine"]
                notes.append("status line: removed")
    else:
        add_ours(settings, a.python, target)
        if not a.no_statusline:
            previous, msg = set_statusline(settings, a.python, target, a.replace_statusline)
            notes.append(msg)
            if previous and not a.dry_run:
                target.mkdir(parents=True, exist_ok=True)
                with open(previous_file, "w", encoding="utf-8") as fh:
                    json.dump(previous, fh, ensure_ascii=False, indent=2)

    was, now = summary(before, settings)
    changed = settings != before
    print("%sClaude config folder: %s" % (tag, cdir))
    if a.uninstall:
        print("%sremoving %d hook entries" % (tag, len(was)))
    else:
        n = copy_files(target, a.dry_run)
        print("%scopying %d files to %s" % (tag, n, target))
        for line in now:
            print("  %s" % line)
    for n in notes:
        print("%s%s" % (tag, n))

    if not changed:
        print("%ssettings.json: already up to date, not written" % tag)
    elif a.dry_run:
        print("[dry run] settings.json would be updated (a backup is made first)")
    else:
        backup = save_settings(settings_path, settings, existed)
        print("settings.json updated" + (" (backup: %s)" % backup.name if backup else ""))

    if a.uninstall and not a.dry_run:
        if a.purge:
            shutil.rmtree(str(target), ignore_errors=True)
            print("removed %s" % target)
        else:
            shutil.rmtree(str(target / "hooks"), ignore_errors=True)
            for name in ("config.example.json", "README.md", "statusline.previous.json"):
                try:
                    (target / name).unlink()
                except OSError:
                    pass
            print("removed the scripts; kept %s and state/ (use --purge to delete them)"
                  % (target / "config.json"))
    if not a.uninstall and not a.dry_run:
        print("Done. Restart Claude Code to load the hooks. Settings: %s" % (target / "config.json"))


if __name__ == "__main__":
    main()
