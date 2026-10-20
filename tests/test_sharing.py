"""Sharing courses and certifications with other people: links that copy, follow or join to edit, roles and the end
of access."""

import json

from sqlalchemy import inspect, select, text

from flashcert_share import cli, db
from flashcert_share.app import create_app
from flashcert_share.config import Config

V2 = {"X-FlashCert-Sync-Version": "2"}
EDIT = {"link_actions": ["copy", "follow", "edit"]}


def owner(engine, label="alice"):
    token_id, raw = db.create_token(engine, label)
    return token_id, {"Authorization": f"Bearer {raw}", **V2}


def put(client, headers, course, uid="course-1"):
    current = client.get(f"/api/v1/courses/{uid}", headers=headers).get_json()
    return client.put(f"/api/v1/courses/{uid}", json={"expected_revision": current.get("revision", 0), **course},
                      headers=headers)


def editable(client, headers, course, uid="course-1"):
    """The course shared privately, its link letting whoever opens it edit it: its share id."""
    sid = put(client, headers, {**course, "visibility": "unlisted"}, uid=uid).get_json()["share_id"]
    assert client.patch(f"/api/v1/courses/{uid}", json=EDIT, headers=headers).status_code == 200
    return sid


def join(client, body, headers=None):
    return client.post("/api/public/join", json=body, headers=headers or {})


def guest(response):
    body = response.get_json()
    return {"Authorization": f"Bearer {body['token']}", **V2}


