# Project: Family Books

Family Books is a single-user, cloud-hosted personal finance system for the Rowe household.

## Primary specification
The authoritative build specification is `docs/BUILD_SPEC.docx`. Read it in full before making any design or implementation decisions. Do not deviate from it without explicit approval from the project owner.

## Working style
- Follow the staged build plan in Section 10 of the spec. Do not skip ahead.
- Before starting any stage, produce a task breakdown and wait for approval.
- Commit at every logical milestone with clear, descriptive messages.
- Run the test suite before moving between stages. Do not proceed on a red build.
- Keep README.md and user documentation updated as you build.
- Maintain a PROGRESS.md file; update it at the end of every session with current stage, what is complete, what is next, and any open questions.
- Never commit secrets, database files, backups, or imported financial data. The .gitignore already excludes these — do not relax it.

## Conventions
- Calendar-year fiscal year, USD only.
- Double-entry from day one. Every posted transaction is a balanced journal entry.
- Monetary amounts stored as fixed-precision decimal or integer cents — never floating point.
- Tests required for the accounting engine (minimum 80% coverage).

## Out of scope for v1
See Section 12 of the build spec. Do not implement anything listed there without explicit approval.

## Owner
Mark Rowe. When the specification is ambiguous, ask before guessing.
