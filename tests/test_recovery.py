import json

from flashcert_share import db


def create(client, auth, course):
    response = client.put("/api/v1/courses/safe", json={**course, "expected_revision": 0}, headers=auth)
    assert response.status_code == 201
    return response.get_json()


def test_guarded_update_and_stale_delete_cannot_destroy_newer_content(client, auth, course):
    create(client, auth, course)
    update = {**course, "title": "New title"}
    assert client.put("/api/v1/courses/safe", json=update, headers=auth).status_code == 428
    update["expected_revision"] = 1
    assert client.put("/api/v1/courses/safe", json=update, headers=auth).get_json()["revision"] == 2
    assert client.put("/api/v1/courses/safe", json={**course, "expected_revision": 1}, headers=auth).status_code == 409
    assert client.delete("/api/v1/courses/safe?expected_revision=1&confirmation=delete-1-deleted&reason=deleted",
                         headers=auth).status_code == 409
    assert client.get("/api/v1/courses/safe", headers=auth).get_json()["title"] == "New title"


def test_major_change_approval_is_bound_to_exact_content_and_revision(client, auth, course):
    create(client, auth, course)
    replacement = {**course, "notes": "Entirely replaced notes", "questions": [], "cards": [], "expected_revision": 1}
    preview = client.post("/api/v1/courses/safe/preview", json=replacement, headers=auth).get_json()
    assert preview["changes"]["large"]
    assert preview["changes"]["questions"]["removed"] == 5
    assert client.put("/api/v1/courses/safe", json=replacement, headers=auth).status_code == 409
    approved = {**replacement, "confirmation": preview["confirmation"]}
    assert client.put("/api/v1/courses/safe", json={**approved, "title": "Different"}, headers=auth).status_code == 409
    assert client.put("/api/v1/courses/safe", json={**approved, "visibility": "unlisted"}, headers=auth).status_code == 409
    assert client.put("/api/v1/courses/safe", json=approved, headers=auth).status_code == 200
    assert client.put("/api/v1/courses/safe", json={**course, **approved, "notes": "Another replacement"}, headers=auth).status_code == 409
    old = client.get("/api/v1/courses/safe/history/1", headers=auth).get_json()
    assert old["content"]["notes"] == course["notes"] and len(old["content"]["questions"]) == 5


def test_trash_restore_keeps_link_reports_and_invalidates_old_deletion(client, auth, course):
    original = create(client, auth, course)
    client.post(f"/api/public/courses/{original['share_id']}/reports",
                json={"element": {"kind": "course"}, "message": "A typo"})
    assert client.delete("/api/v1/courses/safe?expected_revision=1", headers=auth).status_code == 409
    deletion = "/api/v1/courses/safe?expected_revision=1&reason=deleted&confirmation=delete-1-deleted"
    assert client.delete(deletion, headers=auth).status_code == 204
    assert client.delete(deletion, headers=auth).status_code == 204
    assert client.get("/api/v1/courses/safe", headers=auth).status_code == 404
    assert client.get("/api/v1/trash", headers=auth).get_json()["courses"][0]["revision"] == 2
    restore = {"revision": 1, "expected_revision": 2, "confirmation": "restore-2-1"}
    restored = client.post("/api/v1/courses/safe/restore", json=restore, headers=auth).get_json()
    assert restored["revision"] == 3 and restored["event"] == "restored"
    assert restored["share_id"] == original["share_id"]
    assert client.delete(deletion, headers=auth).status_code == 409
    assert len(client.get("/api/v1/reports", headers=auth).get_json()["reports"]) == 1