def test_joining_from_a_link_makes_a_guest_editor(client, engine, course):
    _, alice = owner(engine)
    sid = editable(client, alice, course)
    r = join(client, {"course": sid, "name": "bob"})
    assert r.status_code == 201 and r.get_json()["kind"] == "guest" and r.get_json()["label"] == "bob"
    assert r.get_json()["server"] == {"name": "FlashCert", "url": "https://share.example.com"}
    bob = guest(r)

    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["uid"] == "course-1" and shared["role"] == "editor" and shared["owner"] == "alice"
    assert "link_actions" not in shared
    body = client.get("/api/v1/courses/course-1", headers=bob).get_json()
    assert body["content"]["notes"] == course["notes"] and body["role"] == "editor"
    # Version 1 clients only see their own courses.
    assert client.get("/api/v1/courses", headers={**bob, "X-FlashCert-Sync-Version": "1"}).get_json()["courses"] == []

    # A guest can't send courses of its own, nor decide who sees the course.
    r = put(client, bob, course, uid="bob-1")
    assert r.status_code == 403 and r.get_json()["code"] == "guest"
    assert client.patch("/api/v1/courses/course-1", json={"visibility": "public"}, headers=bob).status_code == 404
    assert client.delete("/api/v1/courses/course-1", headers=bob).status_code == 404

    # Joining again with the token changes nothing; the owner joining their own course neither.
    assert join(client, {"course": sid}, headers=bob).status_code == 200
    assert join(client, {"course": sid}, headers=alice).get_json()["membership"] is None
    assert join(client, {"course": sid}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert join(client, {"code": "whatever"}).status_code == 400

    # A link only reaches what is still shared: private again, the access ends.
    client.patch("/api/v1/courses/course-1", json={"visibility": "private", "expected_revision": 1}, headers=alice)
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and listing["gone"][0]["reason"] == "revoked"
    assert join(client, {"course": sid}).status_code == 404


def test_editor_changes_reach_the_owner_with_their_author(client, engine, course):
    _, alice = owner(engine)
    sid = editable(client, alice, course)
    client.post(f"/api/public/courses/{sid}/reports", json={"element": {"kind": "course"}, "message": "Typo"})
    _, bob = owner(engine, "bob")
    r = join(client, {"course": sid}, headers=bob)
    assert r.status_code == 200 and "token" not in r.get_json()

    edited = {**course, "title": "Hooks, edited", "visibility": "public", "position": 9, "certification": "Other"}
    edited["cards"] = course["cards"] + [{"id": "c3", "front": "New?", "back": "Yes."}]
    r = put(client, bob, edited)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["changed"] and body["role"] == "editor" and body["owner"] == "alice"
    # The owner keeps where the course is listed and who sees it.
    assert body["visibility"] == "unlisted" and body["position"] == 2 and body["certification"] == "Claude Agent SDK"

    mine = client.get("/api/v1/courses/course-1", headers=alice).get_json()
    assert mine["title"] == "Hooks, edited" and len(mine["content"]["cards"]) == 3 and mine["role"] == "owner"
    history = client.get("/api/v1/courses/course-1/history", headers=alice).get_json()["history"]
    assert [h["author"] for h in history] == ["bob", "alice"]
    assert client.get("/api/v1/courses/course-1/history", headers=bob).status_code == 200

    # Editors read the reports; the owner still decides who sees the course.
    assert len(client.get("/api/v1/reports", headers=bob).get_json()["reports"]) == 1
    assert client.patch("/api/v1/courses/course-1", json={"visibility": "public", "expected_revision": 2},
                        headers=bob).status_code == 404


def test_members_and_the_end_of_their_access(client, engine, course):
    _, alice = owner(engine)
    first = editable(client, alice, course)
    second = editable(client, alice, {**course, "title": "Second"}, uid="course-2")
    bob = guest(join(client, {"course": first, "name": "bob"}))
    carol = guest(join(client, {"course": second, "name": "carol"}))
    assert [c["uid"] for c in client.get("/api/v1/courses", headers=bob).get_json()["courses"]] == ["course-1"]

    members = client.get("/api/v1/members?course=course-1", headers=alice).get_json()["members"]
    assert [(m["label"], m["role"], m["guest"]) for m in members] == [("bob", "editor", True)]
    assert "via" not in members[0]

    # The owner can make an editor a viewer, whose edits are then refused.
    assert client.patch(f"/api/v1/memberships/{members[0]['id']}", json={"role": "viewer"}, headers=bob).status_code == 404
    assert client.patch(f"/api/v1/memberships/{members[0]['id']}", json={"role": "viewer"},
                        headers=alice).get_json()["role"] == "viewer"
    assert client.get("/api/v1/courses", headers=bob).get_json()["courses"][0]["role"] == "viewer"
    r = put(client, bob, {**course, "title": "Mine now"})
    assert r.status_code == 403 and r.get_json()["code"] == "read_only"

    # Removed: the course comes back as revoked until the end, so devices keep their copy, detached.
    assert client.delete(f"/api/v1/memberships/{members[0]['id']}", headers=alice).status_code == 204
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and [(g["uid"], g["reason"]) for g in listing["gone"]] == [("course-1", "revoked")]
    assert client.get("/api/v1/courses/course-1", headers=bob).status_code == 404

    # Leaving, once its devices had the course (before, there is nothing to tell them).
    assert len(client.get("/api/v1/courses", headers=carol).get_json()["courses"]) == 1
    [mine] = client.get("/api/v1/memberships", headers=carol).get_json()["memberships"]
    assert mine["course"]["uid"] == "course-2" and mine["owner"] == "alice" and "via" not in mine
    assert client.delete(f"/api/v1/memberships/{mine['id']}", headers=carol).status_code == 204
    assert client.get("/api/v1/courses", headers=carol).get_json()["gone"][0]["reason"] == "revoked"


def test_deleting_a_shared_course_ends_access(client, engine, course):
    _, alice = owner(engine)
    bob = guest(join(client, {"course": editable(client, alice, course)}))
    client.get("/api/v1/courses", headers=bob)
    rev = client.get("/api/v1/courses/course-1", headers=alice).get_json()["revision"]
    assert client.delete(f"/api/v1/courses/course-1?reason=deleted&expected_revision={rev}"
                         f"&confirmation=delete-{rev}-deleted", headers=alice).status_code == 204
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and listing["gone"][0]["reason"] == "revoked"


def test_shared_certification_page_lists_its_shared_courses(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, {**course, "visibility": "unlisted"})
    put(client, alice, {**course, "title": "Kept private", "visibility": "private"}, uid="course-2")
    r = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "unlisted"}, headers=alice)
    cert = r.get_json()
    assert cert["visibility"] == "unlisted" and cert["courses"] == ["course-1", "course-2"]
    assert cert["actions"] == ["copy", "follow"] and cert["link_actions"] is None

    page = client.get(f"/cert/{cert['share_id']}")
    assert page.status_code == 200 and b"Hooks and guardrails" in page.data and b"Kept private" not in page.data
    assert b"flashcert://add?url=https%3A%2F%2Fshare.example.com%2Fcert%2F" in page.data
    public = client.get(f"/api/public/certifications/{cert['share_id']}").get_json()
    assert [c["title"] for c in public["courses"]] == ["Hooks and guardrails"] and public["name"] == "Claude Agent SDK"

    # No joining from a link that doesn't offer editing.
    assert join(client, {"certification": cert["share_id"]}).status_code == 403
    r = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "unlisted",
                                                  "link_actions": []}, headers=alice)
    assert r.get_json()["actions"] == []
    assert b"flashcert://" not in client.get(f"/cert/{cert['share_id']}").data
    client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "private"}, headers=alice)
    assert client.get(f"/cert/{cert['share_id']}").status_code == 404
    assert [c["name"] for c in client.get("/api/v1/certifications", headers=alice).get_json()["certifications"]] == \
        ["Claude Agent SDK"]


