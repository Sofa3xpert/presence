"""The default data folder per platform, and the override."""

from pathlib import Path

from presence.core.paths import default_data_dir, resolve_data_dir


def test_default_locations():
    env = {"HOME": "/Users/ada"}
    mac = Path("/Users/ada/Library/Application Support/Presence")
    assert default_data_dir("darwin", env) == mac
    assert default_data_dir("linux", env) == Path("/Users/ada/.local/share/presence")
    assert default_data_dir("linux", {**env, "XDG_DATA_HOME": "/x"}) == Path("/x/presence")
    assert default_data_dir("win32", {"APPDATA": "C:/Users/ada/AppData/Roaming"}) == Path(
        "C:/Users/ada/AppData/Roaming/Presence")
    assert default_data_dir("darwin", {"PRESENCE_DATA": "/tmp/p"}) == Path("/tmp/p")


def test_resolve_prefers_explicit(tmp_path):
    assert resolve_data_dir(tmp_path) == tmp_path
    assert resolve_data_dir(None).name.lower() == "presence"
