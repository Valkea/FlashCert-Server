"""Each token's courses are its own; the app syncs them both ways and learns which ones its other devices deleted."""

import json

from sqlalchemy import create_engine, select, text

from flashcert_share import cli, db, payload
from flashcert_share.app import create_app
from flashcert_share.config import Config


def token(engine, label="other"):
    token_id, raw = db.create_token(engine, label)
    return token_id, {"Authorization": f"Bearer {raw}"}


def put(client, headers, course, uid="course-1"):
    current = client.get(f"/api/v1/courses/{uid}", headers=headers).get_json()
    return client.put(f"/api/v1/courses/{uid}", json={"expected_revision": current.get("revision", 0), **course}, headers=headers)


def test_other_tokens_cannot_see_or_touch_a_course(client, auth, engine, course):
    share_id = put(client, auth, course).get_json()["share_id"]
    client.post(f"/api/public/courses/{share_id}/reports", json={"element": {"kind": "course"}, "message": "Typo"})
    _, other = token(engine)

    assert client.get("/api/v1/courses", headers=other).get_json() == {"courses": [], "gone": []}
    assert client.get("/api/v1/courses/course-1", headers=other).status_code == 404
    assert client.patch("/api/v1/courses/course-1", json={"visibility": "unlisted"}, headers=other).status_code == 404
    assert client.delete("/api/v1/courses/course-1", headers=other).status_code == 404
    assert put(client, other, {**course, "title": "Taken over"}).status_code == 403
    assert client.get("/api/v1/reports?status=all", headers=other).get_json()["reports"] == []
    status = client.get("/api/v1/status", headers=other).get_json()
    assert status["courses"] == 0 and status["open_reports"] == 0 and status["token"] == "other"

    rid = client.get("/api/v1/reports", headers=auth).get_json()["reports"][0]["id"]
    assert client.patch(f"/api/v1/reports/{rid}", json={"status": "resolved"}, headers=other).status_code == 404
    assert client.delete(f"/api/v1/reports/{rid}", headers=other).status_code == 404
    # The owner still has everything, unchanged.
    mine = client.get("/api/v1/courses", headers=auth).get_json()["courses"][0]
    assert mine["title"] == "Hooks and guardrails" and mine["visibility"] == "public" and mine["open_reports"] == 1


def test_owner_downloads_the_course_with_its_edit_time(client, auth, course):
    put(client, auth, {**course, "modified_at": 1_700_000_000_000})
    body = client.get("/api/v1/courses/course-1", headers=auth).get_json()
    assert body["modified_at"] == "2023-11-14T22:13:20+00:00"
    assert [q["id"] for q in body["content"]["questions"]] == ["q1", "q2", "q3", "q4", "q5"]
    assert body["content"]["notes"] == course["notes"]

    # Same content with another edit time: nothing to store.
    again = put(client, auth, {**course, "modified_at": 1_800_000_000_000}).get_json()
    assert not again["changed"] and again["modified_at"] == body["modified_at"]


def test_edit_time_in_the_future_counts_as_now(client, auth, course):
    body = put(client, auth, {**course, "modified_at": 9_999_999_999_999}).get_json()
    assert body["modified_at"] <= body["updated_at"]


def test_private_course_is_a_backup_without_a_page(client, auth, course):
    body = put(client, auth, {**course, "visibility": "private"}).get_json()
    sid = body["share_id"]
    assert body["visibility"] == "private"
    assert client.get(f"/c/{sid}").status_code == 404
    assert client.get(f"/api/public/courses/{sid}").status_code == 404
    assert client.post(f"/api/public/courses/{sid}/reports",
                       json={"element": {"kind": "course"}, "message": "Typo here"}).status_code == 404
    assert client.get("/api/public/courses").get_json()["courses"] == []
    assert [c["uid"] for c in client.get("/api/v1/courses", headers=auth).get_json()["courses"]] == ["course-1"]

    # Shared later: same link.
    r = client.patch("/api/v1/courses/course-1", json={"visibility": "unlisted", "expected_revision": 1}, headers=auth)
    assert r.get_json()["share_id"] == sid
    assert client.get(f"/c/{sid}").status_code == 200


def test_deleted_course_is_listed_as_gone_for_its_owner_only(client, auth, engine, course):
    first = put(client, auth, course).get_json()
    assert client.delete("/api/v1/courses/course-1?reason=bogus", headers=auth).status_code == 400
    assert client.delete("/api/v1/courses/course-1?reason=deleted&expected_revision=1&confirmation=delete-1-deleted", headers=auth).status_code == 204
    put(client, auth, course, uid="course-2")
    client.delete("/api/v1/courses/course-2?expected_revision=1&confirmation=delete-1-removed", headers=auth)

    listing = client.get("/api/v1/courses", headers=auth).get_json()
    assert listing["courses"] == []
    assert [(g["uid"], g["reason"]) for g in listing["gone"]] == [("course-1", "deleted"), ("course-2", "removed")]
    _, other = token(engine)
    assert client.get("/api/v1/courses", headers=other).get_json()["gone"] == []
    assert put(client, other, course).status_code == 403

    # Only an explicit restore brings it back, preserving its link.
    assert put(client, auth, course).status_code == 409
    again = client.post("/api/v1/courses/course-1/restore", json={"revision": 1, "expected_revision": 2,
                        "confirmation": "restore-2-1"}, headers=auth)
    assert again.status_code == 200 and again.get_json()["share_id"] == first["share_id"]
    assert [g["uid"] for g in client.get("/api/v1/courses", headers=auth).get_json()["gone"]] == ["course-2"]


