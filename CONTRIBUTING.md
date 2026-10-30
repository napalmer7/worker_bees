# Contributing to Worker Bees

Please bring general fixes and improvements back to
https://github.com/napalmer7/worker_bees so the community can benefit from one maintained core.

## Proposing work

For a bug, include the affected revision, expected behavior, observed behavior, and a minimal
reproduction. For a larger feature or architecture change, open an issue explaining the use
case, scope, and validation approach before investing in a large implementation.

Keep pull requests focused. Explain the behavior being changed, include regression tests
where they establish the behavior, and report checks run and their limitations. Update
documentation when public behavior or a claimed capability changes.

```bash
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest -q
uv build
```

Repository indexing uses committed source. Commit fixture changes in the test repositories
before expecting the indexer to observe them. Preserve unresolved results and partial-coverage
reporting rather than turning uncertainty into an unsupported success claim.

## Contribution licensing

By intentionally submitting a contribution for inclusion in Worker Bees, you agree to license
that contribution under this project's GNU Affero General Public License, version 3 only
(`AGPL-3.0-only`), unless a separate written agreement with the maintainers states otherwise.
You retain copyright in your contributions. Submit only work you have the right to contribute,
and preserve applicable third-party copyright and license notices.

No separate contributor license agreement is required. Acceptance of contributions
under AGPL does not automatically grant the maintainer the right to relicense those contributions
under a proprietary license.

## Upstream contributions and license compliance

Sending a pull request to the core repository is encouraged. It is not an additional condition
of the AGPL license. Source-sharing duties depend on the license and how the covered software
is conveyed or offered over a network, not on whether an upstream pull request was opened.

Maintainers review proposed contributions and decide what to merge. A contribution or review
does not itself grant release, deployment, or repository administration authority.
