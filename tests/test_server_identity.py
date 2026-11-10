"""User/version in the header when the API server has auth enabled."""

from types import SimpleNamespace

from twisk.backend import skypilot
from twisk.backend.base import ServerInfo


def make(monkeypatch, api_info):
    backend = skypilot.SkyPilotBackend("https://sky.example.com")
    # Unauthenticated /api/health on an auth-enabled server: just "healthy".
    monkeypatch.setattr(backend, "_probe", lambda: ServerInfo(backend.endpoint, True))
    calls = []

    def info():
        calls.append(1)
        return api_info()

    fake_sky = SimpleNamespace(client=SimpleNamespace(sdk=SimpleNamespace(api_info=info)))
    monkeypatch.setattr(skypilot, "_sky", lambda: fake_sky)
    return backend, calls


def test_user_and_version_come_from_authenticated_api_info(monkeypatch):
    response = SimpleNamespace(user=SimpleNamespace(name="reda@example.com", id="u1"), version="0.14.2", api_version="27")
    backend, calls = make(monkeypatch, lambda: response)
    info = backend.server_info()
    assert (info.user, info.version, info.api_version) == ("reda@example.com", "0.14.2", "27")
    backend._health = None
    assert backend.server_info().user == "reda@example.com"
    assert len(calls) == 1  # asked once, then cached


def test_dict_response_and_failures_are_harmless(monkeypatch):
    backend, calls = make(monkeypatch, lambda: {"user": {"id": "abc"}, "version": "0.14.2"})
    assert backend.server_info().user == "abc"

    def boom():
        raise RuntimeError("401")

    backend, calls = make(monkeypatch, boom)
    info = backend.server_info()
    assert info.healthy and info.user is None
    backend._health = None
    backend.server_info()
    assert len(calls) == 1  # no retry storm: next attempt only after 30s
