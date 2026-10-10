import json
import threading
import urllib.request

from flashcert_share import db
from flashcert_share.cli import make_server
from flashcert_share.config import Config
from tests.conftest import COURSE, database_url


def run(cfg):
    app, server = make_server(cfg, "127.0.0.1", 0, threads=2)
    threading.Thread(target=server.run, daemon=True).start()
    _, raw = db.create_token(app.extensions["flashcert"]["engine"])
    return server, raw


def put(server, raw, headers):
    port = server.effective_port
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/courses/a", data=json.dumps(COURSE).encode(),
                                 method="PUT", headers={"Authorization": f"Bearer {raw}",
                                                        "Content-Type": "application/json", **headers})
    return json.load(urllib.request.urlopen(req))


PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "courses.example.com"}


def test_waitress_passes_proxy_headers_to_the_app(tmp_path):
    server, raw = run(Config(database_url=database_url(tmp_path), proxies=1))
    try:
        assert put(server, raw, PROXY)["url"].startswith("https://courses.example.com/c/")
    finally:
        server.close()


def test_proxy_headers_ignored_when_no_proxy_is_trusted(tmp_path):
    server, raw = run(Config(database_url=database_url(tmp_path), proxies=0))
    try:
        assert put(server, raw, PROXY)["url"].startswith("http://127.0.0.1:")
    finally:
        server.close()
