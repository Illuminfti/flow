import pytest

from flow import authoring


GOOD = '''
def run(wf, args):
    wf.phase("p")
    return wf.parallel([(lambda i=i: wf.agent(f"q{i}", label=f"a{i}")) for i in range(3)])
'''

BAD_IMPORT = '''
import os
def run(wf, args):
    return 1
'''

NO_RUN = '''
def helper(x):
    return x
'''


def test_validate_accepts_good():
    authoring.validate(GOOD)


def test_validate_rejects_disallowed_import():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate(BAD_IMPORT)


def test_validate_requires_run():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate(NO_RUN)


def test_validate_rejects_syntax_error():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate("def run(wf, args)\n  return 1")


def test_dry_run_executes_dag_without_models(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    res = authoring.dry_run(GOOD, args=None)
    assert res["ok"] is True
    assert res["leaf_count"] == 3  # 3 agent leaves, forced local, zero model calls
