# Security policy

## Supported versions

| Version | Security support |
|---|---|
| `3.2.x` | Current supported release line |
| `3.0.x` | Superseded; security fixes are not guaranteed |
| `1.0.x` | Historical; security fixes are not guaranteed |
| Earlier versions | Not supported |
| Unreleased `main` snapshots | Best effort; not a stable release |

The current supported stable release is `3.2.0`. Users on earlier releases should upgrade after verifying the release-bundle hashes and migration notes.

Windows 11 x64 is the supported product environment. macOS, Linux, Windows Server, modified third-party packages, and deployments exposed beyond the loopback interface are outside the current security support boundary.

## Reporting a vulnerability

Use [GitHub Private Vulnerability Reporting](https://github.com/Lisalsj/folioskiff/security/advisories/new). Do not open a public issue for a suspected vulnerability, leaked credential, or private-data exposure.

Include:

- affected version or commit;
- Windows version and installation type;
- minimal reproduction with synthetic data;
- expected security impact;
- whether exploitation requires another local process, a malicious PDF, a plugin, or a webpage;
- the smallest relevant redacted log excerpt.

Do not attach API keys, cookies, private PDFs, full prompts, backups, local database files, personal reading history, or unredacted paths. If a reproducer needs sensitive material, describe its shape and wait for a private handling agreement.

## Security boundary

The service must remain bound to `127.0.0.1`, use its bootstrap/session protections, validate origins and CSRF tokens, keep credentials in the operating-system credential store, and run plugins and Agent tasks with their documented permission boundaries. External research is opt-in and private paper content is never treated as trusted instruction.

The application cannot protect data after the Windows account or operating system is fully compromised, and it does not bypass publisher access controls. See [docs/security-privacy.md](docs/security-privacy.md) and the explicit [v2 threat model](docs/threat-model-v2.md) for the complete threat, credential, prompt-injection, and SSRF boundaries. Safe user-facing diagnostics are documented in [troubleshooting](docs/troubleshooting.md).