def test_public_course_offers_the_owners_link_actions_and_follows_with_etags(client, engine, course):
    _, alice = owner(engine)
    sid = put(client, alice, course).get_json()["share_id"]
    r = client.get(f"/api/public/courses/{sid}")
    assert r.get_json()["actions"] == ["copy", "follow"] and r.get_json()["revision"] == 1
    etag = r.headers["ETag"]
    assert client.get(f"/api/public/courses/{sid}", headers={"If-None-Match": etag}).status_code == 304
    assert b"flashcert://add?url=https%3A%2F%2Fshare.example.com%2Fc%2F" in client.get(f"/c/{sid}").data

    r = client.patch("/api/v1/courses/course-1", json={"link_actions": ["follow"]}, headers=alice)
    assert r.get_json()["link_actions"] == ["follow"] and r.get_json()["actions"] == ["follow"]
    for unknown in (["steal"], ["join"]):
        assert client.patch("/api/v1/courses/course-1", json={"link_actions": unknown}, headers=alice).status_code == 400
    client.patch("/api/v1/courses/course-1", json={"link_actions": []}, headers=alice)
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == []
    assert b"flashcert://" not in client.get(f"/c/{sid}").data

    put(client, alice, {**course, "title": "Changed"})
    assert client.get(f"/api/public/courses/{sid}", headers={"If-None-Match": etag}).status_code == 200


def server(tmp_path, **settings):
    cfg = Config(database_url=f"sqlite:///{tmp_path / 'join.db'}", public_url="https://share.example.com", proxies=0,
                 **settings)
    app = create_app(cfg)
    return app.test_client(), app.extensions["flashcert"]["engine"]


def test_the_server_bounds_what_links_offer(tmp_path, course):
    client, engine = server(tmp_path, link_actions=("copy", "follow"))
    _, alice = owner(engine)
    sid = editable(client, alice, course)
    r = client.get("/api/v1/courses/course-1", headers=alice).get_json()
    # The owner's choice is kept; editing isn't allowed on this server.
    assert r["link_actions"] == ["copy", "follow", "edit"] and r["actions"] == ["copy", "follow"]
    assert join(client, {"course": sid}).status_code == 403
    assert client.get("/api/v1/status", headers=alice).get_json()["link_actions"] == ["copy", "follow"]


