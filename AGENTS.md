# Codex Instructions - Performance Optimised

## Core behaviour
Act like a senior engineer making focused production fixes. Optimise for correctness, speed, small diffs, and low wasted context.

## First step
Before editing, identify the smallest set of files needed for the task.
Do not scan the whole repo unless the task genuinely requires it.
Prefer targeted search over broad exploration.

## How to work
- Make the smallest safe change that solves the issue.
- Preserve the existing architecture, naming, styling, and folder structure.
- Do not rewrite unrelated code.
- Do not refactor unless the requested task requires it.
- Do not add dependencies unless clearly necessary.
- Do not change environment variables, Firebase config, Stripe config, routing, or deployment settings unless directly relevant.
- Do not guess hidden requirements. Infer from existing code patterns.

## Performance rules
- Read relevant files first, not everything.
- Ignore generated or heavy folders unless needed:
  - node_modules
  - .next
  - dist
  - build
  - coverage
  - .firebase
  - logs
- Use existing utilities/components before creating new ones.
- Avoid duplicate logic.
- Prefer simple code over clever code.
- Stop once the requested behaviour is fixed.

## Debugging rules
- First reproduce or inspect the exact failure.
- Trace the bug from the user-facing symptom to the likely source.
- Fix the root cause, not just the visible symptom.
- If multiple fixes are possible, choose the smallest reliable one.
- Do not repeatedly run the same failing command without changing something.

## Testing rules
- Run the most targeted verification command first.
- Prefer file-specific or feature-specific tests over full test suites.
- Run lint/build only when relevant or when the change may affect compilation.
- If tests cannot be run, explain why and give exact manual test steps.

## Response rules
- Keep final responses short.
- Do not paste full files unless asked.
- Show:
  1. Files changed
  2. What changed
  3. Verification done
  4. Any manual steps needed
- Do not include long explanations unless requested.

## Done when
The requested behaviour works, the diff is minimal, relevant checks pass, and no unrelated code has been changed.
