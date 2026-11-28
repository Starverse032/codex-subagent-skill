# Codex handoff

Send a self-contained task. Codex gets the brief and the files it can access. Include the conversation decisions it needs.

```markdown
# Goal
Describe the requested result, current behavior and observable success criteria.

# Workspace and scope
- Specify the absolute working directory and relevant entry points.
- Name the files or components assigned to this task and existing changes to preserve.
- List relevant references, interface contracts and usage examples.
- State which dependencies, behavior and compatibility requirements apply.

# Acceptance
- List checks that establish the expected behavior and important edge cases.
- Identify required outputs and any evidence that needs human inspection.
- Report actual changes, commands and results, including checks that could not run.
```

Use a UTF-8 task file or pipe UTF-8 bytes to `--task-file -` for a substantial brief. Keep credentials out of the input and provide only the reference material needed for the task.

For parallel work, assign distinct file scopes and separate lanes. Serialize work that needs to edit the same files. For a follow-up, describe the remaining work and use the original thread UUID or lane.

Use a caller-authored [task spec](events-schema.md) for automated checks. The host verifies the receipt and compares the work with the acceptance criteria. For visual work include reference material and observable targets in the brief, then inspect the resulting visuals.
