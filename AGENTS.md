# Codex project instructions

Before doing project work, read this repository's entire CLAUDE.md and follow
its rules, handoffs, verification requirements, and operator-only boundaries.
CLAUDE.md remains the source of truth; do not edit or weaken it for Codex.
Read any nested CLAUDE.md that applies before working in its directory.

Use the installed Superpowers workflow and the docs-workflow,
context-management, and proto-workflow skills when applicable. Project rules
and client-data restrictions take precedence over skill suggestions.

The Codex guard translates supported tool calls to the existing Claude guards.
Never bypass a denial using another tool, shell indirection, a subagent, an
interactive shell, MCP, browser, network endpoint, or a sandbox override.
Do not change .codex, .agents, AGENTS.md, CLAUDE.md, existing guards, or their
registrations during ordinary project work. Policy maintenance is operator-only.
Do not use full access, disable hooks, or request unsandboxed execution.

Client-data roots are additionally denied by the filesystem sandbox. Even
sanctioned aggregate commands that need those roots must be handed to the
operator to run outside Codex. Read only their approved released aggregates.
Do not grant a sandbox exception just to run an aggregate command.

Hook trust is required: before project work, the operator reviews this project's
hook in /hooks. If hooks are missing, disabled, or untrusted, stop project work.
This integration targets local Codex CLI/desktop execution, not cloud execution.
Start Codex from this project directory. Do not pass --sandbox or --full-auto:
legacy sandbox flags override this project's named permission profile.

For Codex-authored commits, do not falsely attribute authorship to the Claude
model named in CLAUDE.md; retain the project's other commit conventions.
