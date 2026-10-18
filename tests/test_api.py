from flashcert_share import db


def put(client, auth, course, uid="course-1"):
    current = client.get(f"/api/v1/courses/{uid}", headers=auth).get_json()
    return client.put(f"/api/v1/courses/{uid}", json={**course, "expected_revision": current.get("revision", 0)}, headers=auth)


def test_sync_needs_a_valid_token(client, course):
    assert client.get("/api/v1/status").status_code == 401
    assert client.put("/api/v1/courses/x", json=course, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_revoked_token_is_refused(client, engine, course):
    token_id, raw = db.create_token(engine)
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/status", headers=headers).status_code == 200
    db.revoke_token(engine, token_id)
    assert client.get("/api/v1/status", headers=headers).status_code == 401


def test_tokens_are_stored_hashed(engine):
    _, raw = db.create_token(engine)
    stored = [r["id"] for r in db.list_tokens(engine)]
    assert stored
    with engine.connect() as conn:
        hashes = [r.hash for r in conn.execute(db.token.select())]
    assert raw not in hashes and db.hash_token(raw) in hashes


def test_put_creates_then_updates_with_a_stable_link(client, auth, course):
    r = put(client, auth, course)
    assert r.status_code == 201
    body = r.get_json()
    assert body["created"] and body["changed"]
    assert body["url"] == f"https://share.example.com/c/{body['share_id']}"
    assert body["questions"] == 5 and body["cards"] == 2

    same = put(client, auth, course).get_json()
    assert not same["created"] and not same["changed"]
    assert same["updated_at"] == body["updated_at"]

    course["title"] = "Hooks, guardrails and subagents"
    changed = put(client, auth, course).get_json()
    assert changed["changed"] and changed["share_id"] == body["share_id"]


def test_public_list_hides_unlisted_courses(client, auth, course):
    put(client, auth, course, "a")
    course["visibility"] = "unlisted"
    course["title"] = "Secret course"
    unlisted = put(client, auth, course, "b").get_json()

    listed = client.get("/api/public/courses").get_json()["courses"]
    assert [c["title"] for c in listed] == ["Hooks and guardrails"]
    # Unlisted stays reachable through its link.
    assert client.get(f"/api/public/courses/{unlisted['share_id']}").status_code == 200
    page = client.get(f"/c/{unlisted['share_id']}")
    assert page.status_code == 200 and b'name="robots" content="noindex"' in page.data
    assert b"Secret course" not in client.get("/").data


def test_patch_visibility_without_resending(client, auth, course):
    body = put(client, auth, course).get_json()
    r = client.patch("/api/v1/courses/course-1", json={"visibility": "unlisted", "expected_revision": body["revision"]}, headers=auth)
    assert r.get_json()["visibility"] == "unlisted"
    assert client.get("/api/public/courses").get_json()["courses"] == []
    assert r.get_json()["share_id"] == body["share_id"]


def test_public_course_has_no_private_fields(client, auth, course):
    course_with_extras = {**course, "format": "flashcert-course-1", "out_lang": "en",
                          "transcript": [{"text": "secret"}], "costs": [{"cost": 1}], "analysis": {"x": 1}}
    share_id = put(client, auth, course_with_extras).get_json()["share_id"]
    data = client.get(f"/api/public/courses/{share_id}").get_json()
    assert set(data) == {"share_id", "title", "certification", "lang", "updated_at", "notes", "sheet", "questions", "cards"}
    assert "uid" not in data


def test_invalid_items_are_dropped_with_warnings(client, auth, course):
    course["questions"].append({"type": "mcq", "q": "Broken", "answers": ["a"], "correct": 3})
    course["questions"].append({"type": "essay", "q": "Unknown type"})
    course["cards"].append({"front": "", "back": "x"})
    body = put(client, auth, course).get_json()
    assert body["questions"] == 5 and body["cards"] == 2
    assert len(body["warnings"]) == 3


def test_ids_are_given_and_made_unique(client, auth, course):
    course["questions"][1]["id"] = "q1"
    del course["questions"][2]["id"]
    body = client.get(f"/api/v1/courses/{'course-1'}", headers=auth)
    assert body.status_code == 404
    put(client, auth, course)
    ids = [q["id"] for q in client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]["questions"]]
    assert len(set(ids)) == len(ids) == 5
    assert ids[1] == "q1-2" and ids[2].startswith("q")


def test_older_single_source_becomes_a_list(client, auth, course):
    share_id = put(client, auth, course).get_json()["share_id"]
    card = client.get(f"/api/public/courses/{share_id}").get_json()["cards"][0]
    assert card["sources"] == [{"section_id": "1.1", "quote": "a control that denies"}]


