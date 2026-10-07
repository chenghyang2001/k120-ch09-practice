"""M5 UI：首頁、閱讀器、靜態檔、parent 篩選相容。"""

import pytest

from tests.test_export import make_done_job


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'id="job-form"' in html and 'id="url"' in html
    assert '<option value="720" selected>' in html
    assert '<option value="zh-TW" selected>' in html
    assert 'id="history-list"' in html and 'id="active-list"' in html
    assert "/static/app.js" in html and "/static/style.css" in html


def test_reader_page(client, data_dir):
    job = make_done_job(client, data_dir)
    res = client.get(f"/read/{job['id']}")
    assert res.status_code == 200
    html = res.text
    assert f'data-job-id="{job["id"]}"' in html
    for marker in ('data-mode="orig"', 'data-mode="tr"', 'data-mode="both"', 'id="page-counter"',
                   'format=html', 'format=pdf', 'format=md', 'id="fullscreen-btn"', 'id="thumb-strip"',
                   "/static/reader.js"):
        assert marker in html
    # 標題經過跳脫
    assert "測試影片 &lt;b&gt;" in html


def test_reader_unknown_job_404(client):
    assert client.get("/read/nope").status_code == 404


@pytest.mark.parametrize("name", ["reader.js", "app.js", "style.css"])
def test_static_files(client, name):
    res = client.get(f"/static/{name}")
    assert res.status_code == 200
    assert len(res.content) > 100


def test_reader_js_uses_textcontent_only(client):
    js = client.get("/static/reader.js").text
    assert "innerHTML" not in js
    assert "textContent" in js


def test_list_jobs_parent_param(client, data_dir):
    make_done_job(client, data_dir)
    res = client.get("/api/jobs?parent=nonexistent")
    assert res.status_code == 200
    assert res.json() == []
