# Changelog

All notable user-visible changes are recorded here. The project follows Semantic Versioning after the first public preview.

## [Unreleased]

## [3.2.0] - 2026-11-18

FolioSkiff brings a bilingual interface, faster reading briefings and a journal-style layout to the Windows research workspace.

### Upgrade

Back up the library, then install 3.2.0 manually once. Earlier builds check the previous repository. The Windows installer and data directory retain their existing identities. Subsequent release checks use the new FolioSkiff repository.

### Added

- Switch between Simplified Chinese and English from setup, navigation or reading settings. The choice persists across restarts and preserves drafts, filters and source passages.
- Choose Chinese or English when building a public reading list. Existing selections default to Chinese and keep their snapshot identities.

### Changed

- Search the library across pages when selecting papers for comparison and publication. Preserve active filters when opening a shared search URL.
- Load independent briefing data and batch the underlying queries.
- Use a quieter PDF workspace, clearer paper metadata and mobile controls that keep the reading area visible.
- Refresh the locked build and Python dependencies. The build-tool exception for picomatch 4.0.7 expires on 18 December 2026. [Dependency review](docs/security-privacy.md#picomatch-407)
- Remove an unused navigation component and duplicate API error handling. Artifact cleanup checks tracked paths and Windows junctions.

### Fixed

- Fix OpenReview API v2 search parameters, preserve all confirmed bilingual alternatives, and balance paginated retrieval across required concepts so a broad first concept cannot consume the entire scan budget.
- Explain empty recommendations with per-month/source date and keyword filter counts, source errors, and scan limits; show `partial_success` in Chinese and distinguish rerunning discovery from rescoring existing candidates.
- Reload recommendations when selecting a different run, keep filter tables keyboard accessible on small screens, and isolate Windows launcher tests from an already running application.
- Share authoritative filtering between previews and durable runs, preserve counts through resumable checkpoints, and guide keyword generation toward common English synonyms and optional method preferences.
- Reserve enough Ollama output for thinking and the final answer; detect exhausted budgets and return actionable model errors with distinct HTTP statuses.
- Discover each model's thinking controls, choose the first installed model when unset, and bound local context, output, and total generation time for all Agent tasks.
- Prevent cross-backend model inheritance and duplicate Cloud schema retries; log safe failure codes and usage counts.
- Update Vue, brace-expansion, source-map-js, and urllib3 to patched releases and exclude their vulnerable versions from the lockfiles.
- Keep the theme switch and task drawer controls beside each other so both remain clickable at every viewport size.

The following entries record development milestones before the first FolioSkiff release.

## 3.0.2 - 2026-09-29

### Added

- Add per-request Agent and model selectors to research keyword generation, including dynamically discovered Codex and Ollama models and reasoning strength.
- Add an explicit model invocation audit map covering keywords, reports, chat, translation, comparison, related intent, and external research actions.

### Fixed

- Bound Ollama keyword requests to a task context/output budget and accept thinking-only stream chunks so large-context models do not appear to hang while reserving memory.
- Show the selected and resolved backend/model while a keyword request is running and surface structured backend errors instead of a bare HTTP status.
- Keep model IDs scoped to their selected backend in Agent settings and synchronize release metadata to 3.0.2.

## 3.1.0 - 2026-09-29

### Added

- Route keyword suggestions through the configured Codex CLI or Ollama backend with strict bilingual schema validation, structured errors, provenance, and redacted invocation logs.
- Add bundled Codex CLI discovery, desktop-process status, a single data-root migration journal, and a keyboard-accessible three-state theme switcher.

### Changed

- Use `Documents/paper-reader` as the default data root and derive state, cache, library, and backups beneath it; legacy roots remain readable during migration.

### Fixed

- Accept the Windows legacy `state/Cache` layout during data-root migration and keep its cache tree separate from migrated state files.
- Keep the fixed Windows 11 cold-start gate at eight seconds while treating GitHub's 2-vCPU Windows Server runner as a bounded functional smoke environment; record the observed startup time, selected budget and failure stage instead of returning an opaque `startup_budget` result.
- Build the packaged SPA inside the fresh Windows release runner before freezing the application, and stop immediately when any native build command fails so later copy errors cannot hide the first cause.

### Security

- Upgrade `cryptography` to 50.x to resolve `PYSEC-2026-3552` in the locked Windows runtime.
- Upgrade PDF.js and patched JavaScript transitive dependencies, and disable PDF scripting at the loader boundary, to resolve the current npm audit findings including `CVE-2026-16633`.

### Changed

- Require a successful `main`-push CI run for the exact release SHA through the read-only GitHub Actions API, then reuse that evidence for manual/tag packaging instead of serially repeating the same hour-long quality gate.
- Split the complete Windows Python suite into two measured `-n2` shards and merge their coverage in the stable `Python 3.12 / Windows` gate, shortening normal CI wall time without raising the per-runner worker count or weakening total/core coverage thresholds.
- Run the Windows Python coverage suite in two phases: two workers for parallel-safe tests and an isolated serial phase with a bounded slow-runner lifecycle budget for resource-intensive or subprocess timing-sensitive contracts; prepare temp roots on clean checkouts, bound the backend CI job to 90 minutes, and wait for the application shell before foundational browser assertions.
- Run the daily browser gate alongside the backend gate and reserve installer, portable archive, SBOM, and source-free smoke generation for the explicit manual/tag release workflow, reducing normal CI latency and Windows Actions-minute usage without removing release verification.

## 3.0.0 - 2026-09-22

### Added

- Add a local-library search page with text, source, publication-date, favorite, and unread filters.
- Add a persisted language preference foundation; full page translations remain unfinished.
- Add automated consistency checks for application version metadata.

### Changed

- Standardize release metadata, Windows version resources, generated OpenAPI metadata, and package locks on 3.0.0.
- Add search navigation and a briefing search shortcut in the existing quiet-library visual system.
- Require release artifacts to carry manifest, SBOM, SHA-256, and GitHub attestation evidence.

### Remaining work before full-plan acceptance

- Large-component/service decomposition, complete runtime translations, saved queries, semantic search integration, and unified AI task interactions are not implemented by this change.
- The existing database migration and backup mechanisms are retained; a dedicated 2.0-to-3.0 upgrade acceptance matrix is still required.

## 2.0.0 - 2026-08-01

### Added

- Add a private local translation API for explicit English-to-Chinese jobs, persisted segment views, glossary updates, safe Markdown/HTML exports and browser printing.
- Add an explicit translation mode to the paper Assistant plus a paged bilingual reader with source/target/parallel views, OCR confidence, terminology controls, durable progress and reload-safe navigation.
- Add a confirmed, revisioned related-research intent contract plus separate persistence for verified paper candidates, external knowledge resources and resumable per-source runs.
- Add one quiet, optional reading-briefing hint derived only from bounded local-library overlap, cached DOI references, confirmed plan terms and explicit interests; it never calls a model or the network.
- Add a local-first related-search intent workflow with bilingual bounded drafts, explicit no-network Agent refinement, term-level diffs, optimistic confirmation and immutable executed revisions.
- Add bounded related-paper retrieval through the existing academic connectors and Work identity review, with partial-source results, cached-reference date exceptions and safe HTTPS paper links.
- Add explicit external knowledge-resource research with closed Agent output, independently verified public HTTPS URLs, bounded redirects/responses and reproducible provenance.
- Add the private staged related-resource API: local draft, optional Agent refinement, explicit intent confirmation, resumable per-source retrieval, separated paper/site results and verified save-to-library actions.
- Add an explicit “相关推荐” reader mode with editable bilingual concept groups, per-run network consent, durable recovery, separate paper/website result lists and a quiet local-library briefing entry.

### Changed

- Share semantic quiet-library page headers, notices, empty states, async boundaries and section navigation, initially consolidating the reading briefing and local library without changing their data flow.
- Isolate reader Work loading, reading-position persistence, annotations/migrations and responsive pane restoration into typed composables while keeping the existing three-column workflow and routes unchanged.
- Compose the reading workspace from focused library, document, mobile-navigation and research-sidebar panes, with report and Assistant content mounted only after first use.
- Add a verified PDF selection action bar that can open explain, ask or English-to-Chinese translation context, or save a highlight, without invoking a model until the user explicitly starts the task.
- Split PDF loading/rendering, search and verified selection into independently tested composables while retaining `PdfReader` as the stable public facade and keeping PDF.js lazy-loaded.
- Split comparison and privacy-whitelisted publishing views into typed feature components while keeping API, durable Job, retry and Git orchestration in their route pages.
- Make the unified paper Assistant the only conversation store; keep every `/api/v1/conversations` route as a storage-free API v1 adapter with the same private evidence-manifest boundary.

### Fixed

- Avoid an extra PDF mount when switching between document versions, and turn failed visibility-triggered position flushes into a private, retryable reader status.
- Keep externally selected explain/translate modes aligned with the Assistant's actual task state instead of changing only the visible mode control.
- Serialize rapid PDF replacements behind session cleanup and prevent stale repeated-query search results from replacing the newest result.
- Resume interrupted translations from committed segment state without repeating completed batches or fabricating a successful Agent run after a process stop.
- Keep the bilingual reader inside the application shell's single top-level `main` landmark so screen-reader navigation remains unambiguous.
- Freeze a first-use related-research reader scope directly from stored page evidence without creating a placeholder Assistant or model job.
- Give the paper Assistant a semantic level-two heading and keep its four task labels on one line in narrow reader sidebars.

### Security

- Escape paper and model text in self-contained HTML exports, enforce a closed Content Security Policy, sanitize Windows filenames and reject export paths that do not match their content hash.
- Pin knowledge-resource probes to prevalidated public DNS addresses while preserving certificate-checked TLS SNI; reject local/reserved targets, credentialed URLs, rebinding peers, unsafe redirects, shorteners, search pages and download mirrors.

## 1.0.0 - 2026-07-16

### Added

- The first stable Windows 11 x64 release of the local-first research-intent Agent, explainable paper recommender, PDF/OCR reading workspace and on-demand Codex/Ollama analysis application.
- A frozen API v1, database migration boundary and plugin protocol 1.x, plus a closed source-free installer/portable release layout with CycloneDX SBOMs and SHA-256 verification.
- Month-bounded discovery across arXiv, OpenReview, Europe PMC, IEEE Xplore and Springer Nature, with user-confirmed bilingual keyword concept groups, resumable source batches and partial-success recovery.

### Changed

- Treat hosted-runner, clean-VM administrator, keyed live-source and manual assistive-technology receipts as explicitly disclosed external limitations for the personal open-source release; known security findings, failed local gates or unverifiable artifacts remain release blockers.
- Preserve the previous `0.9.0` artifact only as an immutable upgrade-test input after the v1 security and dependency changes.

### Fixed

- Keep backup staging names short and random in the final backup directory instead of repeating the long user-visible archive name, preserving atomic publication on Windows systems without long-path support.

### Security

- Parse untrusted arXiv Atom XML with `defusedxml`, rejecting DTDs and entity expansion with a stable response error that never includes the response body.
- Replace dynamically assembled embedding-record placeholders with fixed SQL and a bound JSON array consumed through SQLite `json_each`.
- Audit the locked Python base and all extras against PyPI and OSV, audit npm dependencies, run medium/high Bandit, classify current-tree secret candidates and scan the complete Git history; no known dependency vulnerability, unclassified secret or unresolved medium/high static finding remained at freeze time.
- Keep v1.0 explicitly unsigned. SHA-256 and SBOMs establish artifact identity and dependency transparency, not publisher identity or Microsoft SmartScreen reputation.

## 0.9.0 - 2026-07-16

### Added

- A release-candidate data-integrity matrix covering every supported preview schema boundary, cross-format PDF/DOI/BibTeX convergence, immutable recommendation snapshots, Work-merge transactions and complete backup/restore relationships.
- A source-free Windows verification job that downloads the immutable release artifact on a fresh runner and validates portable, install, upgrade, uninstall, activation and crash-recovery behavior without Python, Node.js or Git.
- A shared Windows PowerShell 5.1 clean-smoke harness and offline Windows Sandbox template with read-only release input and a separate redacted-evidence output mapping.
- Deterministic CycloneDX 1.6 Python and npm SBOMs generated from the exact frozen-build environment and lock file, with schema, root-component, license and complete dependency-graph validation.
- A closed ten-file release bundle containing installer, portable ZIP, two SBOMs, capability matrix, changelog, release notes, unsigned notice, canonical release manifest and SHA-256 inventory.
- An offline release verifier that rejects missing, extra, duplicate, case-colliding, linked, CRLF-modified, size-drifted or hash-drifted artifacts.
- Frozen v1 database, OpenAPI and plugin-protocol compatibility contracts with semantic hashes and release-manifest cross-checks.

### Changed

- Keep 200,000-paper library queries and fuzzy identity candidate selection bounded by SQLite indexes and FTS5, with explicit lexical degradation instead of full-table scans.
- Use a responsive three-viewport reading journey with accessible Reka UI tabs, stable PDF position and route-aware focus management.
- Persist cancellation requests and resumable events atomically so the task drawer acknowledges cancellation without waiting for a worker checkpoint.

### Fixed

- Retry restore-journal atomic publication for at most one second when Windows reports transient scanner or sharing errors (`winerror` 5 or 32), while preserving immediate failure and safe rollback for every other error.
- Derive every official-source User-Agent from the release version instead of retaining preview-era `0.1` identifiers or the generic HTTP client identity.

### Security

- Bound untrusted PDF extraction, OCR and browser reading by page geometry, decoded text, object/line counts, image bytes, aggregate document budgets and cooperative time limits; reject the whole batch with stable path-free errors instead of publishing partial evidence.
- Disable PDF.js evaluation and XFA, require strict parser errors, cap document-wide search text, release inactive page caches and show a recoverable local safety-limit message without exposing PDF URLs or content.
- Block all non-loopback IPv4 and all IPv6 traffic for each tested executable during clean smoke, and fail closed when temporary firewall isolation cannot be established.
- Keep packaging PRs read-only and secret-free; grant `contents: write` only to the post-verification tag job, which creates a draft release that still requires maintainer confirmation.
- Keep v0.8 explicitly unsigned and explain that hashes prove byte integrity, not publisher identity or Microsoft SmartScreen trust.
- Run any future signing hook only after explicit opt-in, through an absolute JSON argv contract without a shell, credentials, inherited tool paths or implicit certificate discovery; v0.8 refuses a signed manifest.
- Generate npm metadata offline from the exact `package-lock.json` with isolated empty npm configuration, and reject Python SBOM environments whose installed `paper-reader` version differs from the requested release.

## 0.6.0 - 2026-07-14

### Added

- Portable `paper-reader-backup-v1` archives with consistent SQLite snapshots, referenced PDF manifests, optional Scrypt/AES-256-GCM encryption, weekly four-copy retention and explicit local downloads.
- Preview-first restore with isolated validation, a second fingerprint-bound confirmation, restart-only apply, dual-root recovery journal, migration checks and path-free immutable receipts.
- A versioned five-capability plugin SDK with metadata-only entry-point discovery, strict JSONL framing, closed configuration/result schemas, exact per-capability grants, brokered network/file/credential access and single-plugin circuit breakers.
- Native Windows AppContainer plus Job Object plugin isolation, a deterministic offline example distribution, management API and permission-focused settings workspace.
- Privacy-allowlisted curated publication previews, immutable `PublishedSnapshot` bytes, fixed GitHub `origin/published` publishing through detached temporary worktrees, non-force retry semantics and a minimal GitHub Pages workflow.
- A responsive curated publishing workspace with ordered paper/report selection, included/excluded field disclosure, explicit target confirmation, durable progress, cancellation, failure recovery and snapshot history.

### Fixed

- Preserve the source worktree HEAD, branch and complete porcelain state across successful, failed, cancelled and concurrent publication attempts.
- Reject corrupt frozen publication bytes and unsafe `.git` links or junctions with stable retryable/non-retryable diagnostics that do not expose temporary paths.

### Security

- Exclude credentials, models, caches, logs, old backups, unreferenced objects and active external side effects from backup/restore; never serialize passwords or staging paths.
- Fail closed when native plugin isolation or exact authorization is unavailable; block direct database, host filesystem, environment credential, private-network, subprocess and host-DLL access.
- Freeze only explicitly selected public fields and formal report versions; exclude PDFs, full text, annotations, chat, behavior, preferences, credentials, complete prompts, failure drafts and source raw responses from publication.
- Pin publication to an uncredentialed GitHub HTTPS target and a four-file allowlist; disable shell execution, user Git configuration, hooks, filters, signing, URL rewrites, force push and source-worktree mutation.

## 0.5.0 - 2026-07-14

### Added

- Unified Codex CLI, Ollama Local and Ollama Cloud backends with capability probing, dynamic model catalogs, legal reasoning/service-tier controls and OS-vault cloud credentials.
- Read-only analysis preflight followed by explicit click-to-run Jobs, immutable structured report versions, evidence citations, persisted fragments, failure drafts and Markdown/HTML/browser-print exports.
- Versioned general, ML/AI and biomedical prompt templates plus exact full-text/abstract evidence bundles bound to the selected document hash, annotations, coverage and complete composed prompt.
- One durable conversation per canonical Work with append-only messages, recoverable replies, page citations and visible context checkpoints that never delete original messages.
- Versioned multi-paper comparison for 2–50 papers, with one-pass synthesis through 10 papers and validated per-paper extraction plus bounded staged synthesis above 10.
- Selective visual evidence for referenced figures, tables and equations, bounded to six in-memory PNG pages and enabled only for a user-approved, explicitly vision-capable model.
- Desktop and mobile Agent workspace acceptance covering cloud warnings, report success/failure/versioning/export, chat reconnection/checkpoints and direct/staged comparison.

### Fixed

- Make the scrollable PDF page viewport keyboard-focusable with a visible focus indicator while preserving reader shortcuts.
- Preserve the newest queued reading position when a conversation citation briefly navigates away from the user's current PDF page.

### Security

- Run Codex without a shell in an allowlisted environment, read-only sandbox and isolated task directory; disable inherited shell, MCP, multi-agent, app, plugin, image-generation and JavaScript capabilities, with network research off unless explicitly selected.
- Encode paper text, metadata, annotations, chat history, user instructions and comparison intermediates as deterministic untrusted JSON so prompt content cannot alter process capabilities, credentials, scores or network scope.
- Revalidate document identity and SHA-256 before rendering visual pages; enforce media signatures, page/pixel/byte limits, cancellation and cleanup, and attach images only through backend-specific image fields.
- Prove with non-empty SQLite fixtures that successful and failed Agent operations leave score snapshots and discovery ranking rows byte-for-byte unchanged.

## 0.4.0 - 2026-07-13

### Added

- Local saved-paper library with lawful PDF/reference imports, content-addressed storage, explicit open-PDF acquisition jobs and preview-before-apply cache retention.
- Page-level PyMuPDF evidence extraction plus explicitly installed, on-demand PP-OCRv5 processing for scanned pages, with restartable jobs and safe runtime degradation.
- Lazy PDF.js reading workspace with a searchable text layer, page/zoom/rotation/fit controls, desktop three-column layout, medium version drawer and state-preserving mobile PDF/Agent tabs.
- Per-document reading-position restore and coalesced persistence, plus hash-bound highlights, notes and bookmarks with exact evidence anchors, rotated overlays and accessible desktop/mobile editing.
- Immutable, reviewable annotation migration between document versions with confidence, ambiguity/orphan evidence, temporary PDF previews and explicit per-item accept/reject decisions.
- User-facing PDF/DOI/BibTeX/URL import dialog, per-paper version import, OCR capability/install settings and preview-first protected cache cleanup.

### Fixed

- Show stable, path-free reader errors for oversized/non-PDF uploads, missing files and unsafe cleanup responses.
- Present readable task names and stop showing “cancelling” after an acquisition has reached its cancelled terminal state.

### Security

- Serve reader PDF bytes only through an authenticated, range-capable endpoint that revalidates library membership, access, hash-derived paths, links, file size and availability without exposing local paths.
- Cap PDF.js backing canvases and OCR page renders at 25 million pixels; keep PDF pages white in dark mode and cancel superseded rendering work.
- Require library ownership, CSRF and immutable content-hash matching for annotation writes; preserve annotations across canonical Work merges and protect their shared PDF objects from cache cleanup.
- Freeze annotation migration evidence and revalidate both document hashes and extraction fingerprints before creating a target annotation; never move or delete the source annotation.

## 0.3.0 - 2026-07-13

### Added

- Multilingual FTS5 retrieval with deterministic BM25 field weights, optional E5/sqlite-vec semantic search, model integrity manifests and lexical-only degradation.
- Candidate-scoped relevance, seven-dimension objective quality scoring, immutable score snapshots, stable five-view ranking and explainable 80/20 exploration quotas.
- Official OpenAlex, Semantic Scholar and Crossref metadata enrichers with field provenance, credential isolation, partial-success jobs and restart recovery.
- Versioned CCF 2022 sixth-edition evidence plus source-separated, licensed CSV/XLSX imports for JCR, CAS and emerging venue catalogs.
- Retraction and correction guardrails, exact paper-to-artifact evidence, bounded explicit/implicit preferences, 180-day irreversible aggregation and history matches outside new-paper quotas.
- Immutable quality-rule revisions, server-authored evidence panels and accessible desktop/mobile editing of enabled dimensions and weights.

### Security

- Reject client-authored implicit-event timestamps and behavior outside the in-app whitelist; preserve event immutability while allowing canonical Work merges.
- Keep OpenAlex, Semantic Scholar and Crossref credentials in Windows Credential Manager and exclude values from API, SQLite, logs and score evidence.

## 0.2.0 - 2026-07-13

### Added

- Research-intent workflow with bilingual local keyword suggestions, immutable concept-group versions, explicit confirmation and visible diffs.
- Fixed or rolling natural-month plans, source-specific filters, manual/daily/weekly/monthly schedules and user-confirmed missed-run catch-ups.
- Official arXiv, OpenReview, Europe PMC, IEEE Xplore and Springer Nature connectors with fixture contracts, capability warnings and partial-success isolation.
- Non-persisting source previews, durable month-by-source runs, checkpoints, restart recovery, cancellation, retries and idempotent HTTP 202 commands.
- Canonical Work identity, DOI/source/fuzzy matching, representative document selection and explicit merge-review decisions.
- Quiet Library plan wizard, plan list, source credential settings, merge-review queue and desktop/mobile accessibility coverage.

### Fixed

- Preserve UTC timezone information when confirmed keyword versions are reloaded from SQLite.
- Transfer a candidate DOI to its canonical Work without transiently violating the unique DOI constraint.

### Security

- Store IEEE, Springer Nature and optional OpenReview credentials only in Windows Credential Manager; APIs expose configuration state but never secret values.

## 0.1.0 - 2026-07-13

### Added

- Local FastAPI/Vue application foundation with safe loopback bootstrap sessions.
- SQLite WAL storage, Alembic migrations, durable jobs and recoverable task events.
- Quiet Library application shell, six primary workspaces and global task drawer.
- Locked Python/npm dependencies, generated OpenAPI types and Windows CI.

### Removed

- Legacy `daily_paper_reader` pipeline, VitePress site, generated archives and browser-side GitHub PAT workflow.

### Security

- HttpOnly SameSite session cookie, loopback Origin checks, double-submit CSRF proof and log redaction.

[Unreleased]: https://github.com/Lisalsj/folioskiff/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/Lisalsj/folioskiff/releases/tag/v3.2.0
