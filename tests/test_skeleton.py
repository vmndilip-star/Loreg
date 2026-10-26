import importlib

import pytest

PACKAGES = [
    "app",
    "app.core",
    "app.core.metrics",
    "app.core.classify",
    "app.core.quality",
    "app.llm",
    "app.llm.client",
    "app.agents",
    "app.agents.inventory",
    "app.agents.market",
    "app.api",
    "app.schemas",
    "app.storage",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name):
    importlib.import_module(name)
