# Security model

`flow` runs workflows as Python code. Treat workflow scripts as trusted code unless you place them inside your own OS-level sandbox.

## Trust boundary

- `flow run workflow.py` executes that file with normal Python process privileges.
- `flow run --nl ...` validates the model-authored script before execution, but validation is a guardrail against accidental unsafe output, not a hostile-code sandbox.
- `wf.local` runs arbitrary Python callables.
- `shell_cmd` runs configured argv lists with `shell=False`, but the called CLI still interprets its own arguments and may have side effects.

## Secrets

- Keep API keys in environment variables or credential files.
- `flow doctor` must not print secret values.
- Journals and reports can contain prompts, model outputs, labels, and tool results. Do not place secrets in prompts unless you are comfortable storing them in the run directory.

## Tools and approval gates

A tool grant is per leaf. Backends without a native tool loop fail closed when tools are requested. Side-effecting tools should require an approval callback or be exposed only in trusted workflows.

## Running untrusted workflows

Use an external sandbox:

- container or VM
- restricted working directory
- no mounted secrets
- no wallet/key files
- constrained network access
- throwaway `FLOW_DATA_DIR`

## Reporting vulnerabilities

See [SECURITY.md](../SECURITY.md).
