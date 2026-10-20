"""Sharing courses and certifications with other people: invites, links that join, roles and the end of access."""

import json

from sqlalchemy import select

from flashcert_share import cli, db
from flashcert_share.app import create_app
from flashcert_share.config import Config

V2 = {"X-FlashCert-Sync-Version": "2"}


def owner(engine, label="alice"):
    token_id, raw = db.create_token(engine, label)
    return token_id, {"Authorization": f"Bearer {raw}", **V2}


def put(client, headers, course, uid="course-1"):
    current = client.get(f"/api/v1/courses/{uid}", headers=headers).get_json()
    return client.put(f"/api/v1/courses/{uid}", json={"expected_revision": current.get("revision", 0), **course},
                      headers=headers)


def invite(client, headers, **body):
    r = client.post("/api/v1/invites", json=body, headers=headers)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def join(client, body, headers=None):
    return client.post("/api/public/join", json=body, headers=headers or {})


def guest(response):
    body = response.get_json()
    return {"Authorization": f"Bearer {body['token']}", **V2}


def test_invite_makes_a_guest_who_reads_the_course(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, {**course, "visibility": "private"})
    inv = invite(client, alice, course="course-1")
    assert inv["url"] == f"https://share.example.com/join/{inv['code']}" and inv["role"] == "viewer"

    preview = client.get(f"/api/public/invites/{inv['code']}").get_json()
    assert preview["title"] == "Hooks and guardrails" and preview["owner"] == "alice" and preview["kind"] == "course"
    page = client.get(f"/join/{inv['code']}")
    assert page.status_code == 200 and b"flashcert://add?url=https%3A%2F%2Fshare.example.com%2Fjoin%2F" in page.data

    r = join(client, {"code": inv["code"], "name": "bob"})
    assert r.status_code == 201 and r.get_json()["kind"] == "guest" and r.get_json()["label"] == "bob"
    assert r.get_json()["server"] == {"name": "FlashCert", "url": "https://share.example.com"}
    bob = guest(r)

    listing = client.get("/api/v1/courses", headers=bob).get_json()
    [shared] = listing["courses"]
    assert shared["uid"] == "course-1" and shared["role"] == "viewer" and shared["owner"] == "alice"
    assert "link_actions" not in shared and "open_reports" not in shared
    body = client.get("/api/v1/courses/course-1", headers=bob).get_json()
    assert body["content"]["notes"] == course["notes"] and body["role"] == "viewer"
    # Version 1 clients only see their own courses.
    assert client.get("/api/v1/courses", headers={**bob, "X-FlashCert-Sync-Version": "1"}).get_json()["courses"] == []

    # Read only, and a guest can't send courses of its own.
    r = put(client, bob, {**course, "title": "Mine now"})
    assert r.status_code == 403 and r.get_json()["code"] == "read_only"
    r = put(client, bob, course, uid="bob-1")
    assert r.status_code == 403 and r.get_json()["code"] == "guest"
    assert client.patch("/api/v1/courses/course-1", json={"visibility": "public"}, headers=bob).status_code == 404
    assert client.delete("/api/v1/courses/course-1", headers=bob).status_code == 404

    # Used up: the code and its page are gone.
    assert join(client, {"code": inv["code"]}).status_code == 404
    assert client.get(f"/join/{inv['code']}").status_code == 404
    assert client.get(f"/api/public/invites/{inv['code']}").status_code == 404


