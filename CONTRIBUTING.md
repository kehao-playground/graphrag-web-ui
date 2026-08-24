# Contributing

## Commit Messages

- Follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`.
- Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `ci`, `build`.
- Write commit messages (subject and body) in **English**.
- Subject: imperative mood, lowercase after the colon, no trailing period, ≤72 chars.

Example:

```
fix(api): scope login rate limit to (ip, email) and count failures only

All deployments put the API behind the web nginx tier, so keying the
sliding window on client IP alone collapses to one shared bucket.
```

## Documentation

- Write project documentation (specs, plans, README, runbooks, chart docs)
  in **English**; new documents are English. English is the primary and
  authoritative version.
- `docs/zh-TW/` exists and mirrors the root README: a README change
  updates its zh-TW mirror in the same PR. The English version stays
  authoritative when they drift.
- Historical specs/plans dated ≤2026-08-23 stay in their original
  language — they are records, not living docs; do not mass-translate
  them retroactively.

## Code Comments

- Write code comments, docstrings, and identifiers in **English**.
- When you touch a file that still carries zh-TW comments, migrate the
  comments of the lines/sections you are modifying (boy-scout rule).
  Do not do repo-wide comment rewrites on their own.

## Note for AI Agents

Every implementation plan for this repo must carry these rules in its
Global Constraints so task-level commits and comments comply without
relying on session memory.
