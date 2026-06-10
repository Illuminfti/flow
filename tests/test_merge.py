"""v4: merge orchestrator — proof gates, guards, money fence, auto-revert."""
import json
import subprocess
from pathlib import Path


from flow import config
from flow import merge as merge_mod
from flow import proof as proof_mod
from flow.runtime import run_workflow


# --------------------------------------------------------------------------
# pure logic (no git, no agents)
# --------------------------------------------------------------------------

def test_tamper_detects_modified_test_not_new_test():
    modify = ("diff --git a/tests/test_x.py b/tests/test_x.py\n"
              "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
              "@@ -1,2 +1,2 @@\n-    assert add(1,1) == 2\n+    assert add(1,1) == 3\n")
    assert merge_mod.touches_existing_tests(modify) == ["tests/test_x.py"]

    new = ("diff --git a/tests/test_new.py b/tests/test_new.py\n"
           "new file mode 100644\n--- /dev/null\n+++ b/tests/test_new.py\n"
           "@@ -0,0 +1,2 @@\n+def test_z():\n+    assert True\n")
    assert merge_mod.touches_existing_tests(new) == []  # new tests are coverage

    src = ("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
           "@@ -1 +1 @@\n-def add(a,b): return a-b\n+def add(a,b): return a+b\n")
    assert merge_mod.touches_existing_tests(src) == []  # source, not a test


def test_allowlist_matches_dir_prefix(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    assert merge_mod._allowlisted(str(repo), [str(tmp_path)])
    assert merge_mod._allowlisted(str(repo), [str(repo)])
    assert not merge_mod._allowlisted(str(repo), [str(tmp_path / "other")])
    assert not merge_mod._allowlisted(str(repo), [])  # empty = fail closed


def test_patch_files_parses_targets():
    patch = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
             "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-c\n+d\n")
    assert merge_mod.patch_files(patch) == ["x.py", "y.py"]


def test_proof_quarantine_flip(tmp_path):
    flag = tmp_path / "flag"
    # fails first (flag absent), passes on the second run (flag created)
    script = tmp_path / "flip.sh"
    script.write_text(f'#!/bin/sh\nif [ -f {flag} ]; then exit 0; fi\ntouch {flag}\nexit 1\n')
    script.chmod(0o755)
    r = proof_mod.run_check_quarantined("test", [str(script)], str(tmp_path))
    assert r.quarantined and not r.passed


def test_proof_real_pass_fail(tmp_path):
    ok = proof_mod.run_check("t", ["sh", "-c", "exit 0"], str(tmp_path))
    assert ok.passed and ok.exit_code == 0
    bad = proof_mod.run_check("t", ["sh", "-c", "echo boom >&2; exit 1"], str(tmp_path))
    assert not bad.passed and "boom" in bad.output_tail


# --------------------------------------------------------------------------
# orchestrator (real git repos, controllable fake agents)
# --------------------------------------------------------------------------

def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", "-C", str(repo), *a], check=True,
                              capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    g("add", "-A")
    g("commit", "-qm", "base")
    return repo


def _patch(repo, rel, content, *, new=False):
    """Build a patch spec that sets file `rel` to `content` against `repo` HEAD."""
    import tempfile
    wt = Path(tempfile.mkdtemp())
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(wt)],
                   check=True, capture_output=True)
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    diff = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--binary"],
                          capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                   capture_output=True)
    return diff


def _cfg(tmp_path, monkeypatch, *, allowlist=None, fixer_fix=None, reviewer_answer=None):
    """Agents: coder='fixer' generic harness that optionally writes a fix +
    prints {"done":true}; reviewer='reviewer' fake-claude returning a verdict."""
    fixer = tmp_path / "fixer.py"
    if fixer_fix:
        rel, content = fixer_fix
        fixer.write_text(
            f'import json\nopen({rel!r}, "w").write({content!r})\n'
            'print(json.dumps({"done": True}))\n')
    else:
        fixer.write_text('import json\nprint(json.dumps({"done": True}))\n')
    reviewer = tmp_path / "reviewer.py"
    ans = reviewer_answer or {"accepted": True, "reason": "ok"}
    reviewer.write_text(f'import json\nprint(json.dumps({ans!r}))\n')
    cfg = {
        "engine": {"executor": "thread"},
        "leaf": {"allow_light_models": True, "noise_patterns": []},
        "providers": {}, "models": {}, "tiers": {"quality": [], "local": []},
        "agents": {
            "coder": {"harness": "generic", "reserve_tokens": 10,
                      "cmd_template": ["python3", str(fixer)]},
            "reviewer": {"harness": "generic", "reserve_tokens": 10,
                         "cmd_template": ["python3", str(reviewer)]},
        },
        "merge": {"allowlist": allowlist or []},
    }
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)


