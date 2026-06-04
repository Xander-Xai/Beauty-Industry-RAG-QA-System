import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

@pytest.fixture(scope="session")
def deployment_mode():
    return os.environ.get("DEPLOYMENT_MODE", "development")

@pytest.fixture(scope="session")
def is_integration_ready(deployment_mode):
    return deployment_mode in ("development", "testing", "production")

@pytest.fixture
def sample_queries():
    import json
    testdata_path = os.path.join(os.path.dirname(__file__), "testdata", "queries.json")
    with open(testdata_path) as f:
        return json.load(f)
