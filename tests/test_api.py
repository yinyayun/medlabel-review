"""Integration checks for provenance, review invariants and portable exports."""
import csv
import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.headers["X-Requested-With"] = "MedLabelReview"
        yield test_client


def drug(client, name="测试资料（模拟）"):
    response = client.post("/api/drugs", json={"name": name, "manufacturer": "虚构厂家"})
    assert response.status_code == 201, response.text
    return response.json()


def version(client, drug_id, text, day="2026-01-01", label="模拟版"):
    response = client.post(f"/api/drugs/{drug_id}/versions/text", json={
        "label": label, "version_date": day, "source": "测试模拟文件", "text": text,
    })
    assert response.status_code == 201, response.text
    return response.json()


def comparison(client):
    d = drug(client)
    a = version(client, d["id"], "【药品名称】\n测试资料（模拟）\n【贮藏】\n模拟条件 A")
    b = version(client, d["id"], "【药品名称】\n测试资料（模拟）\n【贮藏】\n模拟条件 B", "2026-02-01")
    response = client.post("/api/comparisons", json={"old_version_id": a["id"], "new_version_id": b["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def test_complete_review_lifecycle_preserves_audit(client):
    c = comparison(client)
    change = c["changes"][0]
    endpoint = f'/api/changes/{change["id"]}'
    assert c["pending_changes"] == 1
    assert client.patch(endpoint, json={"status": "ignored", "note": " ", "revision": 0}).status_code == 422
    response = client.patch(endpoint, json={"status": "confirmed", "note": "已与模拟原文核对", "revision": 0})
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert client.patch(endpoint, json={"status": "ignored", "note": "旧窗口提交", "revision": 0}).status_code == 409
    complete = client.get(f'/api/comparisons/{c["id"]}').json()
    assert complete["pending_changes"] == 0
    assert complete["status"] == "completed"
    assert client.patch(endpoint, json={"status": "pending", "note": "重新核对", "revision": 1}).status_code == 200
    logs = client.get("/api/audit").json()
    reviews = [json.loads(log["detail"]) for log in logs if log["action"] == "审核变更"]
    assert len(reviews) == 2
    assert reviews[0]["before"]["note"] == "已与模拟原文核对"


def test_uploaded_original_hash_and_filename_are_preserved_safely(client):
    d = drug(client)
    raw = "【药品名称】\n模拟药品\n【贮藏】\n仅用于软件测试".encode()
    response = client.post(f'/api/drugs/{d["id"]}/versions',
                           data={"label": "测试", "version_date": "2026-01-01", "source": "模拟"},
                           files={"file": ("../../example.txt", raw, "text/plain")})
    assert response.status_code == 201, response.text
    v = response.json()
    assert v["filename"] == "example.txt"
    assert v["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "storage_name" not in v
    assert client.get(f'/api/versions/{v["id"]}/file').content == raw


def test_section_correction_locks_after_comparison(client):
    d = drug(client)
    a = version(client, d["id"], "【贮藏】\n原始文字")
    endpoint = f'/api/versions/{a["id"]}/sections'
    updated = client.put(endpoint, json={"sections": [{"title": "贮藏", "text": "手工校正文字"}]})
    assert updated.status_code == 200
    assert updated.json()["text"] == a["text"]
    assert updated.json()["sha256"] == a["sha256"]
    assert updated.json()["sections"][0]["start_line"] is None
    b = version(client, d["id"], "【贮藏】\n新版", "2026-02-01")
    response = client.post("/api/comparisons", json={"old_version_id": a["id"], "new_version_id": b["id"]})
    assert response.status_code == 201
    assert "手工" in response.json()["changes"][0]["old_location"]
    assert client.put(endpoint, json={"sections": [{"title": "贮藏", "text": "再修改"}]}).status_code == 409


def test_invalid_version_pairs_rejected(client):
    first, second = drug(client), drug(client)
    a = version(client, first["id"], "模拟 A", "2026-02-01")
    b = version(client, first["id"], "模拟 B", "2026-01-01")
    c = version(client, second["id"], "模拟 C", "2026-03-01")
    for old, new in [(a, a), (a, b), (a, c)]:
        assert client.post("/api/comparisons", json={"old_version_id": old["id"], "new_version_id": new["id"]}).status_code == 422
    assert client.get("/api/comparisons").json() == []


@pytest.mark.parametrize("filename,raw", [("bad.exe", b"MZ"), ("empty.txt", b""), ("bad.pdf", b"not a pdf"), ("bad.docx", b"not zip")])
def test_bad_upload_does_not_create_version_or_orphan_file(client, filename, raw):
    d = drug(client)
    response = client.post(f'/api/drugs/{d["id"]}/versions',
                           data={"label": "测试", "version_date": "2026-01-01"},
                           files={"file": (filename, raw)})
    assert response.status_code == 422, response.text
    assert client.get(f'/api/drugs/{d["id"]}/versions').json() == []
    assert list(client.app.state.db.uploads.iterdir()) == []


def test_exports_escape_user_content_and_neutralize_formulas(client):
    d = drug(client, '=HYPERLINK("https://example.invalid")')
    a = version(client, d["id"], "【贮藏】\n旧内容")
    b = version(client, d["id"], "【贮藏】\n<script>alert(1)</script>", "2026-02-01")
    c = client.post("/api/comparisons", json={"old_version_id": a["id"], "new_version_id": b["id"]}).json()
    base = f'/api/comparisons/{c["id"]}/export'
    html = client.get(base + "?format=html")
    assert html.status_code == 200
    assert "<script>alert(1)</script>" not in html.text
    assert "&lt;script&gt;" in html.text
    assert a["sha256"] in html.text
    csv_export = client.get(base + "?format=csv")
    assert csv_export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(csv_export.content.decode("utf-8-sig"))))
    assert rows[1][1].startswith("'=HYPERLINK")
    assert all(len(row) == len(rows[0]) for row in rows)
    data = client.get(base + "?format=json").json()
    assert data["changes"][0]["new_text"] == "<script>alert(1)</script>"
    assert data["old_version"]["source"] == "测试模拟文件"
    assert "exported_at" in data


def test_empty_diff_is_complete_and_exports_valid_csv(client):
    d = drug(client)
    a = version(client, d["id"], "【贮藏】\n一致内容")
    b = version(client, d["id"], "【贮藏】\n一致内容", "2026-02-01")
    c = client.post("/api/comparisons", json={"old_version_id": a["id"], "new_version_id": b["id"]}).json()
    assert c["changes"] == []
    assert c["status"] == "completed"
    result = client.get(f'/api/comparisons/{c["id"]}/export?format=csv')
    rows = list(csv.reader(io.StringIO(result.content.decode("utf-8-sig"))))
    assert len(rows[0]) == len(rows[1])


def test_demo_is_idempotent_and_survives_app_restart(client):
    first = client.post("/api/demo").json()
    assert client.post("/api/demo").json() == first
    assert client.get("/api/dashboard").json()["drugs"] == 1
    with TestClient(create_app(client.app.state.db.directory)) as reopened:
        result = reopened.get(f'/api/comparisons/{first["comparison_id"]}').json()
        assert result["total_changes"] > 0
        assert "模拟" in result["old_version"]["source"] or "虚构" in result["old_version"]["source"]


def test_local_mutation_guard_and_validation(client):
    client.headers.pop("X-Requested-With")
    assert client.post("/api/demo").status_code == 403
    assert client.get("/api/health").status_code == 200
    client.headers["X-Requested-With"] = "MedLabelReview"
    assert client.post("/api/drugs", json={"name": " "}).status_code == 422
    assert client.get("/api/versions/9999").status_code == 404


def test_rebinding_and_cross_origin_requests_rejected(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    for origin in ["null", "https://attacker.example", "http://testserver:9999"]:
        assert client.post("/api/demo", headers={"Origin": origin}).status_code == 403
    assert client.post("/api/demo", headers={"Origin": "http://testserver"}).status_code == 200
