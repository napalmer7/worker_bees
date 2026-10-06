# Operational Field Guide

This file carries the source specification's reusable operating rules in implementation-neutral form. Concrete commands and repository-specific facts belong in a profile. Each rule is a prompt to establish evidence, not a substitute for judgment.

## A. Claims and evidence

### A1. Bind claims to an artifact
Name the artifact and revision for every state claim, measurement, review verdict, and test result. Prefer symbol or stable identifier plus file; line numbers are hints. Measurements expire when either the target or comparison reference changes. Re-read before relaying old results.

### A2. A description is a claim
Comments, documentation, test names, task text, release notes, and prior incident notes describe behavior; verify against the actual call path. When behavior changes, search for the distinctive old claim across the relevant repository and update every surviving copy. Confirm referenced test and skill names still resolve.

### A3. Bound every finding
State the population, scope, method, and exceptions beside a count or negative result. “Absent from these two branches” is not “absent.” Equal counts do not prove equal sets. Publish how a synthetic matrix was constructed; a count over a grid is a property of that grid.

### A4. Relay creates a new claim
A relay preserves source, revision, scope, uncertainty, and constraints. Attribute unverified findings to their author instead of restating them as fact. Re-measure at the revision relevant to the recipient. A peer cannot close a decision the human reserved for themselves. Put constraints before recommendations.

### A5. Corrections and retractions
A correction requires evidence too; confidence or recency does not prove it. Keep a visible record of the superseded claim when useful to prevent recurrence. Search for duplicated copies and correct them all. Raise the evidence bar for retracting a valid finding as well as for asserting one.

## B. Controls and instruments

### B1. Test the instrument's capability
Before using a result, name the question and confirm the instrument can answer it: correct target, revision, scope, configuration, runtime, and failure distinction. An excluded directory, wrong checkout, parse-only check, missing input, or conflated exit status can all look green. Distinguish zero results from query failure and inconclusive from pass.

### B2. Controls must execute and discriminate
A printed warning is not a gate. A required control aborts the action on failure. Establish that a mutation actually applied before interpreting survival; establish that a negative case arrived at the code in negative form. Demonstrate both a known-detectable case and a known-clean case through the same invocation path when validating a new detector.

A useful positive control shares the property that could hide the target (depth, repository, reference, construction, enabled rule, runtime, or boundary). A control that only proves the tool launched does not prove the target was observable.

### B3. Reconcile and inspect raw evidence
A control catches emptiness; reconciliation catches plausible but wrong non-empty results. Cross-check totals against parts, compare sorted failure sets rather than counts, and inspect raw output before trusting summaries. Two empty outputs compare equal; assert meaningful inputs and at least one expected difference when validating comparison machinery.

A second run of the same method may reproduce arithmetic while inheriting the wrong question. Corroboration uses a different source, predicate, or instrument capable of disagreeing.

### B4. Fail closed with distinct states
Checks that govern actions must enforce the result, including input errors and unavailable sources. Use separate outcomes for pass, detected problem, and unable to evaluate. Never map “could not compare” to “different” or “clean.” State the check's scope in the same breath as an all-clear.

## C. Change and test discipline

### C1. Verify the path users take
For a behavior change, identify the production entry point and verify through that path when practical. Directly testing an internal guard can miss unreachable or differently configured behavior. State why if the tested path differs. Check values after real defaults and setup, not only values a unit fixture can manufacture.

### C2. Exercise the contract, not just examples
When a contract relates multiple inputs, entry points, positions, keys, or representations, derive a test space from the actual vocabulary where practical. Exercise both sides of the boundary and both directions of a changed predicate. Include a known-correct/non-vacuity cell. Test whether structural and enumerated implementations drift; derive the candidate space rather than hand-listing only familiar cases. Hand enumeration is appropriate when the authority is external; name that authority.