def test_recovery_is_owner_only_and_history_is_bounded(client, auth, engine, course):
    create(client, auth, course)
    _, raw = db.create_token(engine)
    other = {"Authorization": f"Bearer {raw}"}
    for endpoint in ("history", "history/1"):
        assert client.get(f"/api/v1/courses/safe/{endpoint}", headers=other).status_code == 404
    assert client.get("/api/v1/trash", headers=other).get_json()["courses"] == []
    assert client.post("/api/v1/courses/safe/restore", json={"revision": 1}, headers=other).status_code == 404
    for revision in range(1, 25):
        assert client.put("/api/v1/courses/safe", json={**course, "title": str(revision),
                          "expected_revision": revision}, headers=auth).status_code == 200
    assert len(client.get("/api/v1/courses/safe/history", headers=auth).get_json()["history"]) == 20


def test_expired_trash_does_not_allow_silent_resurrection(client, auth, engine, course):
    create(client, auth, course)
    client.delete("/api/v1/courses/safe?expected_revision=1&confirmation=delete-1-removed", headers=auth)
    with engine.begin() as conn:
        conn.execute(db.course.update().values(deleted_at=1))
    db.prune(engine)
    assert client.get("/api/v1/trash", headers=auth).get_json()["courses"] == []
    assert client.put("/api/v1/courses/safe", json=course, headers=auth).status_code == 409


def test_same_size_rewrite_and_item_replacement_are_detected():
    from flashcert_share.changes import summarize
    before = {"notes": "one two three four", "questions": [{"id": "q", "q": "one"}], "cards": []}
    after = {"notes": "five six seven eight", "questions": [{"id": "q", "q": "two"}], "cards": []}
    impact = summarize(before, after)
    assert impact["large"] and impact["notes_changed_percent"] == 100
    assert impact["questions"]["edited"] == 1


def test_concurrent_devices_cannot_both_write_the_same_base_revision(app, auth, course):
    from concurrent.futures import ThreadPoolExecutor
    create(app.test_client(), auth, course)
    def update(title):
        with app.test_client() as client:
            return client.put("/api/v1/courses/safe", json={**course, "title": title,
                              "expected_revision": 1}, headers=auth).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["Phone", "Tablet"]))
    assert sorted(outcomes) == [200, 409]
    history = app.test_client().get("/api/v1/courses/safe/history", headers=auth).get_json()["history"]
    assert len(history) == 2


def test_restoring_for_resharing_can_keep_old_content_private(client, auth, course):
    original = create(client, auth, course)
    client.delete("/api/v1/courses/safe?expected_revision=1&confirmation=delete-1-removed", headers=auth)
    body = {"revision": 2, "expected_revision": 2, "visibility": "private", "confirmation": "restore-2-2-private"}
    restored = client.post("/api/v1/courses/safe/restore", json=body, headers=auth).get_json()
    assert restored["visibility"] == "private" and restored["share_id"] == original["share_id"]
    assert client.get(f"/c/{original['share_id']}").status_code == 404


def test_legacy_clients_never_receive_destructive_download_or_deletion_instructions(client, auth, course):
    create(client, auth, course)
    legacy = {"Authorization": auth["Authorization"]}
    assert "gone" not in client.get("/api/v1/courses", headers=legacy).get_json()
    assert client.get("/api/v1/courses/safe", headers=legacy).status_code == 428
    client.delete("/api/v1/courses/safe?expected_revision=1&confirmation=delete-1-removed", headers=auth)
    assert client.get("/api/v1/courses", headers=legacy).get_json() == {"courses": []}
    assert len(client.get("/api/v1/courses", headers=auth).get_json()["gone"]) == 1


def test_replacing_a_nonempty_sheet_requires_review_even_when_notes_and_counts_stay_the_same(client, auth, course):
    create(client, auth, course)
    replacement = {**course, "sheet": {"essentiel": ["Completely unrelated replacement content"]}, "expected_revision": 1}
    response = client.put("/api/v1/courses/safe", json=replacement, headers=auth)
    assert response.status_code == 409
    impact = response.get_json()["changes"]
    assert impact["sheet_changed"] and impact["sheet_changed_percent"] >= 30
    assert impact["notes_changed_percent"] == 0
