"""
test_documents.py — CRUD + health + cache behaviour.

Each test names exactly what it asserts. The CI pipeline runs these with
coverage; the build only proceeds to image-push if they pass.
"""

from __future__ import annotations


def test_liveness_always_ok(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_readiness_checks_db(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["database"] == "up"


def test_create_and_get_document(client):
    # CREATE → 201 with server-generated id + created_at
    r = client.post("/documents", json={
        "title": "Raft paper notes",
        "content": "Leader election uses randomised timeouts.",
        "metadata": {"source": "reading", "week": 2},
    })
    assert r.status_code == 201
    doc = r.json()
    assert doc["id"]
    assert doc["created_at"]
    assert doc["metadata"]["week"] == 2

    # GET by id → 200, same content (first call is a cache miss, populates cache)
    r2 = client.get(f"/documents/{doc['id']}")
    assert r2.status_code == 200
    assert r2.json()["title"] == "Raft paper notes"

    # Second GET should be served from cache and still be correct
    r3 = client.get(f"/documents/{doc['id']}")
    assert r3.status_code == 200
    assert r3.json()["id"] == doc["id"]


def test_get_missing_returns_404(client):
    r = client.get("/documents/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_partial_update_preserves_other_fields(client):
    created = client.post("/documents", json={
        "title": "Original", "content": "body", "metadata": {},
    }).json()

    # PATCH only the title; content must be untouched.
    r = client.patch(f"/documents/{created['id']}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"
    assert r.json()["content"] == "body"


def test_delete_then_404(client):
    created = client.post("/documents", json={
        "title": "Delete me", "content": "x", "metadata": {},
    }).json()

    assert client.delete(f"/documents/{created['id']}").status_code == 204
    assert client.get(f"/documents/{created['id']}").status_code == 404


def test_list_pagination(client):
    for i in range(3):
        client.post("/documents", json={
            "title": f"doc {i}", "content": "c", "metadata": {},
        })
    r = client.get("/documents?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2


def test_metrics_endpoint_exposes_prometheus(client):
    client.get("/health/live")  # generate at least one observation
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text
