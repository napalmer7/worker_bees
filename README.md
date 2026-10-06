# Reusable Agent Team

A platform-neutral starter for coordinating multiple AI agent sessions with explicit ownership, durable handoffs, independent verification, and clear authority boundaries.

- `SPEC.md`: core responsibilities and coordination contract.
- `FIELD-GUIDE.md`: detailed portable rules distilled from the source specification.
- `roles/`: operational charters for coordinator, contributor, reviewer, and optional task agent.
- `skills/`: claim/handoff, verification, review, integration, and dispatch procedures.
- `profiles/`: Git worktree example and runtime configuration template.
- `templates/`: starter durable records for board, task, and decisions.

## Start small

Choose a coordinator, one contributor, and a reviewer. Select one durable source of truth for task ownership and one place for team notes. Complete a runtime profile, agree on authority and review policy, and then begin with one contributor. Add workers or mechanisms only when a concrete need justifies their coordination cost.

A runtime may not support atomic claims, separate workspaces, persistent files, or autonomous monitoring. A profile must disclose capabilities and fallback procedures. Local aliases such as WorkerBee/Queen/Forager/Inspector are optional and carry no extra authority.

## Source and adaptation

This is a generalized adaptation of the WorkerBee Swarm specification and related role/skill templates. Deployment identifiers, personal authority provisions, incident-specific measurements, and stack-specific commands were omitted or moved to profiles. The field guide retains the reusable failure mechanisms and operating rules; use a local appendix for evidence that should remain private or project-specific.

Before public distribution, select and add a license; this package currently makes no explicit reuse grant. Preserve local security and platform instructions when adapting these files.
