# claude-code-cost-hooks

Three free hooks for Claude Code that stop the most common ways a session costs more than it
should. They work on Windows, macOS and Linux, use only the Python standard library and send
nothing anywhere.

| Piece | Event | What it does |
|---|---|---|
| `subagent_model_guard.py` | PreToolUse `Agent` | Blocks a subagent launched without `model` (or fills in your default), so it does not silently inherit the most expensive model |
| `background_process_guard.py` | PreToolUse `Bash\|PowerShell` | Blocks `&`, `nohup`, `Start-Job`: processes the harness cannot list, stop or kill |
| `statusline.py` | status line | Folder, git branch, model and the current context size, the number every next call pays again |

## Why hooks and not rules

In Claude Code the expensive part is not a tool call, it is re-reading the context: everything
that lands in the conversation is sent again on every later API call of the session. Rules
written in `CLAUDE.md` ("use a cheaper model for subagents") did not change behaviour in the
sessions these hooks were measured on: the shortcut kept winning. A hook does change it, because
it acts at the moment of the call.

- **Subagents.** A subagent without `model` inherits the main model. In the measured setup,
  with a written rule to send search to Haiku, 90 of 126 subagent calls in 30 days set no model
  and none went to Haiku.
- **Background processes.** A `&` inside a normal shell call once left five scripts running for
  20 hours at 100 % CPU that nobody could see or stop. `run_in_background: true` gives each
  process an id, a completion notice and a way to stop it.
- **Context size.** You cannot manage what you do not see. The status line shows the context of
  the last call, read from the transcript.

## Installation

Requirements: Claude Code and Python 3.8 or newer (no packages).

```bash
git clone https://github.com/talcaldeg/claude-code-cost-hooks
cd claude-code-cost-hooks
python3 install.py --dry-run
python3 install.py
```

On Windows use `python` instead of `python3`. Then restart Claude Code.

The installer copies the scripts to `~/.claude/context-economy/` (or
`$CLAUDE_CONFIG_DIR/context-economy/`), creates `config.json` there, and adds its entries to
`settings.json` without touching anything that is already there, after saving a timestamped
backup. It is idempotent and keeps your status line if you have one (`--replace-statusline`
swaps it; uninstalling restores yours).

```bash
python3 install.py --uninstall
```

## Configuration

`~/.claude/context-economy/config.json`, documented key by key. The two you are most likely to
touch:

| Key | Default | Meaning |
|---|---|---|
| `subagent_model.default_model` | `null` | `null` blocks; `"sonnet"` or `"haiku"` fills the model in instead |
| `statusline.path_segments` | `2` | How many trailing folders the status line shows |

Every hook has `"enabled": true` to switch it off without uninstalling, and exits silently on
any error: a broken hook never blocks a tool call.

## Example

A subagent launched without a model:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "BLOCKED: an Agent call without `model` inherits the main session's model, usually the most expensive one. Pick by task: `haiku` if it only locates or searches, `sonnet` for long mechanical work (edit, extract, transform), and an explicit `opus` only when the subagent plans, audits or reaches a conclusion that matters. ..."}}
```

The status line:

```text
projects/api | main* | Opus | ctx 85k
```

## Tests

```bash
python3 tests/test_hooks.py
```

They run in CI on Ubuntu, macOS and Windows with Python 3.8 and 3.12.

## The rest of the set

These three come from a larger set, built and measured on the author's own daily sessions. The
background, the numbers and what worked are in a free article series,
[Context economy in Claude Code](https://talcaldeg.github.io/articulos/context-economy/), and
in a free chapter,
[Claude Code token costs: where they go and what works](https://talcrafter.gumroad.com/l/claude-code-token-costs).

The full set is the [Claude Code Cost Kit](https://talcrafter.gumroad.com/l/claude-code-cost-kit)
(paid): these three plus a guard against reading the same big file twice, a nudge when a fresh
session would be cheaper, a watch on what your skills cost in the fixed context, an inline
Python guard, a browser screenshot guard, a skill that audits your own transcripts and a
document style checker. Its installer uses the same folder, so it replaces this one cleanly.

## License

MIT. See [LICENSE](LICENSE).
