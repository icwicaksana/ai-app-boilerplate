"""Unit-test bootstrap: Windows WAJIB SelectorEventLoop untuk psycopg.

`ensure_selector_loop()` dihapus dari import-time (core #1), sehingga policy
harus dipasang eksplisit per proses test. Integration/conftest memasang
miliknya sendiri; file ini menutup unit test + TestClient.
"""

import asyncio
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _selector_event_loop_policy():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    yield
