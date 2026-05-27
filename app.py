from flask import (
    Flask,
    render_template,
    request,
    g,
    redirect,
    url_for,
    session,
    flash,
    send_file,
)
import pickle
import numpy as np
import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ----------------- CONFIG -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_FILENAME = os.path.join(BASE_DIR, "heart-disease-prediction-knn-model.pkl")
LEGACY_DATABASE = os.path.join(BASE_DIR, "heart_predictions.db")
FALLBACK_DATABASE = os.path.join(BASE_DIR, "data", "heart_predictions.db")


def _default_database_path():
    if not os.path.exists(LEGACY_DATABASE):
        return LEGACY_DATABASE
    if os.access(LEGACY_DATABASE, os.W_OK):
        return LEGACY_DATABASE
    return FALLBACK_DATABASE


# Allow tests and hosts to override DB path via AK_DB env var
DATABASE = os.getenv("AK_DB", _default_database_path())
DB_DIR = os.path.dirname(DATABASE)
if DB_DIR:
    os.makedirs(DB_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv(
    "SECRET_KEY", "change_this_to_a_random_secret_key"
)  # set SECRET_KEY in production


# ----------------- MODEL LOADING -----------------
with open(MODEL_FILENAME, "rb") as f:
    model = pickle.load(f)


# ----------------- DB HELPERS -----------------
def get_db():
    """Get a connection for the current request."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db


def init_db():
    """Create tables if they don't exist."""
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()

        # predictions table – stores each user input + prediction
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                age INTEGER,
                sex INTEGER,
                cp INTEGER,
                trestbps INTEGER,
                chol INTEGER,
                fbs INTEGER,
                restecg INTEGER,
                thalach INTEGER,
                exang INTEGER,
                oldpeak REAL,
                slope INTEGER,
                ca INTEGER,
                thal INTEGER,
                prediction INTEGER,
                proba REAL
            )
            """
        )

        # users table – simple auth with a role column
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'patient',
                created_at TEXT
            )
            """
        )

        # doctors table – optional profile entries for doctors (can be linked to a user)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                specialization TEXT,
                city TEXT,
                state TEXT,
                phone TEXT,
                email TEXT,
                user_id INTEGER,
                doctor_user_id INTEGER,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(doctor_user_id) REFERENCES users(id)
            )
            """
        )

        # appointments table – store bookings and teleconsult links
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_id INTEGER NOT NULL,
                patient_user_id INTEGER,
                patient_name TEXT,
                patient_email TEXT,
                appointment_date TEXT,
                appointment_time TEXT,
                scheduled_at TEXT,
                status TEXT DEFAULT 'pending',
                teleconsult_link TEXT,
                created_at TEXT,
                razorpay_order_id TEXT,
                razorpay_payment_id TEXT,
                razorpay_signature TEXT,
                payment_status TEXT,
                FOREIGN KEY(doctor_id) REFERENCES doctors(id),
                FOREIGN KEY(patient_user_id) REFERENCES users(id)
            )
            """
        )

        conn.commit()


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


# create tables on startup
init_db()

# Seed a handful of Karnataka cardiologists if doctors table is empty
def init_sample_doctors():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM doctors")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        sample = [
            ("Dr. Ramesh Kumar", "Cardiologist", "Bengaluru", "Karnataka", "", "", datetime.now().isoformat(timespec="seconds")),
            ("Dr. S. Narayan", "Interventional Cardiologist", "Mysore", "Karnataka", "", "", datetime.now().isoformat(timespec="seconds")),
            ("Dr. Meera Rao", "Pediatric Cardiologist", "Hubli", "Karnataka", "", "", datetime.now().isoformat(timespec="seconds")),
        ]
        cur.executemany(
            "INSERT INTO doctors (name, specialization, city, state, phone, email, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            sample,
        )
        conn.commit()
    conn.close()

init_sample_doctors()


# ----------------- AUTH DECORATOR -----------------
def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please login or create an account to continue.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


# ----------------- ROUTES -----------------
@app.route("/")
def home():
    # Landing page (AK Health Prediction)
    return render_template("index.html")


