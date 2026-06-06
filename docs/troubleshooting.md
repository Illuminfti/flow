# Troubleshooting

## `flow run --nl` fails validation

The authoring model produced a script that did not pass guardrails. Recent versions attempt one bounded repair pass. If it still fails:

```bash
flow author "your task" -o workflow.py
# edit workflow.py
flow run workflow.py --budget '{"max_usd":1}'
```

## `flow self-test --offline` fails

Run:

```bash
flow doctor
python -c "import flow; print(flow.__version__)"
```

Check that the installed package is the intended one and that the config file is parseable.

## `flow self-test --online` fails

`--online` needs one configured model route with credentials. Run `flow doctor` and check provider status. This is usually an auth/config/provider issue, not an offline engine failure.

## YAML config fails on a zero-dependency install

Use JSON config, or install YAML support:

```bash
pip install 'flow[yaml] @ git+https://github.com/Illuminfti/flow'
```

`flow init` writes JSON when YAML support is not installed.

## A leaf is reused when you expected it to rerun

Resume reuse depends on leaf identity. Change the phase, label, prompt, model, backend, schema, provider, toolsets, tools, max tokens, or script id when you need different work.

## A leaf reruns when you expected cache reuse

Check:

```bash
flow status <run_id> --json
flow trace <run_id> --json
```

Common causes: changed script content, changed label, changed prompt, different route, changed schema, or a prior leaf ended cancelled/failed instead of completed.

## Tools fail closed

Only backends with a native tool loop can run tools. `openai_http` and `anthropic_sdk` support tools. `shell_cmd` and `codex` reject tool grants clearly.

## Shell command output is noisy

Use `leaf.noise_patterns` in config to strip known noise lines from `shell_cmd` stdout.
