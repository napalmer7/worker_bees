# Optional profile: Git and worktrees

Git-specific practices for separate agent workspaces. Adapt paths, default branch, host commands, and policy. This profile does not replace repository security instructions.

## Isolation
- Give each concurrent contributor a separate worktree or clone; review from a separate checkout where practical.
- Fetch before creating a branch from the remote target. Fetch updates references, not an existing worktree.
- Avoid `git stash` as coordination in a common repository: stash state is repository-wide. Prefer commits or another explicit recoverable mechanism.
- Before removing a worktree, inspect uncommitted/unpushed work and preserve anything valuable. Check shared links and dependency directories before cleanup.

## Integration
- Inspect conflict resolutions as authored changes. A successful rebase does not prove the intended commits were replayed or the result is correct.
- Ancestry alone cannot establish that a squash-merged change is present. Compare content with the target too; use the merge base to size changes and the target tip/tree to check presence.
- Verify integrated content, including expected additions and deletions. Run relevant checks on the merged result where changes may interact.
- Distinguish merge completion from deployment or runtime availability.

## Fill in before adoption
Document host feedback channels, branch protection, approval freshness, CI checks, deployment path, default branch, and shell/filesystem hazards. Re-check these facts when configuration changes.