PYTEST = ["python3", "-m", "pytest", "-q", "test_calc.py"]


def test_clean_patches_merge_with_proofs(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    repo = _repo(tmp_path)
    p1 = _patch(repo, "feature.py", "VALUE = 1\n", new=True)
    p2 = _patch(repo, "feature2.py", "VALUE = 2\n", new=True)

    def run(wf, args):
        wf.phase("merge")
        return wf.merge(
            [{"task_id": "t1", "patch_text": p1, "files": ["feature.py"]},
             {"task_id": "t2", "patch_text": p2, "files": ["feature2.py"]}],
            repo=str(repo), checks=[{"name": "test", "command": PYTEST}])

    rep = run_workflow(run_fn=run, args={}, run_id="merge-clean-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["merged"] == ["t1", "t2"]
    assert not res["exiled"]
    # integration branch holds both commits; main untouched (no auto_merge)
    assert res["merged_to_target"] is False
    for o in res["outcomes"]:
        assert o["proofs"]["green"] is True


def test_red_patch_without_repair_is_exiled(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    repo = _repo(tmp_path)
    # break add() → the existing test fails
    bad = _patch(repo, "calc.py", "def add(a, b):\n    return a - b\n")

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "bad", "patch_text": bad, "files": ["calc.py"]}],
                        repo=str(repo), checks=[{"name": "test", "command": PYTEST}],
                        max_repairs=0)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-red-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["exiled"] == ["bad"]
    assert res["outcomes"][0]["status"] == "exiled"
    assert "red" in res["outcomes"][0]["reason"]
    # the integration branch was reset — no broken commit kept
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", "flow/integ-merge-red-001"],
                         capture_output=True, text=True).stdout
    assert "flow[bad]" not in log


def test_red_patch_repaired_by_agent_merges(tmp_path, monkeypatch):
    # task adds feature.py with a syntax error; the import proof fails; the
    # repair agent rewrites feature.py correctly (a real diff vs base) → green.
    _cfg(tmp_path, monkeypatch,
         fixer_fix=("feature.py", "VALUE = 42\n"))
    repo = _repo(tmp_path)
    bad = _patch(repo, "feature.py", "VALUE = \n", new=True)  # syntax error
    import_check = [{"name": "import", "command": ["python3", "-c", "import feature"]}]

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "fixme", "patch_text": bad, "files": ["feature.py"]}],
                        repo=str(repo), checks=import_check, max_repairs=1)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-repair-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["merged"] == ["fixme"]
    assert res["outcomes"][0]["proofs"]["green"] is True


def test_test_tamper_blocked_when_reviewer_rejects(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch,
         reviewer_answer={"accepted": False, "reason": "weakens the assertion"})
    repo = _repo(tmp_path)
    # weaken the test so a broken add() would pass
    tamper = _patch(repo, "test_calc.py",
                    "from calc import add\n\ndef test_add():\n    assert True\n")

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "sneaky", "patch_text": tamper,
                          "files": ["test_calc.py"]}],
                        repo=str(repo), checks=[{"name": "test", "command": PYTEST}])

    rep = run_workflow(run_fn=run, args={}, run_id="merge-tamper-001",
                       script_id="s", slug="t")
    res = rep["final"]
    o = res["outcomes"][0]
    assert o["status"] == "quarantined_block"
    assert "test-tamper" in o["reason"]
    assert o["review"]["accepted"] is False