def test_editing_from_a_link_only_when_the_owner_chose_it_and_never_in_public(client, engine, course):
    _, alice = owner(engine)
    sid = put(client, alice, {**course, "visibility": "unlisted"}).get_json()["share_id"]
    # Allowed by the server, but never part of its default: a course that didn't choose offers no editing.
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow"]
    assert join(client, {"course": sid}).status_code == 403

    r = client.patch("/api/v1/courses/course-1", json=EDIT, headers=alice)
    assert r.get_json()["actions"] == ["copy", "follow", "edit"]
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow", "edit"]
    bob = guest(join(client, {"course": sid, "name": "bob"}))
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["role"] == "editor" and shared["owner"] == "alice"

    # Listed, anyone can find it: the link no longer makes editors. Those already in stay, removable by the owner.
    client.patch("/api/v1/courses/course-1", json={"visibility": "public", "expected_revision": 1}, headers=alice)
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow"]
    assert join(client, {"course": sid}).status_code == 403
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["role"] == "editor"


def test_a_certification_link_covers_its_shared_courses_now_and_later(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, {**course, "visibility": "unlisted"})
    put(client, alice, {**course, "title": "Kept private", "visibility": "private"}, uid="course-2")
    put(client, alice, {**course, "title": "Elsewhere", "certification": "Other"}, uid="other")
    cert = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "unlisted", **EDIT},
                      headers=alice).get_json()
    assert cert["actions"] == ["copy", "follow", "edit"]
    assert client.get(f"/api/public/certifications/{cert['share_id']}").get_json()["actions"] == ["copy", "follow", "edit"]
    bob = guest(join(client, {"certification": cert["share_id"]}))
    listing = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    # Only what the link shows: the private course stays out.
    assert [(c["uid"], c["role"]) for c in listing] == [("course-1", "editor")]
    [membership] = client.get("/api/v1/memberships", headers=bob).get_json()["memberships"]
    assert membership["certification"]["name"] == "Claude Agent SDK"
    assert len(client.get("/api/v1/members?certification=Claude Agent SDK", headers=alice).get_json()["members"]) == 1

    # Added later, it comes in; moved to another certification by its owner, it goes out.
    put(client, alice, {**course, "title": "Added later", "visibility": "unlisted"}, uid="course-3")
    listing = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert sorted(c["uid"] for c in listing) == ["course-1", "course-3"]
    put(client, alice, {**course, "title": "Added later", "certification": "Other", "visibility": "unlisted"},
        uid="course-3")
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert [c["uid"] for c in listing["courses"]] == ["course-1"] and listing["gone"][0]["uid"] == "course-3"

    public = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "public"},
                        headers=alice).get_json()
    assert public["actions"] == ["copy", "follow"] and public["link_actions"] == ["copy", "follow", "edit"]
    assert join(client, {"certification": cert["share_id"]}).status_code == 403

    # Not shared any more: those who joined lose its courses.
    client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "private"}, headers=alice)
    assert client.get("/api/v1/courses", headers=bob).get_json()["courses"] == []


def test_joins_are_rate_limited(tmp_path, course):
    client, engine = server(tmp_path, joins_per_hour=2)
    _, alice = owner(engine)
    sid = editable(client, alice, course)
    assert join(client, {"course": sid}).status_code == 201
    assert join(client, {"course": sid}).status_code == 201
    assert join(client, {"course": sid}).status_code == 429