@app.route("/predict_form")
@login_required
def predict_form():
    # Prediction form page
    return render_template("main.html", show_all_bookings_link=True)


# ---------- PREDICTION + SAVE TO DB + STORE IN SESSION ----------
@app.route("/predict", methods=["POST"])
@login_required
def predict():
    try:
        # 1. Read user input from form
        age = int(request.form["age"])
        sex = int(request.form["sex"])
        cp = int(request.form["cp"])
        trestbps = int(request.form["trestbps"])
        chol = int(request.form["chol"])
        fbs = int(request.form["fbs"])
        restecg = int(request.form["restecg"])
        thalach = int(request.form["thalach"])
        exang = int(request.form["exang"])
        oldpeak = float(request.form["oldpeak"])
        slope = int(request.form["slope"])
        ca = int(request.form["ca"])
        thal = int(request.form["thal"])

        features = np.array(
            [
                [
                    age,
                    sex,
                    cp,
                    trestbps,
                    chol,
                    fbs,
                    restecg,
                    thalach,
                    exang,
                    oldpeak,
                    slope,
                    ca,
                    thal,
                ]
            ]
        )

        # 2. Model prediction
        prediction = int(model.predict(features)[0])

        proba = None
        if hasattr(model, "predict_proba"):
            proba = float(model.predict_proba(features)[0][1])  # probability of class 1

        created_at = datetime.now().isoformat(timespec="seconds")

        # 3. Save everything to DB (for logs) – best effort
        db_id = None
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO predictions (
                    created_at, age, sex, cp, trestbps, chol, fbs,
                    restecg, thalach, exang, oldpeak, slope, ca, thal,
                    prediction, proba
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    age,
                    sex,
                    cp,
                    trestbps,
                    chol,
                    fbs,
                    restecg,
                    thalach,
                    exang,
                    oldpeak,
                    slope,
                    ca,
                    thal,
                    prediction,
                    proba,
                ),
            )
            conn.commit()
            db_id = cur.lastrowid
        except Exception as db_err:
            # Don't crash the app; just log it
            print("DB error during insert:", db_err)

        # 4. Store latest prediction in session (for PDF generation)
        session["last_prediction"] = {
            "id": db_id,
            "created_at": created_at,
            "age": age,
            "sex": sex,
            "cp": cp,
            "trestbps": trestbps,
            "chol": chol,
            "fbs": fbs,
            "restecg": restecg,
            "thalach": thalach,
            "exang": exang,
            "oldpeak": oldpeak,
            "slope": slope,
            "ca": ca,
            "thal": thal,
            "prediction": prediction,
            "proba": proba,
        }

        # 5. Render result page
        return render_template(
            "result.html",
            prediction=prediction,
            proba=proba,
            error=None,
        )

    except Exception as e:
        return render_template(
            "result.html",
            prediction=None,
            proba=None,
            error=str(e),
        )


