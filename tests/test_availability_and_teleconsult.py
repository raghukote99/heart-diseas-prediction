import os
from datetime import datetime, timedelta


def test_availability_excludes_booked_slot(client):
    c = client
    doctor_id = 1
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    # 1) Check availability before booking: slot should be present
    r = c.get(f'/api/doctor_availability/{doctor_id}?date={tomorrow}')
    assert r.status_code == 200
    data = r.get_json() if hasattr(r, 'get_json') else __import__('json').loads(r.data)
    assert 'slots' in data
    assert '10:00 AM' in data['slots']

    # 2) Register a user and create a pending order for that slot
    ts = int(datetime.now().timestamp())
    username = f'vuser_{ts%1000000}'
    password = 'pass1234'
    email = f'{username}@example.com'
    reg = c.post('/register', data={'username': username, 'password': password, 'confirm_password': password, 'email': email}, follow_redirects=True)
    assert reg.status_code in (200, 302)

    payload = {'appointment_date': tomorrow, 'appointment_time': '10:00 AM', 'notes': 'slot test'}
    r2 = c.post(f'/create_order/{doctor_id}', json=payload)
    assert r2.status_code == 200
    order = r2.get_json()
    assert order.get('order_id')

    # 3) Availability should now exclude the booked slot
    r3 = c.get(f'/api/doctor_availability/{doctor_id}?date={tomorrow}')
    assert r3.status_code == 200
    data2 = r3.get_json() if hasattr(r3, 'get_json') else __import__('json').loads(r3.data)
    assert '10:00 AM' not in data2['slots']


def test_teleconsult_link_created_on_verify(client):
    c = client
    doctor_id = 1
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    # Register a user
    ts = int(datetime.now().timestamp())
    username = f'tuser_{ts%1000000}'
    password = 'pass1234'
    email = f'{username}@example.com'
    r = c.post('/register', data={'username': username, 'password': password, 'confirm_password': password, 'email': email}, follow_redirects=True)
    assert r.status_code == 200

    # Create order
    payload = {'appointment_date': tomorrow, 'appointment_time': '10:30 AM', 'notes': 'tele test'}
    r2 = c.post(f'/create_order/{doctor_id}', json=payload)
    assert r2.status_code == 200
    order = r2.get_json()
    assert order.get('order_id')

    # Verify payment (mock)
    payment_payload = {
        'razorpay_payment_id': f'pay_mock_{int(datetime.now().timestamp())}',
        'razorpay_order_id': order.get('order_id'),
        'razorpay_signature': 'mock_signature',
        'appointment_date': tomorrow,
        'appointment_time': '10:30 AM',
        'notes': 'tele test'
    }
    r3 = c.post(f'/verify_payment/{doctor_id}', json=payment_payload)
    assert r3.status_code == 200
    resp = r3.get_json()
    assert resp.get('success') is True

    # Check DB for teleconsult_link
    db_path = os.environ.get('AK_DB', 'heart_predictions.db')
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT teleconsult_link FROM appointments WHERE razorpay_order_id = ?', (order.get('order_id'),))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] and row[0].startswith('https://meet.jit.si/')