A failure at one boundary should trigger a check of the matching boundary or mirror path. Check both expected presence and absence. A set of individually green changes can still fail after integration; test the combined result when shared contracts can interact.

### C3. Targeted mutations (optional technique)
Mutation is evidence for changed guards and predicates, not a volume contest. Limit it to changed behavior and run relevant tests fail-fast. A mutation record names the mutation, killing test, and observed failure; verify the edit applied. Cover the risky direction and meaningful inputs, not only reverting the change. A reviewer can spot-check a sample and add independent adversarial cases when a gap is plausible.

Time-box the work. If the mutation harness is too costly or unreliable, report that and improve its placement rather than presenting fabricated precision. Run the normal suite at the agreed points; avoid overlapping a mutation probe and background suite in one mutable workspace. Re-test only mutations affected by a later change.

### C4. Say what the test cannot show
A check's result applies only to its tested configuration and platform. A test environment that prevents the relevant race, loads a different module, omits a pipeline path, or uses a stub that avoids the operation is not evidence about the production case. Name the limitation and its consequence.

## D. Change review and integration

### D1. Review is revision-bound
A review pass and an approval describe a particular revision. Re-evaluate after relevant edits, feedback, base movement, or other configured trigger. Small exceptions may be extended only by a measured, documented policy that names both revisions and the instrument that supports the extension. Do not call an extension a fresh review.

### D2. Review the change set and its entry path
Run checks over the full changed set as the target CI would. Discover which gates actually run, block, and apply to this path; a green pipeline is not self-explanatory. Assign one independent reviewer per revision. Review findings state invariant and evidence separately from the suggested edit. Reviewers check the fix did not break a legitimate neighboring case.

### D3. Integrate by content
A conflict resolution is an authored change and deserves inspection. Success messages do not establish that the intended commit range was applied; inspect the range and compare before/after content. For squash-based workflows, ancestry can miss already-integrated content. Use the target tree/content to determine presence and the merge base to size the change; name both comparison points.

Verify expected additions and deletions in the integrated result. A merge is not deployment. Report checks that only run after merge and confirm they actually ran. If the base changes under an open change, evaluate the combined result when interactions can matter.

### D4. Reassignment and cleanup
Before reassignment, inspect the underlying branch or artifact and durable work record. A status summary is not proof that no work exists. Preserve single-copy work before deleting transient workspaces; confirm the saved artifact by content, then remove stale recovery artifacts. Check symlinks, junctions, shared dependencies, and repository-wide caches before cleanup.

## E. Durable coordination

### E1. Memory before message
Write result, finding, decision, or blocker to durable state before signalling. A delivery receipt says the transport accepted a message, not that the recipient read it. The recipient checks durable state on each cycle; silence means only that no signal was observed.

### E2. Keep asks actionable
Each unresolved human ask is one durable item with decision/action needed, evidence, owner if known, cost of delay, and next checkpoint. Separate a decision only the human can make from an action only they can perform. Preserve resolved items with why they closed. If publishing a rendered index, derive it from source items and validate the generated output against the source count.

### E3. One writer or safe concurrency
Give shared board and index artifacts one writer, or use a mechanism with real concurrency control. Participant-private logs can be append-only in each participant's area. Roster (who exists) and board (what they are doing now) are separate data with separate owners.

## F. Simplicity and learning

Prefer an arrangement that prevents an error over a reminder or complicated recovery, when it meets the requirement. Before adding machinery, state the requirement/evidence, supporting mechanisms, cleanup, and caller-visible failure states. Preserve necessary safeguards; fewer lines alone do not mean simpler.

Capture recurring failures by mechanism, not the tool on which they happened. Promote a new standing rule only when existing categories cannot express it. Promote project-specific facts to a profile and re-check facts that drift. An exception names the rule it changes, why, and who authorized it.

A process learns when findings change the next cycle: track external findings missed, false positives, checks available but skipped, and self-corrections. Calibrate without treating reviewer volume itself as waste.
