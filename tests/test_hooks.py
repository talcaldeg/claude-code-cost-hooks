#!/usr/bin/env python3
"""Tests for the installer and the three hooks, in a throwaway Claude config folder.

    python tests/test_hooks.py

Standard library only. Exits 1 if any check fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
FAILED = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name)
    if not ok:
        FAILED.append(name)
        if detail:
            print("      " + detail.replace("\n", "\n      "))


def run(args, env, stdin=None, cwd=None):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, cwd=str(cwd or REPO), timeout=60)
    return (p.returncode, p.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
            p.stderr.decode("utf-8", "replace"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cch-test-"))
    cfg, home = tmp / "claude", tmp / "home"
    cfg.mkdir()
    home.mkdir()
    env = dict(os.environ)
    env.update({"CLAUDE_CONFIG_DIR": str(cfg), "HOME": str(home), "USERPROFILE": str(home),
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    settings_path = cfg / "settings.json"
    settings_path.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
    target = cfg / "context-economy"

    def install(*args):
        return run([PY, str(REPO / "install.py")] + list(args), env)

    def settings():
        return json.loads(settings_path.read_text(encoding="utf-8"))

    def commands(s):
        out = []
        for event, groups in (s.get("hooks") or {}).items():
            for g in groups:
                for h in g.get("hooks", []):
                    out.append((event, g.get("matcher"), h.get("command", "")))
        return out

    try:
        # --- installer
        rc, out, err = install("--dry-run")
        check("dry run writes nothing", rc == 0 and not target.exists()
              and settings() == json.loads(settings_path.read_text(encoding="utf-8")), out + err)

        rc, out, err = install()
        s = settings()
        ours = [c for c in commands(s) if "/context-economy/hooks/" in c[2].replace("\\", "/")]
        scripts = sorted(Path(c[2].replace('"', " ").split()[-1]).name for c in ours)
        check("install adds exactly the two guards",
              rc == 0 and scripts == ["background_process_guard.py", "subagent_model_guard.py"],
              "%s\n%s%s" % (scripts, out, err))
        check("install keeps the user's own hooks and keys",
              s.get("model") == "opus" and ("Stop", None, "echo mine") in commands(s))
        check("install sets the status line",
              "statusline.py" in (s.get("statusLine") or {}).get("command", ""))
        check("install copies config.json", (target / "config.json").is_file())
        check("install makes a backup", any(cfg.glob("settings.json.bak-*")))

        rc, out, err = install()
        check("second install changes nothing", rc == 0 and "already up to date" in out, out + err)

        # --- hooks
        def hook(script, payload):
            return run([PY, str(target / "hooks" / script)], env,
                       stdin=json.dumps(payload).encode("utf-8"), cwd=tmp)

        def decision(result):
            try:
                return json.loads(result[1])["hookSpecificOutput"]
            except (ValueError, KeyError):
                return {}

        def bg(cmd, tool="Bash"):
            return hook("background_process_guard.py",
                        {"session_id": "t", "tool_name": tool, "tool_input": {"command": cmd}})

        blocked = [("sleep 100 &", "Bash"), ("python a.py > log.txt 2>&1 &", "Bash"),
                   ("nohup python a.py", "Bash"), ("setsid ./run.sh", "Bash"),
                   ("Start-Job { 1 }", "PowerShell"),
                   ("Start-Process python -ArgumentList a.py", "PowerShell")]
        allowed = [("make && echo done", "Bash"), ("cmd 2>&1 | tee out.txt", "Bash"),
                   ("echo 'a & b'", "Bash"), ("ls &> out.txt", "Bash"),
                   ("Start-Process python a.py -Wait", "PowerShell"),
                   ("Start-Process report.xlsx", "PowerShell")]
        bad = [cmd for cmd, tool in blocked
               if decision(bg(cmd, tool)).get("permissionDecision") != "deny"]
        bad += [cmd for cmd, tool in allowed if bg(cmd, tool)[1].strip()]
        check("background guard blocks & / nohup / Start-Job and passes the rest", not bad,
              "\n".join(bad))

        agents = cfg / "agents"
        agents.mkdir()
        (agents / "withmodel.md").write_text("---\nname: withmodel\nmodel: sonnet\n---\nb\n",
                                             encoding="utf-8")

        def sub(inp):
            return hook("subagent_model_guard.py",
                        {"session_id": "t", "tool_name": "Agent", "tool_input": inp})

        check("subagent guard denies an Agent call without model",
              decision(sub({"prompt": "p"})).get("permissionDecision") == "deny")
        check("subagent guard passes an explicit model", not sub({"prompt": "p", "model": "haiku"})[1].strip())
        check("subagent guard passes a fork", not sub({"prompt": "p", "subagent_type": "fork"})[1].strip())
        check("subagent guard passes an agent whose definition sets model",
              not sub({"prompt": "p", "subagent_type": "withmodel"})[1].strip())
        (target / "config.json").write_text(json.dumps({"subagent_model": {"default_model": "sonnet"}}),
                                            encoding="utf-8")
        d = decision(sub({"prompt": "p"}))
        check("subagent guard fills in default_model",
              d.get("permissionDecision") == "allow" and d.get("updatedInput", {}).get("model") == "sonnet",
              str(d))

        transcript = tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 1000, "cache_read_input_tokens": 84000}}}) + "\n", encoding="utf-8")
        rc, out, err = run([PY, str(target / "hooks" / "statusline.py")], env, cwd=tmp,
                           stdin=json.dumps({"workspace": {"current_dir": str(tmp)},
                                             "model": {"display_name": "Opus"},
                                             "transcript_path": str(transcript)}).encode("utf-8"))
        check("status line shows model and context", rc == 0 and "Opus" in out and "ctx 85k" in out,
              repr(out) + err)

        # --- uninstall
        rc, out, err = install("--uninstall")
        s = settings()
        check("uninstall removes ours and keeps the user's",
              rc == 0 and not any("context-economy" in c[2] for c in commands(s))
              and ("Stop", None, "echo mine") in commands(s) and "statusLine" not in s, out + err)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    print("\n%d failed" % len(FAILED) if FAILED else "\nall checks passed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
