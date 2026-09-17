"""Which port the app takes: the asked-for one whenever it can, its own range next."""

import socket

from presence.app import run


def _hold(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_port_free_sees_listeners_but_not_closed_connections():
    port = run.pick_port()
    holder = _hold(port)
    try:
        assert not run.port_free(port)
        client = socket.create_connection(("127.0.0.1", port), timeout=1)
        served, _ = holder.accept()
    finally:
        holder.close()
    served.close()  # the server side closes first, so its port lingers in TIME_WAIT
    client.close()
    assert run.port_free(port), "a port with only closing connections must count as free"


def test_pick_port_prefers_asked_then_its_own_range(monkeypatch):
    busy = {run.PORTS[0]}
    monkeypatch.setattr(run, "port_free", lambda p: p not in busy)
    assert run.pick_port(run.PORTS[0]) == run.PORTS[1]  # not a random port
    assert run.pick_port(run.PORTS[3]) == run.PORTS[3]
    assert run.pick_port() == run.PORTS[1]
    busy = set(run.PORTS)
    monkeypatch.setattr(run, "port_free", lambda p: p not in busy)
    assert run.pick_port(run.PORTS[0]) not in run.PORTS  # everything taken: any free port
