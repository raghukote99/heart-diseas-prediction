import os
from datetime import datetime, timedelta
import sqlite3


def test_doctor_accept_and_reschedule(client):
    c = client
    doctor_id = 1
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    # Register patient and create a pending appointment via create_order
    ts = int(datetime.now().timestamp())
    username = f'pa_{ts%1000000}'
    password = 'pass1234'
    email = f'{username}@example.com'
    r = c.post('/register', data={'username': username, 'password': password, 'confirm_password': password, 'email': email}, follow_redirects=True)
    assert r.status_code in (200, 302)

    payload = {'appointment_date': tomorrow, 'appointment_time': '11:00 AM', 'notes': 'accept test'}
    r2 = c.post(f'/create_order/{doctor_id}', json=payload)
    assert r2.status_code == 200
    order = r2.get_json()
    apt_id = order.get('appointment_id')
    assert apt_id is not None

    # Now create a doctor user and link to doctor record
    # Logout current patient
    c.get('/logout')
    d_ts = ts + 1
    d_un = f'doc_{d_ts%1000000}'
    d_pw = 'docpass'
    d_email = f'{d_un}@example.com'
    rd = c.post('/register', data={'username': d_un, 'password': d_pw, 'confirm_password': d_pw, 'email': d_email}, follow_redirects=True)
    assert rd.status_code in (200, 302)

    # Link doctor user to doctor row and set role to 'doctor'
    db_path = os.environ.get('AK_DB', 'heart_predictions.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # find doctor user id
    cur.execute('SELECT id FROM users WHERE username = ?', (d_un,))
    d_user_id = cur.fetchone()[0]
    cur.execute('UPDATE users SET role = ? WHERE id = ?', ('doctor', d_user_id))
    # associate doctors.id=doctor_id to this user
    cur.execute('UPDATE doctors SET doctor_user_id = ? WHERE id = ?', (d_user_id, doctor_id))
    conn.commit()
    conn.close()

    # Accept appointment as doctor
    r3 = c.post(f'/doctor/accept_appointment/{apt_id}', follow_redirects=True)
    assert r3.status_code in (200, 302)

    # Check DB: status scheduled and teleconsult_link present
    db_path = os.environ.get('AK_DB', 'heart_predictions.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT status, teleconsult_link FROM appointments WHERE id = ?', (apt_id,))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'scheduled'
    assert row[1] and row[1].startswith('https://meet.jit.si/')

    # Reschedule to day after tomorrow
    new_date = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    new_time = '3:00 PM'
    r4 = c.post(f'/doctor/reschedule_appointment/{apt_id}', data={'new_date': new_date, 'new_time': new_time}, follow_redirects=True)
    assert r4.status_code in (200, 302)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT appointment_date, appointment_time, teleconsult_link FROM appointments WHERE id = ?', (apt_id,))
    row2 = cur.fetchone()
    conn.close()
    assert row2 is not None
    assert row2[0] == new_date
    assert row2[1] == new_time
    assert row2[2] and row2[2].startswith('https://meet.jit.si/')