def test_editor_changes_reach_the_owner_with_their_author(client, engine, course):
    _, alice = owner(engine)
    first = put(client, alice, {**course, "visibility": "unlisted"}).get_json()
    client.post(f"/api/public/courses/{first['share_id']}/reports", json={"element": {"kind": "course"}, "message": "Typo"})
    _, bob = owner(engine, "bob")
    r = join(client, {"code": invite(client, alice, course="course-1", role="editor")["code"]}, headers=bob)
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
    put(client, alice, course)
    put(client, alice, {**course, "title": "Second"}, uid="course-2")
    bob = guest(join(client, {"code": invite(client, alice, course="course-1", uses=2)["code"], "name": "bob"}))
    carol = guest(join(client, {"code": invite(client, alice, course="course-2")["code"], "name": "carol"}))
    assert [c["uid"] for c in client.get("/api/v1/courses", headers=bob).get_json()["courses"]] == ["course-1"]

    members = client.get("/api/v1/members?course=course-1", headers=alice).get_json()["members"]
    assert [(m["label"], m["role"], m["guest"], m["via"]) for m in members] == [("bob", "viewer", True, "invite")]
    assert client.patch(f"/api/v1/memberships/{members[0]['id']}", json={"role": "editor"}, headers=bob).status_code == 404
    assert client.patch(f"/api/v1/memberships/{members[0]['id']}", json={"role": "editor"},
                        headers=alice).get_json()["role"] == "editor"
    assert client.get("/api/v1/courses", headers=bob).get_json()["courses"][0]["role"] == "editor"

    # Removed: the course comes back as revoked until the end, so devices keep their copy, detached.
    assert client.delete(f"/api/v1/memberships/{members[0]['id']}", headers=alice).status_code == 204
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and [(g["uid"], g["reason"]) for g in listing["gone"]] == [("course-1", "revoked")]
    assert client.get("/api/v1/courses/course-1", headers=bob).status_code == 404

    # Leaving, once its devices had the course (before, there is nothing to tell them).
    assert len(client.get("/api/v1/courses", headers=carol).get_json()["courses"]) == 1
    [mine] = client.get("/api/v1/memberships", headers=carol).get_json()["memberships"]
    assert mine["course"]["uid"] == "course-2" and mine["owner"] == "alice"
    assert client.delete(f"/api/v1/memberships/{mine['id']}", headers=carol).status_code == 204
    assert client.get("/api/v1/courses", headers=carol).get_json()["gone"][0]["reason"] == "revoked"


def test_deleting_a_shared_course_ends_access(client, engine, course):
    _, alice = owner(engine)
    first = put(client, alice, course).get_json()
    bob = guest(join(client, {"code": invite(client, alice, course="course-1")["code"]}))
    client.get("/api/v1/courses", headers=bob)
    rev = first["revision"]
    assert client.delete(f"/api/v1/courses/course-1?reason=deleted&expected_revision={rev}"
                         f"&confirmation=delete-{rev}-deleted", headers=alice).status_code == 204
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and listing["gone"][0]["reason"] == "revoked"


def test_certification_invite_covers_its_courses_now_and_later(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, {**course, "visibility": "private"})
    put(client, alice, {**course, "title": "Elsewhere", "certification": "Other"}, uid="other")
    inv = invite(client, alice, certification="Claude Agent SDK", role="editor")
    assert client.get(f"/api/public/invites/{inv['code']}").get_json()["courses"] == 1
    bob = guest(join(client, {"code": inv["code"]}))
    put(client, alice, {**course, "title": "Added later", "visibility": "private"}, uid="course-2")

    listing = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert sorted((c["uid"], c["role"]) for c in listing) == [("course-1", "editor"), ("course-2", "editor")]
    [membership] = client.get("/api/v1/memberships", headers=bob).get_json()["memberships"]
    assert membership["certification"]["name"] == "Claude Agent SDK"
    members = client.get("/api/v1/members?certification=Claude Agent SDK", headers=alice).get_json()["members"]
    assert len(members) == 1

    # Moved to another certification by its owner: out of the shared one.
    put(client, alice, {**course, "title": "Added later", "certification": "Other", "visibility": "private"},
        uid="course-2")
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert [c["uid"] for c in listing["courses"]] == ["course-1"] and listing["gone"][0]["uid"] == "course-2"


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

    # No join from the link on this server's settings.
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

    r = client.patch("/api/v1/courses/course-1", json={"link_actions": ["follow", "join"]}, headers=alice)
    # "join" isn't allowed on this server.
    assert r.get_json()["link_actions"] == ["follow", "join"] and r.get_json()["actions"] == ["follow"]
    assert client.patch("/api/v1/courses/course-1", json={"link_actions": ["steal"]}, headers=alice).status_code == 400
    client.patch("/api/v1/courses/course-1", json={"link_actions": []}, headers=alice)
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == []
    assert b"flashcert://" not in client.get(f"/c/{sid}").data

    put(client, alice, {**course, "title": "Changed"})
    assert client.get(f"/api/public/courses/{sid}", headers={"If-None-Match": etag}).status_code == 200


