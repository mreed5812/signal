"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture()
def mock_db_connected():
    """Patch DB connectivity so API tests don't need a real database."""
    with patch("src.common.database.check_connectivity", return_value=True):
        yield


@pytest.fixture()
def mock_db_disconnected():
    with patch("src.common.database.check_connectivity", return_value=False):
        yield


@pytest.fixture()
def client(mock_db_connected):
    from src.api.main import app
    with TestClient(app) as c:
        yield c
