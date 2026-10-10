def share(client, auth, course):
    return client.put("/api/v1/courses/course-1", json=course, headers=auth).get_json()["share_id"]


def report(client, share_id, kind, element_id="", message="This is wrong", **extra):
    return client.post(f"/api/public/courses/{share_id}/reports",
                       json={"element": {"kind": kind, "id": element_id}, "message": message, **extra})


def test_visitor_reports_each_kind_of_element(client, auth, course):
    sid = share(client, auth, course)
    assert report(client, sid, "question", "q1").status_code == 201
    assert report(client, sid, "card", "c2").status_code == 201
    assert report(client, sid, "section", "1.2", name="Alice").status_code == 201
    reports = client.get("/api/v1/reports", headers=auth).get_json()["reports"]
    assert [r["excerpt"] for r in reports] == ["Which event can block?", "Least privilege?", "§1.2 Least privilege"]
    assert reports[2]["author"] == "Alice" and reports[0]["course_uid"] == "course-1"


def test_sheet_elements(client, auth, course):
    sid = share(client, auth, course)
    assert report(client, sid, "sheet", "definitions.0").status_code == 201
    assert report(client, sid, "sheet", "structure.0").status_code == 201
    assert report(client, sid, "sheet", "pieges.5").status_code == 404
    excerpts = [r["excerpt"] for r in client.get("/api/v1/reports", headers=auth).get_json()["reports"]]
    assert excerpts == ["Guardrail", "Events"]


def test_unknown_element_or_bad_message(client, auth, course):
    sid = share(client, auth, course)
    assert report(client, sid, "question", "nope").status_code == 404
    assert report(client, sid, "teacher").status_code == 400
    assert report(client, sid, "course", message="x").status_code == 400
    assert report(client, "zzzzzzzzzz", "course").status_code == 404


def test_honeypot_is_accepted_silently_but_not_stored(client, auth, course):
    sid = share(client, auth, course)
    assert report(client, sid, "course", website="http://spam").status_code == 201
    assert client.get("/api/v1/reports", headers=auth).get_json()["reports"] == []


def test_rate_limit(client, auth, course):
    sid = share(client, auth, course)
    codes = [report(client, sid, "course").status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


def test_app_resolves_reports(client, auth, course):
    sid = share(client, auth, course)
    rid = report(client, sid, "course").get_json()["id"]
    r = client.patch(f"/api/v1/reports/{rid}", json={"status": "resolved"}, headers=auth)
    assert r.get_json()["status"] == "resolved"
    assert client.get("/api/v1/reports", headers=auth).get_json()["reports"] == []
    assert len(client.get("/api/v1/reports?status=all", headers=auth).get_json()["reports"]) == 1
    assert client.get("/api/v1/courses", headers=auth).get_json()["courses"][0]["open_reports"] == 0
    assert client.delete(f"/api/v1/reports/{rid}", headers=auth).status_code == 204
    assert client.patch(f"/api/v1/reports/{rid}", json={"status": "open"}, headers=auth).status_code == 404


def test_since_id_filter(client, auth, course):
    sid = share(client, auth, course)
    first = report(client, sid, "course").get_json()["id"]
    report(client, sid, "card", "c1")
    rows = client.get(f"/api/v1/reports?since_id={first}", headers=auth).get_json()["reports"]
    assert [r["element"]["kind"] for r in rows] == ["card"]
