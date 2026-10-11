from pathlib import Path

import pytest

from atlas.config import Settings
from atlas.storage.store import Store


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/atlas.db",
        data_dir=tmp_path / "data",
        repo_root=tmp_path / "repositories",
        sandbox="local",
        allow_trusted_local=True,
    )


@pytest.fixture
def store(settings):
    instance = Store(settings.database_url)
    yield instance
    instance.close()
