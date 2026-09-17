"""The job-boards step: adding, switching and removing a board keeps the person on that step."""

from presence.app import server
from presence.app.config_io import read_yaml, write_yaml


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_board_changes_land_back_on_the_boards_step(tmp_path):
    c = _client(tmp_path)
    assert 'id="boards"' in c.get("/").data.decode()
    r = c.post("/sources/add", data={"url": "https://boards.greenhouse.io/acme"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/#boards")
    src = read_yaml(tmp_path / "sources.yaml")["sources"]
    assert src == [{"id": "acme", "provider": "greenhouse", "label": "Acme", "enabled": True,
                    "config": {"board": "acme"}}]
    r = c.post("/sources/acme/toggle")
    assert r.headers["Location"].endswith("/#boards")
    assert read_yaml(tmp_path / "sources.yaml")["sources"][0]["enabled"] is False
    r = c.post("/sources/add", data={"url": "https://example.com/careers"})
    assert r.headers["Location"].endswith("/#boards")  # a refusal stays on the step too
    r = c.post("/sources/acme/remove")
    assert r.headers["Location"].endswith("/#boards")
    assert read_yaml(tmp_path / "sources.yaml")["sources"] == []
    write_yaml(tmp_path / "search.yaml", {})
    r = c.post("/setup/filters", data={"locations": "London", "freshness_days": "7"})
    assert r.headers["Location"].endswith("/#boards")
