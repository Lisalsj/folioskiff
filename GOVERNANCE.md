# Governance

## Scope

This document describes how the personal open-source FolioSkiff project accepts changes, makes product decisions, and publishes releases.

## Maintainer

Lisalsj is the current maintainer and final decision maker. Contributors may propose changes through issues and pull requests; contribution does not imply an ongoing support obligation or repository access.

## Decision process

Routine fixes are decided through review of tests, user impact, privacy boundaries, maintainability, and alignment with the documented product scope. Material changes to stored data, networking, Agent behavior, plugin permissions, or release security require a written design note or architecture decision record before implementation.

When proposals conflict, the maintainer records the selected trade-off in the issue, pull request, or documentation. Compatibility and user-owned data take priority over convenience unless a major release explicitly documents a rebuild boundary.

## Releases

Releases follow Semantic Versioning, include a changelog, generated checksums, an SBOM, and verified Windows artifacts. Tags and public release artifacts are created only after local and continuous-integration quality gates pass.

## Security decisions

Security reports are handled privately under [SECURITY.md](SECURITY.md). Credentials, private papers, prompts, reading history, and backups are never accepted as public fixtures or diagnostics.

## Code of conduct enforcement

The maintainer enforces [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), documents proportionate actions when safe, and protects reporter confidentiality.