def test_rejects_bad_documents(client, auth, course):
    assert put(client, auth, {"format": "other"}).status_code == 422
    assert put(client, auth, {**course, "title": ""}).status_code == 422
    assert put(client, auth, {**course, "visibility": "secret"}).status_code == 422
    assert put(client, auth, course, uid="bad uid!").status_code in (400, 404)
    assert client.put("/api/v1/courses/x", data="not json", headers={**auth, "Content-Type": "application/json"}).status_code == 400


def test_delete_hides_course_and_preserves_reports_for_recovery(client, auth, engine, course):
    share_id = put(client, auth, course).get_json()["share_id"]
    client.post(f"/api/public/courses/{share_id}/reports", json={"element": {"kind": "course"}, "message": "Typo here"})
    url = "/api/v1/courses/course-1?expected_revision=1&confirmation=delete-1-removed"
    assert client.delete(url, headers=auth).status_code == 204
    assert client.get(f"/c/{share_id}").status_code == 404
    with engine.connect() as conn:
        assert conn.execute(db.report.select()).first() is not None
    assert client.delete(url, headers=auth).status_code == 204


def test_list_courses_for_the_app(client, auth, course):
    put(client, auth, course)
    rows = client.get("/api/v1/courses", headers=auth).get_json()["courses"]
    assert rows[0]["uid"] == "course-1" and rows[0]["open_reports"] == 0 and len(rows[0]["hash"]) == 64
    status = client.get("/api/v1/status", headers=auth).get_json()
    assert status["courses"] == 1 and status["base_url"] == "https://share.example.com"


def test_items_come_back_as_the_app_sent_them(client, auth, course):
    # What the app keeps and sends: checklist ids, right options in its own order, no empty lists filled in.
    course["questions"][0]["objective_ids"] = ["chapter:notion"]
    course["questions"][1].update(correct=[2, 0], objective_ids=[])
    course["questions"][3] = {"id": "q4", "type": "cloze", "q": "Exit code ＿＿＿ blocks.", "answer": "2"}
    course["cards"][1]["objective_ids"] = ["chapter:other"]
    put(client, auth, course)
    content = client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]
    assert content["questions"] == course["questions"]
    assert content["cards"][1] == course["cards"][1]


def test_checklist_ids_stay_out_of_the_pages(client, auth, course):
    course["questions"][0]["objective_ids"] = ["chapter:notion"]
    course["cards"][0]["objective_ids"] = ["chapter:notion"]
    share_id = put(client, auth, course).get_json()["share_id"]
    data = client.get(f"/api/public/courses/{share_id}").get_json()
    assert all("objective_ids" not in item for item in data["questions"] + data["cards"])
    assert b"chapter:notion" not in client.get(f"/c/{share_id}").data


def test_malformed_checklist_ids_are_dropped(client, auth, course):
    course["questions"][0]["objective_ids"] = "chapter:notion"
    course["questions"][1]["objective_ids"] = ["x" * 201]
    put(client, auth, course)
    questions = client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]["questions"]
    assert "objective_ids" not in questions[0] and "objective_ids" not in questions[1]


def test_importance_and_notions_reach_the_pages(client, auth, course):
    course["questions"][0]["importance"] = 3
    course["questions"][1]["importance"] = 0
    course["cards"][0]["importance"] = 1
    course["notions"] = [{"chapter": "Guardrails", "quote": "denies or gates a dangerous action", "importance": 3}]
    share_id = put(client, auth, course).get_json()["share_id"]
    stored = client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]
    assert stored["questions"][:2] == course["questions"][:2] and stored["notions"] == course["notions"]
    data = client.get(f"/api/public/courses/{share_id}").get_json()
    assert [q.get("importance") for q in data["questions"]] == [3, 0, None, None, None]
    assert data["cards"][0]["importance"] == 1 and data["notions"] == course["notions"]


def test_invalid_importance_and_notions_are_dropped(client, auth, course):
    course["questions"][0]["importance"] = 4
    course["questions"][1]["importance"] = True
    course["notions"] = [{"chapter": "Guardrails", "quote": "x", "importance": 0}, {"chapter": "", "quote": "x", "importance": 2},
                         "nope", {"chapter": "Guardrails", "quote": "Least privilege", "importance": 2}]
    body = put(client, auth, course).get_json()
    assert len(body["warnings"]) == 3
    content = client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]
    assert "importance" not in content["questions"][0] and "importance" not in content["questions"][1]
    assert content["notions"] == [{"chapter": "Guardrails", "quote": "Least privilege", "importance": 2}]
    assert put(client, auth, {**course, "notions": {"a": 1}}).status_code == 422


def test_a_course_without_notions_keeps_its_hash(client, auth, course):
    first = put(client, auth, course).get_json()
    assert "notions" not in client.get("/api/v1/courses/course-1", headers=auth).get_json()["content"]
    assert not put(client, auth, course).get_json()["changed"]
    assert put(client, auth, {**course, "notions": []}).get_json()["hash"] != first["hash"]
