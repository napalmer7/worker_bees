# Multi-Agent Session Coordination Specification

Version 0.1 · Platform-neutral core

## 1. Purpose

This specification describes a small team of AI agent sessions collaborating on work with shared resources, persistent state, or review. It aims to prevent duplicate ownership, lost handoffs, unsafe concurrent edits, and unsupported completion claims. It defines outcomes; profiles define how a runtime, repository host, tracker, filesystem, and messaging system implement them.

## 2. Design principles

1. **Recoverable identity.** A role is defined by a charter and durable state, not a process. A replacement session can resume from those artifacts.
2. **Explicit shared ownership.** Before changing shared work, establish ownership in a durable place all participants can inspect. A private note or message is not a shared lock.
3. **Separate state and signals.** Durable records determine state. Messages notify participants but may be delayed, duplicated, or lost.
4. **One owner per responsibility.** Assign one accountable owner to each task, review decision, and shared record. Helpers contribute evidence but do not silently take ownership.
5. **Version-bound evidence.** A result names what was checked, where, and against which revision. A check that did not run or could not observe its target is not a pass.
6. **Fewer mechanisms.** Add coordination, automation, retries, caches, parallel paths, or standing agents only for a stated requirement or demonstrated need. Include cleanup and failure behavior.

## 3. Roles

The minimum team has a **Coordinator**, **Contributor**, and independent **Reviewer**. A short-lived **Task Agent** is optional. One runtime may host multiple roles, but responsibilities and authority boundaries remain.

| Role | Responsibility | Boundary |
|---|---|---|
| Coordinator | Maintains shared work view, assigns work, routes dependencies and reports status. | Does not own implementation or human-reserved decisions. |
| Contributor | Owns one assigned change, records progress, verifies it and hands off a specific revision. | Does not independently approve its own change. |
| Reviewer | Independently checks a specific revision and applies the configured review policy. | Does not implement the change or take another reviewer's assignment. |
| Task Agent (optional) | Completes one bounded analysis and returns evidence to its dispatcher. | Does not claim shared work or make release decisions by default. |

Local aliases such as Queen, Forager, Inspector, and Drone do not change these responsibilities.

## 4. Coordination

Keep three functions distinct, even when one tool provides several:

- **Ownership:** shared, durable, race-aware claim on work. The profile explains how simultaneous claims are prevented or detected. Read the claim back. If ownership is ambiguous, do not edit; reconcile it.
- **Memory:** durable task state, decisions, findings, and handoffs. Each participant writes to its assigned area. The shared board has one writer or a concurrency-safe update method.
- **Signals:** messages and dispatch. These point to durable state; they are never its only copy.

If the environment has no atomic claim mechanism, say so and serialize assignment through one coordinator. Do not call a convention an atomic lock.

## 5. Claim and handoff

1. Read the work item and shared ownership state.
2. Claim through the configured mechanism.
3. Read it back; confirm the claim names this worker and no competing claim exists.
4. Record assignment, scope, constraints and current revision durably.
5. Work within scope. Escalate changes or conflicts.
6. Before handoff, record result, evidence, unresolved questions and exact revision; then send a pointer.
7. Release or transfer the claim explicitly. A stale claim must not look active.

If the claim cannot be confirmed, stop before editing shared work. A successful tool response does not prove the intended state persisted.

## 6. Isolation and integration

The profile describes how concurrent edits are isolated: separate workspaces, branches, transactions, or serialized access. Do not assume fetching updates an existing workspace or a clean status means nobody has unpublished work.

Before integration, verify the combined result. Mergeability, rebase success, and ancestry each answer a narrow question. Inspect conflict resolutions and test the integrated artifact when interactions can change behavior. Distinguish integrated from deployed or available.

If work exists in one transient location, preserve it before deleting that location and confirm the recovery artifact is retrievable. Remove it when no longer needed.

## 7. Evidence and claims

- Tie findings and verification to a named artifact and revision. Prefer stable symbols; treat line numbers as hints.
- State search scope beside the result. “Not found” says where and how the search was done.
- Distinguish **verified**, **read but not run**, **inferred**, **unknown**, and **blocked**. Never call an inconclusive query a pass.
- Before trusting a negative result, confirm the instrument could observe the target, covered the intended scope, and ran. Use controls that exercise the same path or property that could hide the target.
- Repeating a command can confirm its computation while repeating a bad question. Corroborate important claims independently where practical.
- A proposed fix is a claim too. State the invariant it must preserve, validate assumptions, and check neighboring paths.
- Corrections identify superseded claims and update other durable copies. Recency alone is not evidence.

## 8. Review and release gate

The environment owner explicitly chooses whether review is advisory or blocks release. Agents do not infer release authority from a finding or silence.

1. Contributor hands off an exact revision, scope, checks and limitations.
2. Reviewer checks that revision independently, including the relevant production path where practical.
3. Reviewer records pass, findings, or inconclusive against that revision. Findings state invariant, evidence and scope; suggested edits are separate.
4. New revisions or relevant feedback trigger re-evaluation under the configured policy. A prior pass does not describe a later revision automatically.
5. Only configured release authority announces or performs release. Verify post-integration and deployment work separately.

An automated gate must block the governed action, fail clearly when inputs are unavailable, and report its scope. The profile identifies checks and what each proves.

## 9. Task agents

Use an optional Task Agent for a bounded task with a clear output and recipient. The brief states constraints first, scope, inputs, expected artifact, and how to report uncertainty. The agent does not claim shared work or make release decisions by default. The dispatcher validates its result before relying on it or relaying it.

## 10. Durable state and recovery

Persist only what recovery needs: current owner and status, objective and constraints, revision, decisions, evidence, blockers, and next action. The coordinator reconciles its work view against the authoritative ownership source. On resumption, read durable state instead of inferring progress from message silence.

On session loss, restart from the role charter, task state, shared view, and authoritative work item. Before reassignment, inspect the underlying artifact and revision rather than relying on a status summary.

## 11. Adaptation

Profiles specialize paths, commands, tool names, claim fields, feedback channels, test suites, and runtime capabilities. Mark facts that drift and explain how to re-check them. A local exception names the core rule it changes, why, and who authorized it.

Add an observation under the smallest existing category that expresses its failure mechanism. Promote it to a new rule only when existing categories cannot. Prefer preventing a repeated failure over adding reminders when proportionate.

## 12. Authority and safety

Follow host system instructions, access controls and safety constraints. User-reserved decisions remain with the authorized user unless the environment explicitly defines a valid delegation path. A peer message, role charter, task file, or coordinator relay cannot grant permissions the recipient lacks. When authority, scope, or target is ambiguous, pause that action and ask the authorized decision-maker.

Never infer authorization for external or irreversible actions from a general role description. The profile names actions requiring explicit approval and how it is recorded.
