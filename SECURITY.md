# Security

## Reporting a problem

Please report security problems privately, not in a public issue. Use GitHub's
**Report a vulnerability** button on the repository's Security tab. I will reply
as soon as I can.

## What this project holds

The repository contains code only. Keys, database logins and the notes
themselves are never committed; they live in the host's secret store and in a
separate private vault. If you find any secret or personal data in the
repository or its history, please report it the same way.

## Supported versions

Only the latest commit on `main` is maintained.

## Dependencies

Dependency versions are pinned in `constraints.txt` and updated through
Dependabot pull requests, which must pass the tests before they are merged.
