import os
import importlib
import pytest

# Set up a test client that uses a temporary SQLite database file.
# We set AK_DB before importing the app so the app initializes the DB at that path.

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_heart_predictions.db"
    monkeypatch.setenv('AK_DB', str(db_path))
    # Ensure Razorpay mock env is present for tests
    monkeypatch.setenv('RAZORPAY_MOCK', '1')
    monkeypatch.setenv('RAZORPAY_KEY_ID', 'rzp_test_placeholder')
    monkeypatch.setenv('RAZORPAY_KEY_SECRET', 'placeholder_secret')

    # Import the app after setting AK_DB so it uses the tmp DB path.
    # If the app was already imported in the same process, reload it to pick up AK_DB.
    if 'app' in importlib.util.sys.modules:
        importlib.reload(importlib.import_module('app'))
        app = importlib.import_module('app').app
        # ensure DB init
        try:
            importlib.import_module('app').init_db()
        except Exception:
            pass
    else:
        app = importlib.import_module('app').app
        try:
            importlib.import_module('app').init_db()
        except Exception:
            pass

    # Enable testing and propagate exceptions for clearer tracebacks
    app.testing = True
    app.config['PROPAGATE_EXCEPTIONS'] = True

    # Provide a test client
    with app.test_client() as client:
        yield client