# ---------- BUILD PDF FROM SESSION DATA (NOW WITH SYMPTOMS & CAUSES) ----------
def build_pdf_from_data(data, username=None):
    """
    data is the dict stored in session["last_prediction"].
    """
    pid = data.get("id") or "N/A"
    created_at = data.get("created_at", "N/A")
    age = data["age"]
    sex = data["sex"]
    cp = data["cp"]
    trestbps = data["trestbps"]
    chol = data["chol"]
    fbs = data["fbs"]
    restecg = data["restecg"]
    thalach = data["thalach"]
    exang = data["exang"]
    oldpeak = data["oldpeak"]
    slope = data["slope"]
    ca = data["ca"]
    thal = data["thal"]
    prediction = data["prediction"]
    proba = data["proba"]

    # human readable mappings
    sex_map = {0: "Female", 1: "Male"}
    cp_map = {
        0: "Typical Angina",
        1: "Atypical Angina",
        2: "Non-anginal Pain",
        3: "Asymptomatic",
    }
    fbs_map = {0: "≤ 120 mg/dL", 1: "> 120 mg/dL"}
    restecg_map = {
        0: "Normal",
        1: "ST-T Wave Abnormality",
        2: "Left Ventricular Hypertrophy",
    }
    exang_map = {0: "No", 1: "Yes"}
    slope_map = {0: "Upsloping", 1: "Flat", 2: "Downsloping"}
    thal_map = {1: "Normal", 2: "Fixed Defect", 3: "Reversible Defect"}

    result_text = (
        "Heart Disease Detected" if prediction == 1 else "No Heart Disease Detected"
    )
    confidence_text = f"{proba * 100:.2f}%" if proba is not None else "N/A"

    pdf_buffer = io.BytesIO()
    p = canvas.Canvas(pdf_buffer, pagesize=letter)
    width, height = letter

    y = height - 50
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Heart Disease Prediction Report")

    y -= 25
    p.setFont("Helvetica", 10)
    p.drawString(50, y, f"Report ID: {pid}")
    y -= 15
    p.drawString(50, y, f"Generated At: {created_at}")

    # include username when available
    if username:
        y -= 15
        p.drawString(50, y, f"User: {username}")

    y -= 25
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "User Inputs")
    p.setFont("Helvetica", 10)
    y -= 15

    def ensure_space():
        nonlocal y
        if y < 80:
            p.showPage()
            y = height - 50
            p.setFont("Helvetica", 10)

    def line(label, value):
        nonlocal y
        ensure_space()
        p.drawString(60, y, f"{label}: {value}")
        y -= 15

    def text_line(text):
        nonlocal y
        ensure_space()
        p.drawString(60, y, text)
        y -= 15

    # ------------- USER INPUTS -------------
    line("Age", age)
    line("Sex", sex_map.get(sex, sex))
    line("Chest Pain Type (cp)", cp_map.get(cp, cp))
    line("Resting BP (trestbps)", f"{trestbps} mmHg")
    line("Cholesterol (chol)", f"{chol} mg/dL")
    line("Fasting Blood Sugar (fbs)", fbs_map.get(fbs, fbs))
    line("Resting ECG (restecg)", restecg_map.get(restecg, restecg))
    line("Maximum Heart Rate (thalach)", f"{thalach} bpm")
    line("Exercise-Induced Angina (exang)", exang_map.get(exang, exang))
    line("ST Depression (oldpeak)", oldpeak)
    line("Slope of ST Segment", slope_map.get(slope, slope))
    line("Number of Major Vessels (ca)", ca)
    line("Thalassemia (thal)", thal_map.get(thal, thal))

    # ------------- PREDICTION RESULT -------------
    y -= 10
    ensure_space()
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Prediction Result")
    y -= 20
    p.setFont("Helvetica", 10)
    line("Prediction", result_text)
    line("Model Confidence", confidence_text)

    # ------------- SYMPTOMS SECTION -------------
    y -= 10
    ensure_space()
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Heart Disease – Common Symptoms")
    y -= 18
    p.setFont("Helvetica", 10)

    text_line("• Chest pain, pressure, or discomfort (especially with activity)")
    text_line("• Shortness of breath or difficulty breathing")
    text_line("• Unusual fatigue or weakness")
    text_line("• Pain in arms, neck, jaw, back, or stomach")
    text_line("• Palpitations (feeling of rapid or irregular heartbeat)")
    text_line("• Dizziness, light-headedness, or fainting")
    text_line("• Swelling of legs, ankles, or feet")

    # ------------- CAUSES / RISK FACTORS SECTION -------------
    y -= 10
    ensure_space()
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Common Causes / Risk Factors")
    y -= 18
    p.setFont("Helvetica", 10)

    text_line("• High blood pressure (hypertension)")
    text_line("• High cholesterol levels")
    text_line("• Diabetes or high blood sugar")
    text_line("• Smoking or tobacco use")
    text_line("• Obesity and lack of regular physical activity")
    text_line("• Unhealthy diet (high in salt, sugar, saturated fat)")
    text_line("• Family history of heart disease")
    text_line("• Increasing age and long-term stress")

    # ------------- DISCLAIMER -------------
    y -= 10
    ensure_space()
    p.setFont("Helvetica-Oblique", 9)
    p.drawString(
        50,
        y,
        "Disclaimer: This report is generated by a machine learning model and is not a medical diagnosis.",
    )
    y -= 12
    ensure_space()
    p.drawString(
        50,
        y,
        "If you have concerning symptoms, please consult a qualified healthcare professional immediately.",
    )

    p.showPage()
    p.save()
    pdf_buffer.seek(0)
    filename = f"heart_prediction_report_{created_at.replace(':', '-')}.pdf"
    return pdf_buffer, filename


