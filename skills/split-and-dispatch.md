# Skill: Split and dispatch work

Use when parallelizing a task or sending bounded work to another agent.

1. Define outcome, acceptance evidence, constraints, dependencies, and human-reserved decisions.
2. Split by ownership of behavior/contracts, not only file location. Identify shared symbols, interfaces, generated outputs, and integration points.
3. Assign one owner per task and distinct task scopes. Check both file overlap and semantic overlap; identify cross-task integration tests.
4. For a short-lived helper, specify one output, inputs/references, constraints first, scope, recipient, and how uncertainty is reported. Do not delegate an unbounded investigation with a misleading repository boundary.
5. Write the brief durably before dispatch. Remember dispatched work cannot be recalled reliably; validate returned evidence and side effects independently.
6. At handoff, record base/head, completed slice, deferrals, checks, and next owner. Do not mark an umbrella task complete if only a slice shipped.