def test_test_tamper_allowed_when_reviewer_accepts(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch,
         reviewer_answer={"accepted": True, "reason": "legitimate rename"})
    repo = _repo(tmp_path)
    # a legitimate test update: reworks the existing assertion line (a real
    # removal → flagged for review) but still asserts correctness
    upd = _patch(repo, "test_calc.py",
                 "from calc import add\n\ndef test_add():\n"
                 "    assert add(2, 3) == 5 and add(1, 1) == 2\n")

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "upd", "patch_text": upd,
                          "files": ["test_calc.py"]}],
                        repo=str(repo), checks=[{"name": "test", "command": PYTEST}])

    rep = run_workflow(run_fn=run, args={}, run_id="merge-tamper-002",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["merged"] == ["upd"]
    assert res["outcomes"][0]["review"]["accepted"] is True


def test_auto_merge_promotes_when_allowlisted_and_canary_green(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _cfg(tmp_path, monkeypatch, allowlist=[str(repo)])
    p = _patch(repo, "feature.py", "VALUE = 1\n", new=True)

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "feat", "patch_text": p, "files": ["feature.py"]}],
                        repo=str(repo), target_branch="master",
                        checks=[{"name": "test", "command": PYTEST}],
                        canary=[{"name": "canary", "command": PYTEST}],
                        auto_merge=True)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-auto-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["merged_to_target"] is True
    assert res["reverted"] is False
    # master actually advanced and contains the feature
    assert (repo / "feature.py").exists()
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", "master"],
                         capture_output=True, text=True).stdout
    assert "flow: merge" in log


def test_auto_merge_reverts_on_canary_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _cfg(tmp_path, monkeypatch, allowlist=[str(repo)])
    before = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    p = _patch(repo, "feature.py", "VALUE = 1\n", new=True)

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "feat", "patch_text": p, "files": ["feature.py"]}],
                        repo=str(repo), target_branch="master",
                        checks=[{"name": "test", "command": PYTEST}],
                        canary=[{"name": "canary", "command": ["sh", "-c", "exit 1"]}],
                        auto_merge=True)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-revert-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert res["reverted"] is True
    assert res["merged_to_target"] is False
    # master rolled back to its pre-merge SHA; feature gone from the tree
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "master"],
                           capture_output=True, text=True).stdout.strip()
    assert after == before
    assert not (repo / "feature.py").exists()
    assert res["outcomes"][0]["status"] == "exiled"


def test_money_fence_withholds_non_allowlisted_promotion(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _cfg(tmp_path, monkeypatch, allowlist=[])  # repo NOT allowlisted
    before = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    p = _patch(repo, "feature.py", "VALUE = 1\n", new=True)

    def run(wf, args):
        wf.phase("merge")
        return wf.merge([{"task_id": "feat", "patch_text": p, "files": ["feature.py"]}],
                        repo=str(repo), target_branch="master",
                        checks=[{"name": "test", "command": PYTEST}],
                        auto_merge=True)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-fence-001",
                       script_id="s", slug="t")
    res = rep["final"]
    # work landed on the integration branch but master was NEVER touched
    assert res["merged"] == ["feat"]
    assert res["merged_to_target"] is False
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "master"],
                           capture_output=True, text=True).stdout.strip()
    assert after == before
    events = [json.loads(l) for l in
              (Path(rep["run_dir"]) / "journal.jsonl").read_text().splitlines() if l.strip()]
    assert any(e.get("event") == "merge_promotion_withheld" for e in events)


def test_conflicting_patch_exiled_without_repair(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    repo = _repo(tmp_path)
    # two patches that edit the SAME line — the second cannot 3-way apply
    p1 = _patch(repo, "calc.py", "def add(a, b):\n    return a + b + 0\n")
    p2 = _patch(repo, "calc.py", "def add(a, b):\n    return (a) + (b)\n")

    def run(wf, args):
        wf.phase("merge")
        return wf.merge(
            [{"task_id": "t1", "patch_text": p1, "files": ["calc.py"]},
             {"task_id": "t2", "patch_text": p2, "files": ["calc.py"]}],
            repo=str(repo), checks=[{"name": "test", "command": PYTEST}],
            max_repairs=0)

    rep = run_workflow(run_fn=run, args={}, run_id="merge-conflict-001",
                       script_id="s", slug="t")
    res = rep["final"]
    assert "t1" in res["merged"]
    assert "t2" in res["exiled"]
    assert "conflict" in next(o["reason"] for o in res["outcomes"]
                              if o["task_id"] == "t2")
