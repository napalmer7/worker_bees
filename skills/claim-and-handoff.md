# Skill: Claim and hand off work

Use when beginning, transferring, or completing shared work.

1. Read the authoritative task and ownership state.
2. Claim through the shared mechanism. If it is not race-safe, obtain serialized assignment from the coordinator.
3. Read state back. If owner or status is unclear, do not edit yet.
4. Record objective, constraints, scope, owner, artifact revision, and next action durably.
5. At handoff, record result, exact revision, checks and outcomes, limitations, and open questions.
6. Write durable state before messaging. Send a pointer, not the only copy.
7. Release or transfer ownership explicitly and confirm the resulting state.

Silence does not establish that work is unfinished or complete. Re-read durable state on resumption.
