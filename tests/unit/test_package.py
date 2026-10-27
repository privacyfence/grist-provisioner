import importlib
import importlib.metadata

import grist_provisioner


def test_version_is_a_string() -> None:
    assert isinstance(grist_provisioner.__version__, str)


def test_version_falls_back_when_not_installed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        reloaded = importlib.reload(grist_provisioner)
        assert reloaded.__version__ == "0.0.0.dev0"
    finally:
        monkeypatch.undo()
        importlib.reload(grist_provisioner)
