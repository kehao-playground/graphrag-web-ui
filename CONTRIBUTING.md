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
  in **English**. English is the primary and authoritative version.
- Traditional Chinese (zh-TW) translations may be added later under
  `docs/zh-TW/`; they follow the English version, which stays authoritative
  when they drift.
- Historical documents written in zh-TW before this convention (the
  Foundation-A spec and plan) remain as-is until replaced; do not
  mass-translate them retroactively.

## Code Comments

- Write code comments, docstrings, and identifiers in **English**.
- When you touch a file that still carries zh-TW comments, migrate the
  comments of the lines/sections you are modifying (boy-scout rule).
  Do not do repo-wide comment rewrites on their own.

## Note for AI Agents

Every implementation plan for this repo must carry these rules in its
Global Constraints so task-level commits and comments comply without
relying on session memory.
