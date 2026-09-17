"""The local app refuses other hosts and cross-site writes."""

from presence.app import server


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_other_hosts_are_refused(tmp_path):
    c = _client(tmp_path)
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert c.get("/", headers={"Host": "127.0.0.1:8790"}).status_code == 200
    assert c.get("/", headers={"Host": "localhost:8790"}).status_code == 200


def test_cross_site_posts_are_refused_but_own_pages_work(tmp_path):
    c = _client(tmp_path)
    data = {"backend": "sqlite"}
    assert c.post("/setup/tracker", data=data,
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/setup/tracker", data=data,
                  headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/setup/tracker", data=data,
                  headers={"Sec-Fetch-Site": "same-origin",
                           "Origin": "http://127.0.0.1:8790"}).status_code == 302
    assert c.post("/setup/tracker", data=data).status_code == 302  # no such headers: a form post
