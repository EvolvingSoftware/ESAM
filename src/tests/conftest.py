"""Test configuration for ES Agent Management."""
import os
import sys
import pytest
from pathlib import Path

# Add src to path
SRC = Path(__file__).parent.parent
sys.path.insert(0, str(SRC))


@pytest.fixture
def test_db():
    """Use a temporary in-memory database for tests."""
    import database
    # Override DB path to use :memory:
    original_path = database.DB_PATH
    database.DB_PATH = Path(":memory:")
    database.init_db()
    yield
    database.DB_PATH = original_path
