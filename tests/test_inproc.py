import os
from datetime import datetime, timedelta
import sqlite3

# Use AK_DB if provided by the test fixture (conftest), otherwise default
DB = os.environ.get('AK_DB', 'heart_predictions.db')


def query_appointment_by_order(order_id):
    db_path = os.environ.get('AK_DB', 'heart_predictions.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT id, status, razorpay_payment_id, payment_status FROM appointments WHERE razorpay_order_id = ?', (order_id,))
    row = cur.fetchone()
    conn.close()
    return row


def test_create_and_verify_flow(client):
    c = client
    ts = int(datetime.now().timestamp())
    username = f'test_{ts%1000000}'
    password = 'testpass123'
    email = f'test_{ts%1000000}@example.com'

    # Register
    r = c.post('/register', data={'username': username, 'password': password, 'confirm_password': password, 'email': email}, follow_redirects=True)
    assert r.status_code == 200

    # Create order
    doctor_id = 1
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    payload = {'appointment_date': tomorrow, 'appointment_time': '10:00 AM', 'notes': 'pytest booking'}
    r = c.post(f'/create_order/{doctor_id}', json=payload)
    assert r.status_code == 200
    order = r.get_json()
    assert order and order.get('order_id')

    # Verify payment
    payment_payload = {
        'razorpay_payment_id': f'pay_mock_{int(datetime.now().timestamp())}',
        'razorpay_order_id': order.get('order_id'),
        'razorpay_signature': 'mock_signature',
        'appointment_date': tomorrow,
        'appointment_time': '10:00 AM',
        'notes': 'pytest booking'
    }
    r2 = c.post(f'/verify_payment/{doctor_id}', json=payment_payload)
    assert r2.status_code == 200
    assert r2.get_json().get('success') is True

    # DB check
    apt = query_appointment_by_order(order.get('order_id'))
    assert apt is not None
    assert apt[1] in ('scheduled',)
