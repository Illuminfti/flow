"""Visual run card — HTML generation + graceful render fallback."""
from flow import progress as P
from flow.runtime import run_workflow


def _run(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "d"))

    def run(wf, args):
        wf.phase("work")
        return wf.parallel([(lambda i=i: wf.local(lambda: i * i)) for i in range(3)])

    return run_workflow(run_fn=run, slug="card")


def test_card_html_renders_run(tmp_path, monkeypatch):
    rep = _run(tmp_path, monkeypatch)
    rid = __import__("pathlib").Path(rep["run_dir"]).name
    html = P.card_html(rid)
    assert html and "<!doctype html>" in html
    assert "flow" in html and "leaves" in html
    assert "work" in html  # the phase name appears


def test_card_html_none_for_unknown_run():
    assert P.card_html("does-not-exist-run") is None


def test_render_card_graceful_without_renderer(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOW_HTML2PNG", raising=False)
    rep = _run(tmp_path, monkeypatch)
    rid = __import__("pathlib").Path(rep["run_dir"]).name
    # no FLOW_HTML2PNG and no html2png arg → None, never raises
    assert P.render_card(rid) is None
    # a bogus path also degrades cleanly
    assert P.render_card(rid, html2png="/nonexistent/html2png.js") is None
