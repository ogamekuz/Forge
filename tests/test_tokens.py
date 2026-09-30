"""Хранение refresh-токенов: save/load/delete через инъектированный backend."""

from __future__ import annotations

from forge.ingest.character import tokens


class FakeBackend:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


def test_save_load_delete_roundtrip():
    be = FakeBackend()
    tokens.save(123, "secret-rt", backend=be)
    assert tokens.load(123, backend=be) == "secret-rt"
    # Записано под service tokens.SERVICE, username = str(id).
    assert be.store[(tokens.SERVICE, "123")] == "secret-rt"
    tokens.delete(123, backend=be)
    assert tokens.load(123, backend=be) is None


def test_delete_missing_is_silent():
    be = FakeBackend()
    tokens.delete(999, backend=be)  # не должно бросать
