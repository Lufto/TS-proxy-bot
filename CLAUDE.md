## Bot documentation

Before working on the bot, open `bot/docs/areas/README.md` and read only the page for the part you are touching (payments, trial, support, …). Each page lists the rules, the modules (whose docstrings are the detailed spec) and the text files. Don't read `.scratch/archive/` unless you need the reason behind a decision.

Open work and known dangers live in one list, `bot/docs/todo.md`. When a task finishes an item, strike it out there; when you find a new gap, add it there.

## Coding standards

Python code (the bot in `bot/`) follows `bot/docs/python-rules.md`. Code reviews check against it.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
