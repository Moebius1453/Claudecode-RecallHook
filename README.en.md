# Claudecode-RecallHook

[中文](README.md) | English

A local Markdown memory retrieval tool for Claude Code. A `UserPromptSubmit` hook retrieves notes related to the current input and attaches file paths with short descriptions for Claude to read as needed.

It supports Chinese note indexing, retrieval across multiple strategies, context budgets, and memory tiers. Deployment requires configuring corpus directories, initializing a database, and registering a hook.

## Features

- Index Markdown files with YAML frontmatter while keeping the source text in files.
- Combine trigger keywords, character n-grams, and description embeddings using reciprocal rank fusion (RRF).
- Fall back to TF-IDF when the embedding service is unavailable.
- Inject budgeted file pointers and short descriptions rather than entire documents.
- Manage tiers, triggers, deduplication, rate limits, and injection logs.
- Optionally extract turns from Claude Code transcripts, screen them with a local model, and review candidate cards before writing long-term Markdown notes.

Retrieval returns file-level pointers. Claude reads the source text as needed. Transcript capture and candidate screening are separate workflows; reviewed candidates can be organized into long-term notes.

## Requirements

Basic indexing and keyword retrieval require Python 3.11 or later, plus NumPy and PyYAML from `requirements.txt`. The code uses `tomllib`, which is not included in Python 3.10.

Vector retrieval requires a compatible local embedding service. The example uses `bge-small-zh-v1.5` with 512 dimensions. Candidate screening and automatic trigger generation require a separate local generation service. Model files and llama.cpp are installed separately.

Process management in `services.py` targets Windows. Compatible services can be started independently on other platforms, but full deployment on those platforms has not been verified.

## Minimal setup

Start with indexing and retrieval for existing Markdown notes. Transcript capture, candidate screening, and tier synchronization can be enabled later.

1. Copy `config.example.toml` to `config.toml` at the repository root. Back up an existing configuration before replacing it.
2. Set `path` in `[[corpus]]` to your notes directory and make sure `[db].path` points to a new database.
3. Relative paths are resolved against the repository root, not the terminal's current directory. The example's `memory` path refers to `memory/` inside the repository. Absolute paths are also supported.
4. Run these PowerShell commands from the repository root:

```powershell
python -m pip install -r ./requirements.txt
python ./src/init_db.py
python ./src/index_docs.py
python ./src/memctl.py status
python ./src/memctl.py query 示例项目
python ./src/hook_inject.py --prompt "示例项目如何部署" --no-log --raw --verbose
```

The last command previews output without writing injection logs. It does not verify actual hook delivery by Claude Code. The Chinese queries refer to the example note below.

Note: `memctl` query commands ensure the database schema, so they are not strictly read-only database probes. `index_docs.py --dry-run` currently does not count added or updated files; zero counts do not indicate that nothing has changed.

Give each note a clear `description` and a small set of specific triggers:

```markdown
---
name: example-project
description: 示例项目的部署约束与操作入口
metadata:
  type: project
triggers: [示例项目]
---

这里记录需要跨会话保留的事实、约束与原因。
```

The example describes deployment constraints and entry points for an example project. Its body holds facts, constraints, and reasons that should persist across sessions.

Supported `metadata.type` values are `user`, `feedback`, `project`, and `reference`. New files start in the `warm` tier. Vector indexing primarily uses descriptions rather than full-text chunks; missing descriptions reduce retrieval quality.

## Configure the hook

Replace the Python and script paths below with absolute paths on your machine, then merge the hook into `.claude/settings.local.json` in the project where you use Claude Code. Claude Code does not need to run in this tool's repository.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"C:/Python312/python.exe\" \"D:/Projects/Claudecode-RecallHook/src/hook_inject.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Preserve existing settings and other hooks. Do not use `async`: output must be attached to the current message. Exceptions cause the hook to exit silently rather than reject the input. Add `--verbose` temporarily to send diagnostics to stderr.

CC Switch and similar configuration tools may rewrite user-level `~/.claude/settings.json` from their profiles and overwrite manually added hooks. Use project-level `.claude/settings.local.json`, or manage user-level hooks through the switching tool. Project-level settings apply only to that project.

Avoid registering the same hook in multiple scopes. After configuration, submit a query with a known match in an interactive session and confirm the attached context. No matches, rate limits, deduplication, or insufficient budget can all result in no output.

## Optional local models

The `[services]` section in `config.example.toml` defines `llama_server`, `embed_model`, and `qwen_model` paths. Set them to actual files before using the Windows service launcher:

```powershell
python ./src/services.py start --only embed
python ./src/services.py status
```

The launcher uses fixed ports: 8765 for embeddings and 8080 for generation. Changing the `[embed]` or `[llama]` endpoint does not change the launcher's ports. Alternatively, manage services independently and configure their compatible endpoints.

Routine retrieval does not require the generation model to remain running. `screen.py` and `gen_triggers.py` require it; model files are supplied by the user.

Note: `services.py stop --only qwen` currently does not stop only qwen; the stop implementation does not filter by `--only`. Do not use it to selectively stop a service. Avoid `stop --all` in a shared model environment.

## Tiers and maintenance

| Tier | Behavior |
|---|---|
| `always` | Synchronize entries into the managed section of `MEMORY.md`; bypass the hook |
| `warm` | Retrieve pointers based on relevance |
| `resting` | Retain limited trigger-based retrieval |
| `archived` | Exclude from automatic injection |

Some budgets and thresholds are stored in the database's `meta` table rather than TOML. By default, the hook selects at most five pointers within a total character budget. The header containing corpus paths also counts toward the budget, so fewer than five pointers may fit.

`capture.py`, `screen.py`, and `gen_triggers.py` write to the database. `tier.py` may also update the managed section of `MEMORY.md`. Back up data and verify target paths before migrations, bulk capture, tier changes, or note updates.

Markdown is the source of note content, but the database also holds manually assigned triggers, tiers, queues, and audit state. These may not be fully recoverable from Markdown. Backups should include both content and state.

## File structure

```text
Claudecode-RecallHook/
  README.md
  LICENSE
  config.example.toml
  requirements.txt
  src/
  scripts/
```

`src/` contains command-line entry points and the `memlib/` modules. `scripts/card-distribution.py` reports candidate card distributions.

Runtime databases are stored in `data/`, models in `models/`, and service logs in `logs/`. These directories and the local `config.toml` are ignored by Git.

## License

See [LICENSE](LICENSE) for the repository license. Third-party models and runtimes are subject to their respective licenses.
