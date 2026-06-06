# Security Policy

## Reporting a vulnerability

Open a private security advisory on GitHub if available, or contact the maintainer directly through the repository owner profile.

Do not file public issues for active vulnerabilities involving credential exposure, sandbox escapes, or unsafe command execution until a fix is available.

## Scope

Security-sensitive areas include:

- model-authored workflow validation
- shell command backend behavior
- tool approval gates
- credential loading and diagnostics
- journal/report contents
- provider auth and OAuth token handling

## Supported versions

The public GitHub branch is currently the supported development line. Pin to a commit SHA for production use until stable package releases begin.

## Important boundary

`flow` is an orchestration engine, not a sandbox. Workflow scripts are Python code and should be treated as trusted unless run in an external sandbox. See [`docs/security.md`](docs/security.md).
