# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

**The test:** Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Project-specific (serverops)
- **Plan/explain before editing**, then proceed step-by-step. For non-trivial work, walk through the change before applying it.
- **Mark task-related changes** with the task marker in a comment (e.g. `# POINT 19:` / `# POINT 20:` / `# POINT 21:`) so edits are traceable to the task.
- **Match existing conventions**: SQLAlchemy 2.0 `Mapped[...]` / `mapped_column`; Alembic migrations chained by number; config in `config/modules.yaml`; run with `PYTHONPATH` set to the serverops dir.
- **Side effects and telemetry must be fail-safe** — a logging/observability/integration failure must never break the pipeline (wrap in try/except; the pipeline is the priority).
- **Secrets** live in `.env` locally and Azure Key Vault in the cloud (via `app/secrets.py`). Never paste tokens/secrets into chat, and never commit them.
- **Verify after changes**: at minimum import-check / `py_compile` the touched modules; prefer a real run when the change is behavioral.
