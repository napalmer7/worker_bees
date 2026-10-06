# Skill: Safe change integration

Use when rebasing, resolving conflicts, combining parallel changes, or confirming merge/deployment.

1. Name source and target revisions and fetch current references. Confirm the workspace represents the revision you intend.
2. Treat each conflict resolution as authored code. Inspect the full resulting diff and intermediate commits where relevant; check for conflict markers and accidental omissions.
3. Confirm the intended range was replayed. Read operation counts against the expected range and compare patches/content; a success message is not sufficient.
4. For squash workflows, use target content to check whether work is already integrated. Use merge base to size a change and name both diff endpoints.
5. Inspect additions and deletions expected from the change. Test combined behavior on the integrated result where contracts can interact.
6. Confirm post-merge checks ran. State merge and deployment as separate outcomes.
7. Before removing workspaces or recovery artifacts, confirm valuable work is preserved and retrievable.
