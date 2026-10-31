from __future__ import annotations

from collections.abc import Iterator

import pytest

from watson_bench.paths import set_repo_root


@pytest.fixture(autouse=True)
def _reset_repo_root() -> Iterator[None]:
    yield
    set_repo_root(None)