def joining_app(tmp_path, **settings):
    cfg = Config(database_url=f"sqlite:///{tmp_path / 'join.db'}", public_url="https://share.example.com", proxies=0,
                 link_actions=("copy", "follow", "join"), **settings)
    app = create_app(cfg)
    return app, app.test_client(), app.extensions["flashcert"]["engine"]


def test_joining_from_a_link_when_the_server_allows_it(tmp_path, course):
    _, client, engine = joining_app(tmp_path)
    _, alice = owner(engine)
    sid = put(client, alice, course).get_json()["share_id"]
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow", "join"]

    r = join(client, {"course": sid})
    assert r.status_code == 201 and r.get_json()["label"] == "guest"
    bob = guest(r)
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["role"] == "viewer"
    # Joining again with the token changes nothing; the owner joining their own course neither.
    assert join(client, {"course": sid}, headers=bob).status_code == 200
    assert join(client, {"course": sid}, headers=alice).get_json()["membership"] is None
    assert join(client, {"course": sid}, headers={"Authorization": "Bearer nope"}).status_code == 401

    # A link only reaches what is still shared: private again, the access ends.
    client.patch("/api/v1/courses/course-1", json={"visibility": "private", "expected_revision": 1}, headers=alice)
    listing = client.get("/api/v1/courses", headers=bob).get_json()
    assert listing["courses"] == [] and listing["gone"][0]["reason"] == "revoked"
    assert join(client, {"course": sid}).status_code == 404


def test_editing_from_a_link_only_when_the_owner_chose_it_and_never_in_public(client, engine, course):
    _, alice = owner(engine)
    sid = put(client, alice, {**course, "visibility": "unlisted"}).get_json()["share_id"]
    # Allowed by the server, but never part of its default: a course that didn't choose offers no editing.
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow"]
    assert join(client, {"course": sid}).status_code == 403

    r = client.patch("/api/v1/courses/course-1", json={"link_actions": ["copy", "follow", "edit"]}, headers=alice)
    assert r.get_json()["actions"] == ["copy", "follow", "edit"]
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow", "edit"]
    r = join(client, {"course": sid, "name": "bob"})
    assert r.status_code == 201
    bob = guest(r)
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["role"] == "editor" and shared["owner"] == "alice"
    [member] = client.get("/api/v1/members?course=course-1", headers=alice).get_json()["members"]
    assert member["label"] == "bob" and member["role"] == "editor" and member["via"] == "link"

    # Listed, anyone can find it: the link no longer makes editors. Those already in stay, removable by the owner.
    client.patch("/api/v1/courses/course-1", json={"visibility": "public", "expected_revision": 1}, headers=alice)
    assert client.get(f"/api/public/courses/{sid}").get_json()["actions"] == ["copy", "follow"]
    assert join(client, {"course": sid}).status_code == 403
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["role"] == "editor"


def test_a_certification_link_can_make_editors_of_its_courses(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, {**course, "visibility": "unlisted"})
    put(client, alice, {**course, "title": "Kept private", "visibility": "private"}, uid="course-2")
    cert = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "unlisted",
                                                     "link_actions": ["copy", "follow", "edit"]}, headers=alice).get_json()
    assert cert["actions"] == ["copy", "follow", "edit"]
    assert client.get(f"/api/public/certifications/{cert['share_id']}").get_json()["actions"] == ["copy", "follow", "edit"]
    bob = guest(join(client, {"certification": cert["share_id"]}))
    listing = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    # From a link, only what the link shows: the private course stays out.
    assert [(c["uid"], c["role"]) for c in listing] == [("course-1", "editor")]

    public = client.put("/api/v1/certifications", json={"name": "Claude Agent SDK", "visibility": "public"},
                        headers=alice).get_json()
    assert public["actions"] == ["copy", "follow"] and public["link_actions"] == ["copy", "follow", "edit"]
    assert join(client, {"certification": cert["share_id"]}).status_code == 403


def test_joins_are_rate_limited_and_invites_can_be_turned_off(tmp_path, course):
    _, client, engine = joining_app(tmp_path, joins_per_hour=2, invites=False)
    _, alice = owner(engine)
    sid = put(client, alice, course).get_json()["share_id"]
    assert client.post("/api/v1/invites", json={"course": "course-1"}, headers=alice).status_code == 403
    assert join(client, {"code": "whatever"}).status_code == 404
    assert join(client, {"course": sid}).status_code == 201
    assert join(client, {"course": sid}).status_code == 201
    assert join(client, {"course": sid}).status_code == 429


