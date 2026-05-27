Development & Test Commands (Windows PowerShell)

Prerequisites
- Python 3.10+ (recommended)
- A virtual environment in `.venv` (optional but recommended)

Create & activate virtualenv
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the Flask app in mock Razorpay mode (for local dev)
```powershell
# Enable Razorpay mock mode so no external payment calls are made
$env:RAZORPAY_MOCK = '1'
$env:RAZORPAY_KEY_ID = 'rzp_test_placeholder'
$env:RAZORPAY_KEY_SECRET = 'placeholder_secret'
python app.py
```

Run pytest (in-process, isolated DB per test)
```powershell
# Activate venv if not already active
.\.venv\Scripts\Activate.ps1
pytest -q
```

Run a specific test file
```powershell
pytest tests/test_inproc.py -q
```

Notes
- Tests use a temporary SQLite DB per-test (via the `AK_DB` environment variable) so your local `heart_predictions.db` won't be modified.
- To run integration tests against an actual running server, start the app as above and run the older scripts in `.` (if present). These scripts expect a running server on `http://localhost:5000`.