# ---------- PDF DOWNLOAD FROM SESSION ----------
@app.route("/download_pdf")
@login_required
def download_pdf():
    data = session.get("last_prediction")
    if not data:
        flash(
            "No recent prediction found to generate PDF. Please run a new prediction.",
            "warning",
        )
        return redirect(url_for("predict_form"))

    # pass current logged-in username (if any) into PDF
    username = session.get("admin_user") or session.get("username")
    pdf_buffer, filename = build_pdf_from_data(data, username=username)
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


# ----------------- REGISTER / LOGIN / LOGOUT -----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "warning")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if len(password) < 4:
            flash("Password must be at least 4 characters long.", "warning")
            return render_template("register.html")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing = cur.fetchone()
        if existing:
            flash("Username already taken. Choose another.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        cur.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

        # set session user id and role (default patient)
        cur.execute("SELECT id, role FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        session["logged_in"] = True
        session["username"] = username
        session["user_id"] = user[0]
        session["role"] = user[1] or "patient"
        flash("Registration successful. You are now logged in.", "success")
        return redirect(url_for("predict_form"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        )
        user = cur.fetchone()

        if user and check_password_hash(user[1], password):
            # fetch role
            cur.execute("SELECT id, role FROM users WHERE username = ?", (username,))
            full = cur.fetchone()
            session["logged_in"] = True
            session["username"] = username
            session["user_id"] = full[0]
            session["role"] = full[1] or "patient"
            flash("Logged in successfully.", "success")
            return redirect(url_for("predict_form"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# ----------------- ADMIN LOGS (OPTIONAL) -----------------
@app.route("/logs")
@login_required
def logs():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_at, age, sex, cp, trestbps, chol, fbs,
               restecg, thalach, exang, oldpeak, slope, ca, thal,
               prediction, proba
        FROM predictions
        ORDER BY created_at DESC
        """
    )
    rows = cur.fetchall()
    return render_template("logs.html", rows=rows)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )


# ----------------- ROLE HELPERS & DOCTOR ROUTES -----------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return {"id": None, "username": None, "role": None}
    # fetch latest role/username from DB to reflect any direct DB changes during tests
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT username, role FROM users WHERE id = ?", (uid,))
        row = cur.fetchone()
        if row:
            return {"id": uid, "username": row[0], "role": row[1] or "patient"}
    except Exception:
        pass
    return {"id": uid, "username": session.get("username"), "role": session.get("role")}

def doctor_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u.get("id"):
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        if u.get("role") not in ("doctor", "admin"):
            flash("You do not have permission to perform this action.", "danger")
            return redirect(url_for("home"))
        return view_func(*args, **kwargs)

    return wrapped


@app.route("/start_call/<int:appointment_id>")
@login_required
def start_call(appointment_id):
    # only patient or the doctor assigned (or admin) can access
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doctor_id, patient_user_id, teleconsult_link FROM appointments WHERE id = ?", (appointment_id,))
    row = cur.fetchone()
    if not row:
        flash("Appointment not found.", "danger")
        return redirect(url_for("home"))
    doctor_id, patient_user_id, teleconsult_link = row
    u = current_user()
    # allow if admin, or patient owner, or doctor linked to doctor record
    if u.get("role") == "admin" or u.get("id") == patient_user_id:
        return render_template("start_call.html", teleconsult_link=teleconsult_link)

    # check if current user is the doctor linked to this doctor record
    if u.get("role") == "doctor":
        cur.execute("SELECT user_id FROM doctors WHERE id = ?", (doctor_id,))
        d = cur.fetchone()
        if d and d[0] == u.get("id"):
            return render_template("start_call.html", teleconsult_link=teleconsult_link)

    flash("You are not authorized to join this call.", "danger")
    return redirect(url_for("home"))


@app.route('/doctors')
def doctors_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, name, specialization, city, state FROM doctors ORDER BY name')
    docs = cur.fetchall()
    return render_template('doctors.html', doctors=docs)


@app.route('/api/doctor_availability/<int:doctor_id>')
def api_doctor_availability(doctor_id):
    # returns a simple list of time slots for requested date (mocked)
    date = request.args.get('date')
    slots = [
        '09:00 AM',
        '10:00 AM',
        '11:00 AM',
        '02:00 PM',
        '03:00 PM',
    ]
    # exclude already-booked slots for this doctor on the given date
    if date:
        conn = get_db()
        cur = conn.cursor()
        # scheduled_at stored as 'YYYY-MM-DD TIME'
        cur.execute(
            "SELECT scheduled_at FROM appointments WHERE doctor_id = ? AND status IN ('pending','scheduled','rescheduled')",
            (doctor_id,)
        )
        rows = cur.fetchall()
        booked_times = set()
        for (scheduled_at,) in rows:
            if not scheduled_at:
                continue
            if scheduled_at.startswith(date):
                # extract time portion after first space
                parts = scheduled_at.split(' ', 1)
                if len(parts) > 1:
                    booked_times.add(parts[1])
        slots = [s for s in slots if s not in booked_times]
    return { 'date': date, 'slots': slots }


@app.route('/create_order/<int:doctor_id>', methods=['POST'])
@login_required
def create_order(doctor_id):
    data = request.get_json() or {}
    appointment_date = data.get('appointment_date')
    appointment_time = data.get('appointment_time')
    notes = data.get('notes')

    # create a pending appointment record and return a mock order id
    order_id = f"order_mock_{int(datetime.now().timestamp())}"
    conn = get_db()
    cur = conn.cursor()
    scheduled_at = f"{appointment_date} {appointment_time}" if appointment_date and appointment_time else None
    cur.execute(
        "INSERT INTO appointments (doctor_id, patient_user_id, patient_name, patient_email, appointment_date, appointment_time, scheduled_at, status, created_at, razorpay_order_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doctor_id,
            session.get('user_id'),
            session.get('username'),
            None,
            appointment_date,
            appointment_time,
            scheduled_at,
            'pending',
            datetime.now().isoformat(timespec='seconds'),
            order_id,
        ),
    )
    conn.commit()
    apt_id = cur.lastrowid
    return { 'order_id': order_id, 'appointment_id': apt_id }


@app.route('/verify_payment/<int:doctor_id>', methods=['POST'])
@login_required
def verify_payment(doctor_id):
    data = request.get_json() or {}
    pay_id = data.get('razorpay_payment_id')
    order_id = data.get('razorpay_order_id')
    signature = data.get('razorpay_signature')

    conn = get_db()
    cur = conn.cursor()
    # find the appointment by order id
    cur.execute('SELECT id FROM appointments WHERE razorpay_order_id = ?', (order_id,))
    row = cur.fetchone()
    if not row:
        return { 'success': False, 'error': 'order not found' }, 404
    apt_id = row[0]
    tele = _generate_jitsi_link(apt_id)
    # update appointment details and mark as scheduled
    cur.execute(
        'UPDATE appointments SET status = ?, razorpay_payment_id = ?, razorpay_signature = ?, payment_status = ?, teleconsult_link = ?, appointment_date = ?, appointment_time = ?, scheduled_at = ? WHERE id = ?',
        ('scheduled', pay_id, signature, 'paid', tele, data.get('appointment_date'), data.get('appointment_time'), f"{data.get('appointment_date')} {data.get('appointment_time')}", apt_id),
    )
    conn.commit()
    return { 'success': True }


@app.route('/doctor-bookings/<int:doctor_id>')
def doctor_bookings(doctor_id):
    conn = get_db()
    cur = conn.cursor()
    # fetch doctor record
    cur.execute('SELECT id, name, specialization, city, state, phone, email, created_at FROM doctors WHERE id = ?', (doctor_id,))
    doctor = cur.fetchone()

    # fetch appointments and transform into dicts
    cur.execute('SELECT id, patient_name, patient_email, appointment_date, appointment_time, scheduled_at, status, teleconsult_link, created_at FROM appointments WHERE doctor_id = ? ORDER BY created_at DESC', (doctor_id,))
    rows = cur.fetchall()
    apts = []
    for r in rows:
        aid, pname, pemail, adate, atime, scheduled_at, status, tele, created_at = r
        date_val = adate or (scheduled_at.split(' ',1)[0] if scheduled_at else '')
        time_val = atime or (scheduled_at.split(' ',1)[1] if scheduled_at and ' ' in scheduled_at else '')
        apts.append({
            'id': aid,
            'patient_name': pname or 'Anonymous',
            'patient_email': pemail,
            'date': date_val,
            'time': time_val,
            'status': status,
            'teleconsult_link': tele,
            'created_at': created_at,
            'notes': None,
        })

    # categorize
    upcoming = [a for a in apts if a['status'] in ('pending','scheduled','rescheduled')]
    completed = [a for a in apts if a['status'] == 'completed']
    cancelled = [a for a in apts if a['status'] == 'cancelled']
    total_bookings = len(apts)

    return render_template('doctor_bookings.html', appointments=apts, doctor=doctor, upcoming=upcoming, completed=completed, cancelled=cancelled, total_bookings=total_bookings)


@app.route('/doctor/<int:doctor_id>')
def doctor_detail(doctor_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, name, specialization, city, state, phone, email FROM doctors WHERE id = ?', (doctor_id,))
    d = cur.fetchone()
    if not d:
        flash('Doctor not found.', 'warning')
        return redirect(url_for('doctors_list'))
    # build a richer doctor object expected by the template
    doctor_obj = {
        'id': d[0],
        'name': d[1],
        'specialization': d[2] or 'General Physician',
        'city': d[3] if len(d) > 3 else '',
        'state': d[4] if len(d) > 4 else '',
        'phone': d[5] if len(d) > 5 else '',
        'email': d[6] if len(d) > 6 else '',
        'image_url': '',
        'rating': 4.8,
        'total_reviews': 120,
        'experience_years': 12,
        'consultation_fee': 500,
        'availability': 'Mon-Fri 09:00 AM - 05:00 PM',
        'bio': f"{d[1]} is an experienced {d[2]} practicing in {d[3] if d[3] else 'the region' }.",
        'qualifications': 'MBBS, MD (Cardiology)'
    }
    slots = ['09:00 AM','10:00 AM','11:00 AM','02:00 PM']
    reviews = []
    return render_template('doctor_detail.html', doctor=doctor_obj, slots=slots, reviews=reviews)


@app.route('/all-doctor-appointments')
def all_doctor_appointments():
    # admin-facing view; for now render a simple page listing counts
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, doctor_id, patient_name, appointment_date, appointment_time, scheduled_at, status FROM appointments ORDER BY created_at DESC')
    rows = cur.fetchall()
    return render_template('all_doctor_appointments.html', rows=rows)


@app.route('/book_appointment/<int:doctor_id>', methods=['POST'])
@login_required
def book_appointment(doctor_id):
    appointment_date = request.form.get('appointment_date')
    appointment_time = request.form.get('appointment_time')
    notes = request.form.get('notes')
    scheduled_at = f"{appointment_date} {appointment_time}" if appointment_date and appointment_time else None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO appointments (doctor_id, patient_user_id, patient_name, patient_email, appointment_date, appointment_time, scheduled_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doctor_id,
            session.get('user_id'),
            session.get('username'),
            None,
            appointment_date,
            appointment_time,
            scheduled_at,
            'scheduled',
            datetime.now().isoformat(timespec='seconds'),
        ),
    )
    conn.commit()
    return redirect(url_for('my_appointments'))


@app.route('/my_appointments')
@login_required
def my_appointments():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.id, a.doctor_id, a.patient_name, a.appointment_date, a.appointment_time, a.scheduled_at, a.status, a.teleconsult_link, a.created_at,
               d.name, d.specialization
        FROM appointments a
        LEFT JOIN doctors d ON d.id = a.doctor_id
        WHERE a.patient_user_id = ?
        ORDER BY a.created_at DESC
    ''', (session.get('user_id'),))
    rows = cur.fetchall()
    appts = []
    for r in rows:
        aid, did, pname, adate, atime, scheduled_at, status, tele, created_at, dname, dspec = r
        date_val = adate or (scheduled_at.split(' ',1)[0] if scheduled_at else '')
        time_val = atime or (scheduled_at.split(' ',1)[1] if scheduled_at and ' ' in scheduled_at else '')
        appts.append({
            'id': aid,
            'doctor_id': did,
            'doctor_name': dname or 'Doctor',
            'specialization': dspec or '',
            'date': date_val,
            'time': time_val,
            'status': status,
            'teleconsult_link': tele,
            'notes': None,
            'fee': '',
            'created_at': created_at,
        })
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('my_appointments.html', appointments=appts, today=today)


@app.route('/api/book_appointment/<int:doctor_id>', methods=['POST'])
@login_required
def api_book_appointment(doctor_id):
    data = request.get_json() or {}
    appointment_date = data.get('appointment_date')
    appointment_time = data.get('appointment_time')
    scheduled_at = f"{appointment_date} {appointment_time}" if appointment_date and appointment_time else None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO appointments (doctor_id, patient_user_id, patient_name, patient_email, appointment_date, appointment_time, scheduled_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doctor_id,
            session.get('user_id'),
            session.get('username'),
            None,
            appointment_date,
            appointment_time,
            scheduled_at,
            'scheduled',
            datetime.now().isoformat(timespec='seconds'),
        ),
    )
    conn.commit()
    return { 'success': True, 'appointment_id': cur.lastrowid }


def _generate_jitsi_link(appointment_id):
    # deterministic-ish room name for privacy + easy sharing
    room = f"ak-health-apt-{appointment_id}-{int(datetime.now().timestamp())}"
    return f"https://meet.jit.si/{room}"


@app.route("/doctor/accept_appointment/<int:appointment_id>", methods=["POST"])
@doctor_required
def doctor_accept_appointment(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    # ensure appointment exists
    cur.execute("SELECT status FROM appointments WHERE id = ?", (appointment_id,))
    row = cur.fetchone()
    if not row:
        flash("Appointment not found.", "danger")
        return redirect(url_for("home"))
    # set status to scheduled and generate teleconsult link
    tele_link = _generate_jitsi_link(appointment_id)
    cur.execute(
        "UPDATE appointments SET status = ?, teleconsult_link = ? WHERE id = ?",
        ("scheduled", tele_link, appointment_id),
    )
    conn.commit()
    flash("Appointment accepted and scheduled. Teleconsult link generated.", "success")
    return redirect(url_for("home"))


@app.route("/doctor/reschedule_appointment/<int:appointment_id>", methods=["POST"])
@doctor_required
def doctor_reschedule_appointment(appointment_id):
    new_date = request.form.get("new_date")
    new_time = request.form.get("new_time")
    if not new_date or not new_time:
        flash("New date and time are required to reschedule.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM appointments WHERE id = ?", (appointment_id,))
    if not cur.fetchone():
        flash("Appointment not found.", "danger")
        return redirect(url_for("home"))
    new_scheduled = f"{new_date} {new_time}"
    new_tele = _generate_jitsi_link(appointment_id)
    cur.execute(
        "UPDATE appointments SET scheduled_at = ?, appointment_date = ?, appointment_time = ?, status = ?, teleconsult_link = ? WHERE id = ?",
        (new_scheduled, new_date, new_time, "rescheduled", new_tele, appointment_id),
    )
    conn.commit()
    flash("Appointment rescheduled.", "success")
    return redirect(url_for("home"))
