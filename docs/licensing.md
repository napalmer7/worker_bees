# Licensing rationale

License selection: AGPL-3.0-only, approved by the repository owner on October 30, 2026.

## Intended outcome

Allow general use, including commercial use, while preserving source availability for covered
improvements. Hosted MCP use makes network reciprocity relevant: distribution-only copyleft
would not address every hosted-service modification.

AGPL-3.0-only is the selected license. The unmodified canonical text is in LICENSE. The
repository declaration selects version 3 only, rather than automatically allowing later
versions. The license applies to original code and accompanying documentation unless stated
otherwise; it does not relicense third-party dependencies or material.

## What reciprocity means

- Conveying covered software can trigger source and licensing obligations under the AGPL.
- Section 13 addresses a modified program that supports remote network interaction: its users
  must receive a prominent opportunity to obtain the Corresponding Source of that version,
  at no charge, through a network source offer.
- These are not blanket duties to publish every private edit to the world. The triggering use,
  covered work, and recipients matter.
- They do not require a pull request, assignment of copyright, or acceptance of changes into
  the original repository. The contribution guide invites upstream improvements separately.

The authoritative license text controls. This short explanation is project guidance, not a
substitute for analyzing a particular derivative work, combination, or deployment.

## Alternative and tradeoff

MPL-2.0 is an alternative when wider proprietary embedding is the priority. It generally
preserves source availability for modified covered files when distributed, while allowing
larger works with different licensing. It does not provide AGPL's network-interaction rule.
That would favor adoption at the cost of weaker reciprocity for hosted modifications.

A custom mandatory-upstream-submission condition would need separate legal analysis and would
not be the unmodified AGPL or MPL. This proposal adds no such condition.

## Contribution ownership and commercial options

Contributors retain their copyrights and submit work under the project license. No CLA or
copyright assignment is introduced. A later proprietary dual-license offering may require
additional permission from all relevant copyright holders. Paid hosting, support, and services
are compatible with open-source licensing; charging does not remove compliance duties.

## References

- GNU AGPL v3, including section 13: https://www.gnu.org/licenses/agpl-3.0.html
- Canonical license text: https://www.gnu.org/licenses/agpl-3.0.txt
- Mozilla MPL FAQ: https://www.mozilla.org/en-US/MPL/2.0/FAQ/
