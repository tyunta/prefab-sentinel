# Security Policy

This file is the entry point for reporting vulnerabilities in Prefab Sentinel. Public issues are reserved for non-security bugs.

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/tyunta/prefab-sentinel/security/advisories/new) (Security Advisory) to send the report. The advisory thread is private to repository maintainers until coordinated disclosure.

Include in the report: a minimal reproduction, the affected version (the `version` field in `pyproject.toml`), and the impact you observed. If GitHub Security Advisories are not reachable from your environment, request a private email channel via a GitHub issue titled `security: request private channel` (no details in the issue body).

Maintainers aim to acknowledge a report within five business days. The advisory drives the fix branch, the CVE assignment (if applicable), and the disclosure timing.

## Supported versions

Only the latest commit on the `main` branch is supported. There are no LTS branches and no backports to tagged releases. Pin to a specific commit if you need reproducible behavior.
