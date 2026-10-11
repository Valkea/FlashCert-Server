import json
import re


def test_index_lists_public_courses_grouped(client, auth, course):
    assert "No course has been shared yet." in client.get("/").get_data(as_text=True)
    client.put("/api/v1/courses/a", json=course, headers=auth)
    course["certification"] = None
    course["title"] = "Loose course"
    client.put("/api/v1/courses/b", json=course, headers=auth)
    html = client.get("/").get_data(as_text=True)
    assert html.index("Claude Agent SDK") < html.index("Hooks and guardrails") < html.index("Other courses") < html.index("Loose course")


def test_french_ui(client):
    html = client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).get_data(as_text=True)
    assert '<html lang="fr">' in html and "Cours partagés" in html


def test_course_page_embeds_data_safely(client, auth, course):
    course["notes"] += "\n</script><script>alert(1)</script>\n"
    share_id = client.put("/api/v1/courses/a", json=course, headers=auth).get_json()["share_id"]
    r = client.get(f"/c/{share_id}/any-slug")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "<script>alert(1)" not in html
    blob = re.search(r'<script type="application/json" id="course-data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob)["notes"].endswith("</script><script>alert(1)</script>\n")
    assert '<meta property="og:title" content="Hooks and guardrails">' in html
    assert "Only PreToolUse can prevent an action." in html  # description from the sheet


def test_security_headers(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_unknown_course_is_a_404_page(client):
    r = client.get("/c/abcdefghij")
    assert r.status_code == 404 and "no longer shared" in r.get_data(as_text=True)
    assert client.get("/api/public/courses/abcdefghij").get_json()["error"]


def test_static_assets(client):
    for path in ("style.css", "course.js", "md.js", "progress.js", "i18n.js", "index.js", "icon.svg",
                 "appearance.js", "palettes.css", "logo.svg", "fonts/grotesk_bold.ttf",
                 "vendor/katex/katex.min.js", "vendor/katex/katex.min.css"):
        assert client.get(f"/static/{path}").status_code == 200, path


def test_healthz(client):
    assert client.get("/healthz").get_json()["ok"] is True


def test_share_url_from_proxy_headers(tmp_path):
    from flashcert_share import db
    from flashcert_share.app import create_app
    from flashcert_share.config import Config
    from tests.conftest import COURSE, database_url

    app = create_app(Config(database_url=database_url(tmp_path), proxies=1))
    _, raw = db.create_token(app.extensions["flashcert"]["engine"])

    r = app.test_client().put("/api/v1/courses/a", json=COURSE, headers={
        "Authorization": f"Bearer {raw}", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "learn.example.org",
        "X-Forwarded-Prefix": "/courses"})
    assert r.get_json()["url"].startswith("https://learn.example.org/courses/c/")