def test_course_without_owner_is_claimed_by_the_first_token_that_sends_it(client, auth, engine, course):
    values = {"title": "Old course", "certification": None, "lang": None, "visibility": "public", "position": None,
              "modified_at": 1, "content": json.dumps({"notes": "x", "sheet": None, "questions": [], "cards": []}),
              "content_hash": "old", "question_count": 0, "card_count": 0}
    db.save_course(engine, "legacy", values, None)
    assert client.get("/api/v1/courses", headers=auth).get_json()["courses"] == []
    assert client.get("/api/v1/courses/legacy", headers=auth).status_code == 404

    meta, content, _ = payload.normalize(course)
    r = put(client, auth, {**course, "expected_revision": 1, "confirmation": payload.content_hash(meta, content)}, uid="legacy")
    assert r.status_code == 200 and not r.get_json()["created"]
    assert [c["uid"] for c in client.get("/api/v1/courses", headers=auth).get_json()["courses"]] == ["legacy"]
    _, other = token(engine)
    assert put(client, other, course, uid="legacy").status_code == 403


def test_revoked_token_keeps_its_id_and_its_courses(client, engine, course):
    old_id, old = token(engine, "lost phone")
    put(client, old, course)
    assert db.revoke_token(engine, old_id) and not db.revoke_token(engine, old_id)
    new_id, new = token(engine, "new phone")
    assert new_id != old_id
    assert client.get("/api/v1/courses", headers=new).get_json()["courses"] == []

    assert db.give_courses(engine, new_id, from_id=old_id) == 1
    assert [c["uid"] for c in client.get("/api/v1/courses", headers=new).get_json()["courses"]] == ["course-1"]
    rows = {t["id"]: t for t in db.list_tokens(engine)}
    assert rows[old_id]["revoked_at"] and rows[old_id]["courses"] == 0 and rows[new_id]["courses"] == 1
    assert db.token_count(engine) == 1


V1 = [
    "CREATE TABLE meta (key VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE token (id INTEGER PRIMARY KEY, label VARCHAR(100) NOT NULL, hash VARCHAR(64) NOT NULL UNIQUE,"
    " created_at BIGINT NOT NULL, last_used_at BIGINT)",
    "CREATE TABLE course (id INTEGER PRIMARY KEY, uid VARCHAR(100) NOT NULL UNIQUE, share_id VARCHAR(16) NOT NULL UNIQUE,"
    " title VARCHAR(300) NOT NULL, certification VARCHAR(200), lang VARCHAR(16), position BIGINT,"
    " visibility VARCHAR(10) NOT NULL, content TEXT NOT NULL, content_hash VARCHAR(64) NOT NULL,"
    " question_count INTEGER NOT NULL, card_count INTEGER NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)",
    "INSERT INTO meta VALUES ('schema_version', '1')",
    "INSERT INTO course VALUES (1, 'u1', 'abcdefgh', 'Old', NULL, NULL, NULL, 'public', '{}', 'h', 0, 0, 1, 1)",
]


def v1_database(path, tokens: int) -> str:
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    with engine.begin() as conn:
        for sql in V1:
            conn.execute(text(sql))
        for i in range(1, tokens + 1):
            conn.execute(text(f"INSERT INTO token VALUES ({i}, 't{i}', 'hash{i}', 1, NULL)"))
    engine.dispose()
    return url


def test_upgrade_gives_old_courses_to_the_only_token(tmp_path):
    engine = db.make_engine(v1_database(tmp_path / "one.db", tokens=1))
    db.init(engine)
    with engine.connect() as conn:
        assert conn.execute(select(db.course.c.owner_id, db.course.c.modified_at)).one() == (1, None)
        assert conn.execute(select(db.meta.c.value)).scalar_one() == str(db.SCHEMA_VERSION)
    db.init(engine)  # a second start changes nothing
    assert db.token_count(engine) == 1


def test_upgrade_with_several_tokens_leaves_old_courses_unowned(tmp_path):
    engine = db.make_engine(v1_database(tmp_path / "two.db", tokens=2))
    db.init(engine)
    with engine.connect() as conn:
        assert conn.execute(select(db.course.c.owner_id)).scalar_one() is None


def test_upgrade_snapshots_existing_content(tmp_path):
    engine = db.make_engine(v1_database(tmp_path / "history.db", tokens=1))
    db.init(engine)
    db.init(engine)
    with engine.connect() as conn:
        row = conn.execute(select(db.course_revision)).one()
        assert row.revision == 1 and json.loads(row.snapshot)["title"] == "Old"


def test_command_line_import_and_owner(tmp_path, capsys, course):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    path = tmp_path / "course.json"
    path.write_text(json.dumps(course))
    engine = db.make_engine(url)
    db.init(engine)
    alice, _ = db.create_token(engine, "alice")
    bob, raw_bob = db.create_token(engine, "bob")

    cli.main(["--database-url", url, "import", str(path), "--uid", "imported", "--owner", str(alice)])
    assert "created: Hooks and guardrails" in capsys.readouterr().out
    cli.main(["--database-url", url, "courses"])
    assert f"#{alice} alice, uid imported" in capsys.readouterr().out

    cli.main(["--database-url", url, "owner", str(bob), "--from", str(alice)])
    assert "1 course(s) now belong to token" in capsys.readouterr().out
    app = create_app(Config(database_url=url, public_url="https://share.example.com", proxies=0))
    rows = app.test_client().get("/api/v1/courses", headers={"Authorization": f"Bearer {raw_bob}"}).get_json()
    assert [c["uid"] for c in rows["courses"]] == ["imported"]