def test_a_guest_can_be_promoted(tmp_path, capsys, course):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    app = create_app(Config(database_url=url, public_url="https://share.example.com", proxies=0))
    client, engine = app.test_client(), app.extensions["flashcert"]["engine"]
    _, alice = owner(engine)
    r = join(client, {"course": editable(client, alice, course), "name": "bob"})
    bob, bob_id = guest(r), r.get_json()["token_id"]
    assert put(client, bob, course, uid="bob-1").status_code == 403

    cli.main(["--database-url", url, "token", "list"])
    assert "bob (guest)" in capsys.readouterr().out
    cli.main(["--database-url", url, "token", "promote", str(bob_id)])
    assert "Promoted" in capsys.readouterr().out
    assert put(client, bob, course, uid="bob-1").status_code == 201
    assert client.get("/api/v1/status", headers=bob).get_json()["kind"] == "user"


def test_status_tells_the_app_what_the_server_offers(client, engine):
    _, alice = owner(engine)
    status = client.get("/api/v1/status", headers=alice).get_json()
    assert status["sync_version"] == 2 and status["kind"] == "user"
    assert status["link_actions"] == ["copy", "follow", "edit"] and status["site_name"] == "FlashCert"
    assert "invites" not in status


def test_revisions_remember_their_author(client, engine, course):
    alice_id, alice = owner(engine)
    put(client, alice, course)
    with engine.connect() as conn:
        assert conn.execute(select(db.course_revision.c.token_id)).scalar_one() == alice_id
        snapshot = json.loads(conn.execute(select(db.course_revision.c.snapshot)).scalar_one())
    assert snapshot["link_actions"] is None


def test_people_choose_the_name_others_see(tmp_path, capsys, course):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    app = create_app(Config(database_url=url, public_url="https://share.example.com", proxies=0))
    client, engine = app.test_client(), app.extensions["flashcert"]["engine"]
    alice_id, alice = owner(engine, label="pixel")
    sid = editable(client, alice, course)
    r = client.patch("/api/v1/me", json={"name": "  Alice Martin "}, headers=alice)
    assert r.status_code == 200 and r.get_json() == {"token_id": alice_id, "name": "Alice Martin"}
    for bad in ({"name": "   "}, {"name": "x" * 101}, {"name": 3}, {}):
        assert client.patch("/api/v1/me", json=bad, headers=alice).status_code == 400
    assert client.patch("/api/v1/me", json={"name": "Eve"}).status_code == 401

    # The owner's name on the course it shares.
    bob = guest(join(client, {"course": sid, "name": "bob"}))
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["owner"] == "Alice Martin"

    # A member's name: joining again with a name renames the token, and so does the app's setting.
    assert join(client, {"course": sid, "name": "Bob Durand"}, bob).status_code == 200

    def member():
        [m] = client.get("/api/v1/members?course=course-1", headers=alice).get_json()["members"]
        return m["label"]

    assert member() == "Bob Durand"
    client.patch("/api/v1/me", json={"name": "Robert"}, headers=bob)
    assert member() == "Robert" and client.get("/api/v1/status", headers=bob).get_json()["token"] == "Robert"
    # Without a name, joining keeps it.
    assert join(client, {"course": sid}, bob).status_code == 200 and member() == "Robert"

    cli.main(["--database-url", url, "token", "rename", str(alice_id), "--label", "Alice"])
    assert "Renamed" in capsys.readouterr().out
    assert client.get("/api/v1/courses", headers=bob).get_json()["courses"][0]["owner"] == "Alice"


def test_upgrade_drops_invitations(tmp_path):
    url = f"sqlite:///{tmp_path / 'v4.db'}"
    engine = db.make_engine(url)
    db.init(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE invite (id INTEGER PRIMARY KEY, hash VARCHAR(64))"))
        conn.execute(text("ALTER TABLE membership ADD COLUMN via VARCHAR(10) NOT NULL DEFAULT 'invite'"))
        conn.execute(db.meta.update().values(value="4"))
    db.init(engine)
    with engine.connect() as conn:
        tables = inspect(conn).get_table_names()
        assert "invite" not in tables and "via" not in {c["name"] for c in inspect(conn).get_columns("membership")}
        assert conn.execute(select(db.meta.c.value)).scalar_one() == str(db.SCHEMA_VERSION)