def test_an_invite_to_a_deleted_course_is_not_used_up(client, engine, course):
    _, alice = owner(engine)
    first = put(client, alice, course).get_json()
    inv = invite(client, alice, course="course-1")
    rev = first["revision"]
    client.delete(f"/api/v1/courses/course-1?reason=deleted&expected_revision={rev}&confirmation=delete-{rev}-deleted",
                  headers=alice)
    assert join(client, {"code": inv["code"]}).status_code == 404
    with engine.connect() as conn:
        assert conn.execute(select(db.invite.c.uses_left)).scalar_one() == 1


def test_invites_expire_and_can_be_revoked(client, engine, course):
    _, alice = owner(engine)
    put(client, alice, course)
    old = invite(client, alice, course="course-1")
    with engine.begin() as conn:
        conn.execute(db.invite.update().where(db.invite.c.id == old["id"]).values(expires_at=db.now_ms() - 1))
    assert join(client, {"code": old["code"]}).status_code == 404

    live = invite(client, alice, course="course-1", uses=3, days=0)
    assert live["expires_at"] is None
    assert [i["id"] for i in client.get("/api/v1/invites", headers=alice).get_json()["invites"]] == [live["id"]]
    assert "code" not in client.get("/api/v1/invites", headers=alice).get_json()["invites"][0]
    assert client.delete(f"/api/v1/invites/{live['id']}", headers=alice).status_code == 204
    assert join(client, {"code": live["code"]}).status_code == 404
    assert client.post("/api/v1/invites", json={"course": "course-1", "role": "owner"}, headers=alice).status_code == 400
    assert client.post("/api/v1/invites", json={"course": "nope"}, headers=alice).status_code == 404


def test_a_guest_can_be_promoted(tmp_path, capsys, course):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    app = create_app(Config(database_url=url, public_url="https://share.example.com", proxies=0))
    client, engine = app.test_client(), app.extensions["flashcert"]["engine"]
    _, alice = owner(engine)
    put(client, alice, course)
    r = join(client, {"code": invite(client, alice, course="course-1")["code"], "name": "bob"})
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
    assert status["link_actions"] == ["copy", "follow", "edit"]
    assert status["invites"] is True and status["site_name"] == "FlashCert"


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
    put(client, alice, {**course, "visibility": "private"})
    r = client.patch("/api/v1/me", json={"name": "  Alice Martin "}, headers=alice)
    assert r.status_code == 200 and r.get_json() == {"token_id": alice_id, "name": "Alice Martin"}
    for bad in ({"name": "   "}, {"name": "x" * 101}, {"name": 3}, {}):
        assert client.patch("/api/v1/me", json=bad, headers=alice).status_code == 400
    assert client.patch("/api/v1/me", json={"name": "Eve"}).status_code == 401

    # The owner's name on the invite and on the course it shares.
    code = invite(client, alice, course="course-1")["code"]
    assert client.get(f"/api/public/invites/{code}").get_json()["owner"] == "Alice Martin"
    bob = guest(join(client, {"code": code, "name": "bob"}))
    [shared] = client.get("/api/v1/courses", headers=bob).get_json()["courses"]
    assert shared["owner"] == "Alice Martin"

    # A member's name: joining again with a name renames the token, and so does the app's setting.
    code = invite(client, alice, course="course-1", role="editor")["code"]
    assert join(client, {"code": code, "name": "Bob Durand"}, bob).status_code == 200

    def member():
        [m] = client.get("/api/v1/members?course=course-1", headers=alice).get_json()["members"]
        return m["label"]

    assert member() == "Bob Durand"
    client.patch("/api/v1/me", json={"name": "Robert"}, headers=bob)
    assert member() == "Robert" and client.get("/api/v1/status", headers=bob).get_json()["token"] == "Robert"
    # Without a name, joining keeps it.
    code = invite(client, alice, course="course-1")["code"]
    assert join(client, {"code": code}, bob).status_code == 200 and member() == "Robert"

    cli.main(["--database-url", url, "token", "rename", str(alice_id), "--label", "Alice"])
    assert "Renamed" in capsys.readouterr().out
    assert client.get("/api/v1/courses", headers=bob).get_json()["courses"][0]["owner"] == "Alice"
