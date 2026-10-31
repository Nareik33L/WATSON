from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-fixture-regen",
        action="store_true",
        default=False,
        help="Regenerate fixtures with Playwright Chromium and diff sidecars/truth",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-fixture-regen"):
        return
    skip = pytest.mark.skip(reason="needs Playwright Chromium; pass --run-fixture-regen")
    for item in items:
        if "fixture_regen" in item.keywords:
            item.add_marker(skip)
