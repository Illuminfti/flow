#!/usr/bin/env python3
"""flow CLI — run | resume | trace | author | list | doctor | init | self-test."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import progress as progress_mod
from ._bootstrap import cmd_init, cmd_self_test, config_path
from .paths import data_root
from .runtime import run_workflow


def _json(s, default=None):
    return json.loads(s) if s else default


def _cmd_run(a) -> int:
    script_path = a.script
    if a.nl:
        from . import authoring
        print(f"[flow] authoring script via model for: {a.nl}", file=sys.stderr)
        script_path = authoring.author_and_save(a.nl, a.out)
        print(f"[flow] authored -> {script_path}", file=sys.stderr)
    if not script_path:
        print("error: provide a script path or --nl", file=sys.stderr)
        return 2
    rep = run_workflow(script_path=script_path, args=_json(a.args), budget=_json(a.budget),
                       run_id=a.run_id, executor_kind=a.executor, max_workers=a.max_workers)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep["status"] == "completed" else 1


def _cmd_resume(a) -> int:
    rep = run_workflow(script_path=a.script, args=_json(a.args), budget=_json(a.budget),
                       run_id=a.run_id, executor_kind=a.executor)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep["status"] == "completed" else 1


def _cmd_trace(a) -> int:
    if getattr(a, "json", False):
        print(json.dumps(progress_mod.spans(a.run_id), indent=2, default=str))
    else:
        print(progress_mod.trace(a.run_id))
    return 0


def _cmd_status(a) -> int:
    rep = progress_mod.status_report(a.run_id)
    if getattr(a, "json", False):
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(progress_mod.render_status(rep))
    return 0


def _cmd_author(a) -> int:
    from . import authoring
    print(authoring.author_and_save(a.task, a.out))
    return 0


def _cmd_list(a) -> int:
    runs_dir = data_root() / "runs"
    if not runs_dir.exists():
        return 0
    for p in sorted([d for d in runs_dir.iterdir() if d.is_dir()], reverse=True)[: a.limit]:
        status = "?"
        rp = p / "run-report.json"
        if rp.exists():
            try:
                status = json.loads(rp.read_text()).get("status", "?")
            except Exception:
                pass
        print(f"{p.name}\t{status}")
    return 0


def _cmd_doctor(a) -> int:
    import os
    import shutil
    from . import config
    cfg = config.get(reload=True)
    providers = cfg.get("providers") or {}
    prov_status = {}
    for name, p in providers.items():
        env = p.get("auth_env")
        prov_status[name] = {"kind": p.get("kind"),
                             "auth_env": env, "key_present": bool(env and os.environ.get(env))}
    print(json.dumps({
        "version": __version__,
        "python": sys.version.split()[0],
        "config_path": str(config_path()),
        "config_exists": config_path().exists(),
        "data_root": str(data_root()),
        "providers": prov_status,
        "models": list((cfg.get("models") or {}).keys()),
        "tiers": cfg.get("tiers"),
        "httpx": bool(shutil.which("python") and _has("httpx")),
        "yaml": _has("yaml"),
    }, indent=2))
    return 0


def _has(mod) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _cmd_init(a) -> int:
    return cmd_init(force=a.force)


def _cmd_selftest(a) -> int:
    return cmd_self_test(online=a.online, as_json=a.json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flow", description="Generic dynamic-workflow engine for any agent, any model")
    p.add_argument("--version", action="version", version=f"flow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a workflow script (or --nl to author then run)")
    r.add_argument("script", nargs="?")
    r.add_argument("--nl", help="natural-language task -> author + run")
    r.add_argument("--out")
    r.add_argument("--args")
    r.add_argument("--budget")
    r.add_argument("--run-id")
    r.add_argument("--executor", choices=["thread", "process"], default="thread")
    r.add_argument("--max-workers", type=int, default=None)
    r.set_defaults(fn=_cmd_run)

    rs = sub.add_parser("resume", help="resume a crashed/paused run")
    rs.add_argument("run_id")
    rs.add_argument("script")
    rs.add_argument("--args")
    rs.add_argument("--budget")
    rs.add_argument("--executor", choices=["thread", "process"], default="thread")
    rs.set_defaults(fn=_cmd_resume)

    t = sub.add_parser("trace", help="render a run's span tree")
    t.add_argument("run_id")
    t.add_argument("--json", action="store_true", help="emit structured spans instead of the dashboard")
    t.set_defaults(fn=_cmd_trace)

    stt = sub.add_parser("status", help="inspect node states + what resume would re-run")
    stt.add_argument("run_id")
    stt.add_argument("--json", action="store_true")
    stt.set_defaults(fn=_cmd_status)

    au = sub.add_parser("author", help="author a script from NL (no run)")
    au.add_argument("task")
    au.add_argument("-o", "--out")
    au.set_defaults(fn=_cmd_author)

    ls = sub.add_parser("list", help="list recent runs")
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(fn=_cmd_list)

    d = sub.add_parser("doctor", help="environment + config diagnostics")
    d.set_defaults(fn=_cmd_doctor)

    i = sub.add_parser("init", help="write a starter config + detect keys")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=_cmd_init)

    for alias in ("self-test", "selftest"):  # accept both spellings
        st = sub.add_parser(alias, help="prove it works (offline by default)")
        st.add_argument("--online", action="store_true", help="also make one real model call")
        st.add_argument("--offline", action="store_true", help="(default) no network")
        st.add_argument("--json", action="store_true")
        st.set_defaults(fn=_cmd_selftest)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
