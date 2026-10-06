# Skill: Review and release gate

Use when independent review is required before release or handoff to a human reviewer.

1. Contributor supplies exact revision, objective, constraints, changed scope, checks, and limitations.
2. Reviewer independently reads that revision and verifies relevant behavior through the actual entry path where practical.
3. Reviewer records a revision-bound verdict: pass, findings, or inconclusive. A finding names invariant, evidence, scope, and optional remedy.
4. Follow the environment's declared policy for whether findings block release. Do not infer hold authority from role names.
5. If artifact or relevant evidence changes, apply the configured re-review rule. Never silently reuse a pass for another revision.
6. Record integration and deployment separately from review. Verify the integrated result when required.

Minimal handoff:

```text
Artifact / revision:
Scope:
Checks run and result:
Known limits / not checked:
Verdict:
Findings (if any):
```
