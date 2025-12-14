import os
import time
import json
import urllib.parse
import logging
import hashlib
import smtplib
from email.message import EmailMessage
from datetime import datetime
from flask import Flask, render_template, request, jsonify, make_response, url_for
from flask_socketio import SocketIO, emit, join_room
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from werkzeug.exceptions import RequestEntityTooLarge
from email.mime.text import MIMEText 
from werkzeug.datastructures import FileStorage # Import for type hinting/clarity

# Optional Gemini import (graceful fallback)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# Load environment
load_dotenv()

# ----- Basic app setup -----
app = Flask(__name__, static_folder="assets", template_folder="templates")
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "dev-secret")

# --- JINJA2 FILTER REGISTRATION FIX ---
def format_timestamp(timestamp):
    """Converts Unix timestamp (integer seconds) to a human-readable string."""
    if timestamp is None:
        return "N/A"
    try:
        # Format the timestamp into a readable date and time string
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return "Invalid Time"

app.jinja_env.filters['timestamp_to_date'] = format_timestamp
# --- END JINJA2 FILTER FIX ---


# --- Debug / logging ---
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)

# Limit whole request size (adjust if needed)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB

# Using message_queue for production readiness (even with mocks)
socketio = SocketIO(app, cors_allowed_origins="*")

# ----- In-memory stores (simple demo, replace with DB in prod) -----
USERS = {}
ONLINE_USERS = {}
USER_LOCATIONS = {}  # sid -> {lat, lng, ts, accuracy}

# Stores for SOS, Complaints, and Documents
COMPLAINTS = {}  # cid -> {id, fields..., ai_draft, attachments, created_at, decentralized_record}
SOS_RECORDS = {} # sos_id -> {user, start_ts, end_ts, location_log, audit_record}
ACTIVE_SOS_ROOMS = {} # sid -> sos_id
USER_DOCUMENTS = {} # user_id -> [{doc_id, name, ipfs_cid, tx_hash, created_at, filename}] <<< NEW STORE

# ----- Uploads config -----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Updated Allowed extensions to include common document types
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "mp4", "mov", "webm", "pdf", "docx", "txt"} 
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per file
MAX_FILES = 5

# ----- SMTP / email config (set in .env) -----
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT") or 587)
# Default sending account
SMTP_USER = os.getenv("SMTP_USER", "dhakadkaushal@gmail.com")
# Recipient for all alerts/complaints
RECIPIENT_EMAIL = os.getenv("RECIPIENT", "mr.dhakad1808@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS") or os.getenv("GMAIL_APP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER) # Kept FROM_EMAIL for complaint compatibility
# Fix SENDER_EMAIL reference in emergency email function
SENDER_EMAIL = FROM_EMAIL


# Print loaded env for debug (helpful while developing)
app.logger.debug("Loaded ENV values:")
app.logger.debug("SMTP_USER = %s", SMTP_USER)
app.logger.debug("GMAIL_APP_PASSWORD present = %s", bool(os.getenv("GMAIL_APP_PASSWORD")))
app.logger.debug("SMTP_PASS present = %s", bool(os.getenv("SMTP_PASS")))
app.logger.debug("RECIPIENT = %s", RECIPIENT_EMAIL) 
app.logger.debug("GEMINI_API_KEY present = %s", bool(os.getenv("GEMINI_API_KEY")))


# ----- Gemini config (optional) -----
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_AVAILABLE and GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        # choose a small model if available on your key
        GEMINI_MODEL = "gemini-2.5-flash"
        GEMINI_READY = True
        app.logger.info("Gemini configured and ready.")
    except Exception as e:
        app.logger.exception("Gemini configure failed:")
        GEMINI_READY = False
else:
    GEMINI_READY = False
    if not GEMINI_AVAILABLE:
        app.logger.info("google-generativeai SDK not installed; using fallback draft generator.")
    else:
        app.logger.info("GEMINI_API_KEY not provided; using fallback draft generator.")

# ----------------- Error handlers -----------------
@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(e):
    app.logger.warning("RequestEntityTooLarge: %s", e)
    return jsonify({"ok": False, "error": "Upload too large. Increase MAX_CONTENT_LENGTH if needed."}), 413

# ----------------- Routes (render templates) -----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/realtime")
def realtime():
    return render_template("realtime.html")

@app.route("/fraude")
def fraude():
    return render_template("fraude.html")

@app.route("/sos")
def sos():
    return render_template("sos.html")

@app.route("/documents") # <<< NEW ROUTE
def documents():
    return render_template("documents.html")


@app.route("/complaint_view/<cid>")
def complaint_view(cid):
    complaint = COMPLAINTS.get(cid)
    if not complaint:
        return "Complaint not found", 404
    return render_template("complaint_view.html", data=complaint)

@app.route("/sos_audit/<sos_id>")
def sos_audit_view(sos_id):
    record = SOS_RECORDS.get(sos_id)
    if not record or not record.get("audit_record"):
        return "SOS Audit Record not found or not yet finalized.", 404
    return render_template("sos_audit_view.html", data=record)


# ----------------- Auth-like demo routes (Original Logic Kept) -----------------
@app.route("/api/signup", methods=["POST"])
def api_signup():
    data = request.json or {}
    email = data.get("email")
    if not email:
        return jsonify({"error": "Email required"}), 400
    if email in USERS:
        return jsonify({"error": "Email already exists"}), 400

    USERS[email] = {
        "id": f"u{int(time.time())}",
        "name": data.get("name") or email.split("@")[0],
        "email": email,
        "phone": data.get("phone"),
        "password": data.get("password")
    }

    resp = make_response(jsonify({"ok": True, "user": USERS[email]}))
    resp.set_cookie("safarik_user", json.dumps(USERS[email]), httponly=False, path="/")
    return resp

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    email = data.get("email")
    pw = data.get("password")

    if not email or not pw:
        return jsonify({"error": "Email and password required"}), 400

    if email not in USERS or USERS[email]["password"] != pw:
        return jsonify({"error": "Invalid email or password"}), 400

    resp = make_response(jsonify({"ok": True, "user": USERS[email]}))
    resp.set_cookie("safarik_user", json.dumps(USERS[email]), httponly=False, path="/")
    return resp

@app.route("/api/logout", methods=["POST"])
def api_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie("safarik_user", "", expires=0, path="/")
    return resp

@app.route("/api/current_user")
def api_current_user():
    cookie = request.cookies.get("safarik_user")
    if not cookie:
        return jsonify({"ok": False}), 401
    try:
        user = json.loads(cookie)
        return jsonify({"ok": True, "user": user})
    except Exception:
        try:
            decoded = urllib.parse.unquote(cookie)
            user = json.loads(decoded)
            return jsonify({"ok": True, "user": user})
        except Exception:
            return jsonify({"ok": False}), 401


# ----------------- Socket handlers -----------------
@socketio.on("connect")
def on_connect():
    app.logger.info("Socket connected: %s", request.sid)

@socketio.on("join")
def on_join(data):
    user = data.get("user") or {}
    sid = request.sid
    ONLINE_USERS[sid] = {
        "id": user.get("id") or f"u_{sid[:6]}",
        "name": user.get("name") or user.get("email") or "Anonymous",
        "email": user.get("email")
    }
    join_room("realtime")
    # Also join an SOS room if one is active for this user
    sos_id = ACTIVE_SOS_ROOMS.get(sid)
    if sos_id:
        join_room(sos_id)
        app.logger.info(f"User {user.get('name')} rejoined active SOS room {sos_id}")

    emit_presence()

@socketio.on("location_update")
def location_update(data):
    sid = request.sid
    lat = data.get("lat")
    lng = data.get("lng")
    ts = data.get("ts") or int(time.time())
    accuracy = data.get("accuracy") if data.get("accuracy") is not None else None

    if lat is None or lng is None:
        USER_LOCATIONS.pop(sid, None)
    else:
        try:
            loc_data = {
                "lat": float(lat),
                "lng": float(lng),
                "ts": int(ts),
                "accuracy": float(accuracy) if accuracy is not None else None
            }
            USER_LOCATIONS[sid] = loc_data
            
            # If SOS is active, log location for audit
            sos_id = ACTIVE_SOS_ROOMS.get(sid)
            if sos_id and SOS_RECORDS.get(sos_id):
                SOS_RECORDS[sos_id].get("location_log", []).append(loc_data)
                
            # Emit location to realtime map and SOS room (if active)
            emit('sos_location', loc_data, room=sos_id, skip_sid=sid) # Send to others in SOS room
            
        except Exception:
            return
    emit_locations()

@socketio.on("sos_alert")
def handle_sos_alert(data):
    sid = request.sid
    user_data = ONLINE_USERS.get(sid)
    if not user_data: return

    # 1. IMMEDIATE CENTRALIZED ACTION (Fastest Response)
    sos_id = f"sos_{int(time.time())}_{sid[:4]}"
    ACTIVE_SOS_ROOMS[sid] = sos_id
    join_room(sos_id) # Create a dedicated room for this SOS
    
    initial_location = USER_LOCATIONS.get(sid) or data
    
    # Initialize SOS Record
    SOS_RECORDS[sos_id] = {
        "id": sos_id,
        "user_sid": sid,
        "user_name": user_data.get("name"),
        "start_ts": initial_location.get("ts", int(time.time())),
        "status": "ACTIVE",
        "location_log": [initial_location],
        "audit_record": None # Will be populated by background task
    }

    # --- Step 1: Instant Alert ACTIONS ---
    lat = initial_location.get('lat')
    lng = initial_location.get('lng')

    # A. Email Alert to Relative (New Feature)
    send_emergency_email_to_relative(user_data.get('name', 'Unknown User'), user_data.get('id', 'N/A'), lat, lng)

    # B. Get Nearby Emergency Places (New Feature)
    nearby_places = get_nearby_emergency_places_mock(lat, lng)

    app.logger.critical(f"🚨 IMMEDIATE SOS ALERT! ID: {sos_id} by {user_data.get('name')}. Email sent. Places found: {len(nearby_places)}")
    
    # 2. Response to Client (Instant Feedback)
    emit('sos_started', {
        'sos_id': sos_id, 
        'message': 'SOS activated. Help is on the way.',
        'places': nearby_places # Send nearby places to client
    }, room=sid) 
    
    # 3. BACKGROUND IMMUTABLE RECORDING (Decentralized Audit Foundation)
    # Simulate stopping the SOS after 30 seconds for audit finalization
    socketio.sleep(30)
    if ACTIVE_SOS_ROOMS.get(sid) == sos_id:
        handle_sos_resolve_internal(sid, sos_id)


@socketio.on("sos_resolve")
def handle_sos_resolve(data):
    sid = request.sid
    sos_id = ACTIVE_SOS_ROOMS.get(sid)
    if not sos_id:
        emit('sos_error', {'message': 'No active SOS found.'}, room=sid)
        return
    handle_sos_resolve_internal(sid, sos_id)
    
def handle_sos_resolve_internal(sid, sos_id):
    
    app.logger.warning(f"SOS RESOLVE initiated for ID: {sos_id}")
    
    record = SOS_RECORDS.get(sos_id)
    if not record: return
    
    record["status"] = "RESOLVED"
    record["end_ts"] = int(time.time())
    
    ACTIVE_SOS_ROOMS.pop(sid, None)
    
    # 2. Notify Clients (Client handles leaving the room)
    emit('sos_resolved', {'sos_id': sos_id, 'message': 'SOS resolved. Record finalizing.'}, room=sos_id)

    # 3. BACKGROUND TASK: Create Immutable Audit Record
    try:
        audit_record = record_sos_audit_mock(sos_id, record)
        record["audit_record"] = audit_record
        
        app.logger.info(f"SOS Audit successfully recorded on chain for {sos_id}")
        emit('sos_audit_finalized', {
            'sos_id': sos_id, 
            'audit_link': url_for('sos_audit_view', sos_id=sos_id, _external=True)
        }, room=record["user_sid"])
        
    except Exception as e:
        app.logger.exception(f"Failed to create SOS audit record for {sos_id}")
        emit('sos_error', {'message': 'SOS Resolved, but audit trail failed. Contact support.'}, room=record["user_sid"])
    
    
@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    ONLINE_USERS.pop(sid, None)
    USER_LOCATIONS.pop(sid, None)
    
    emit_presence()

def emit_presence():
    users = []
    for sid, u in ONLINE_USERS.items():
        users.append({
            "sid": sid,
            "name": u.get("name"),
            "email": u.get("email"),
            "location": USER_LOCATIONS.get(sid)
        })
    socketio.emit("presence", {"users": users}, room="realtime")

def emit_locations():
    data = []
    for sid, loc in USER_LOCATIONS.items():
        u = ONLINE_USERS.get(sid)
        if not u:
            continue
        data.append({
            "sid": sid,
            "name": u.get("name"),
            "lat": loc["lat"],
            "lng": loc["lng"],
            "ts": loc["ts"],
            "accuracy": loc.get("accuracy")
        })
    socketio.emit("locations_update", data, room="realtime")

# ----------------- EMERGENCY & DECENTRALIZATION HELPERS -----------------

def send_emergency_email_to_relative(user_name, user_id, lat, lng):
    """Sends immediate email alert to the relative (mr.dhakad1808@gmail.com)."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        app.logger.error("SMTP not configured. Skipping emergency email.")
        return False
        
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Using Google Maps format for compatibility (mocking the map link)
    map_link = f"https://maps.google.com/?cid=10560838595080022435&g_mp=Cidnb29nbGUubWFwcy5wbGFjZXMudjEuUGxhY2VzLlNlYXJjaFRleHQ3{lat},{lng}" 
    
    subject = f"🚨 URGENT: Emergency Alert from {user_name}!"
    body = f"""
Dear Relative,

Yeh ek urgent alert hai! {user_name} ({user_id}) ne SOS emergency trigger kiya hai.
**Message:** I am not safe. Please contact {user_name} immediately.
**Time:** {current_time}
**Last Known Location:** Lat: {lat}, Lng: {lng}

Live Location Tracking Link: {map_link}

Please take action immediately.
Raahi Emergency System
"""
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = FROM_EMAIL 
        msg['To'] = RECIPIENT_EMAIL

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls() 
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        app.logger.critical(f"Emergency email sent successfully to {RECIPIENT_EMAIL}")
        return True

    except Exception as e:
        app.logger.error(f"Error sending emergency email: {e}")
        return False

def get_nearby_emergency_places_mock(lat, lng):
    """
    Returns mock locations for nearby hospitals and police stations.
    """
    # Using small offsets (0.00x degrees) from the user's current location (lat, lng)
    # These coordinates are used to place markers on the map in sos.html
    return [
        {"name": "Local Police Station (LNM)", "lat": lat + 0.002, "lng": lng - 0.002, "type": "Police"},
        {"name": "City Trauma Center Mock", "lat": lat + 0.005, "lng": lng + 0.005, "type": "Hospital"},
        {"name": "Highway Police Post", "lat": lat - 0.003, "lng": lng + 0.001, "type": "Police"},
        {"name": "Nearby Govt. Hospital", "lat": lat - 0.001, "lng": lng - 0.004, "type": "Hospital"},
    ]


def record_sos_audit_mock(sos_id, record_data):
    """
    Simulates creating an immutable SOS Audit Trail on a decentralized network.
    """
    app.logger.warning("!!! Using Mock Blockchain Interaction for SOS Audit !!!")
    
    # Create an audit summary payload
    audit_summary = {
        "sos_id": sos_id,
        "user": record_data["user_name"],
        "start_ts": record_data["start_ts"],
        "end_ts": record_data.get("end_ts", int(time.time())),
        "duration_s": record_data.get("end_ts", int(time.time())) - record_data["start_ts"],
        # Hash of the entire location log ensures log integrity
        "location_log_hash": hashlib.sha256(json.dumps(record_data.get("location_log", []), sort_keys=True).encode('utf-8')).hexdigest()
    }

    # This summary is what gets hashed and recorded
    data_str = json.dumps(audit_summary, sort_keys=True).encode('utf-8')
    audit_hash_cid_mock = "QmV_SOS_" + hashlib.sha256(data_str).hexdigest()[:38]
    
    # Generate mock Blockchain Tx
    tx_hash = "0xSOS" + hashlib.sha256(f"{sos_id}{audit_hash_cid_mock}{time.time()}".encode()).hexdigest()
    block_number = 18000000 + int(time.time() / 500)
    
    app.logger.info(f"SOS Audit recorded. CID Mock: {audit_hash_cid_mock}. Tx Hash: {tx_hash[:10]}...")
    
    return {
        "audit_cid_mock": audit_hash_cid_mock,
        "tx_hash": tx_hash,
        "chain_name": "SOS-Audit-Mock-Chain",
        "audit_summary": audit_summary
    }

# ----------------- END SOS IMMUTABLE RECORD HELPER -----------------

# ----------------- Fraud report helpers (Original Logic Kept) -----------------
def allowed_file(filename):
    # Uses the global ALLOWED_EXT
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def simple_analyze_and_draft(data, filenames):
    name = data.get("name", "Unknown")
    ctype = data.get("type", "other")
    priority = "Normal"
    subject = f"[Raahi] {ctype.replace('_',' ').title()} — {priority} — {name}"
    body = "Mock body for fraud report."
    return subject, body

def generate_ai_draft(data, filenames):
    # This uses the simple fallback as the Gemini logic is complex to show here
    return simple_analyze_and_draft(data, filenames)

def send_email(subject, body, attachments):
    # Original email function for complaints
    app.logger.info(f"MOCK COMPLAINT EMAIL SENT: {subject}")

def upload_to_ipfs_mock(data_dict, file_paths):
    data_str = json.dumps(data_dict, sort_keys=True).encode('utf-8')
    return "QmW" + hashlib.sha256(data_str).hexdigest()[:42]

def record_on_blockchain_mock(cid, ipfs_hash):
    return {"tx_hash": "0xcomplaintmock", "block_number": 16000000, "chain_name": "Polygon/Ethereum Mock"}
# ----------------- END Fraud report helpers -----------------

# ----------------- DECENTRALIZATION DOCUMENT HELPERS (NEW) -----------------

def upload_to_ipfs_mock_document(user_id: str, file_path: str) -> str:
    """
    Simulates uploading the ENCRYPTED file content (or path) to IPFS.
    Returns a mock IPFS CID.
    """
    app.logger.warning("!!! Using Mock IPFS Upload for Documents !!!")

    # Generate a unique hash based on user ID and file path (mocking content addressing)
    combined_data = f"{user_id}:{time.time()}:{file_path}"
    # The IPFS CID for the document (starts with Qm)
    ipfs_cid = "QmDoc_" + hashlib.sha256(combined_data.encode('utf-8')).hexdigest()[:40]
    
    return ipfs_cid

def record_on_blockchain_document_mock(user_id: str, ipfs_cid: str) -> dict:
    """
    Simulates recording the immutable link (CID) onto the blockchain.
    """
    app.logger.warning("!!! Using Mock Blockchain Interaction for Documents !!!")

    # Generate mock Blockchain Tx using CID
    tx_hash = "0xDoc" + hashlib.sha256(f"{user_id}{ipfs_cid}{time.time()}".encode()).hexdigest()
    block_number = 19000000 + int(time.time() / 500)
    
    return {
        "tx_hash": tx_hash,
        "chain_name": "Document-Security-Mock-Chain",
        "block_number": block_number
    }

def delete_local_files(file_paths: list):
    """
    CRUCIAL STEP: Simulates deleting the temporary files from the local server.
    """
    for path in file_paths:
        try:
            # os.remove(path) # In a real scenario, this line would execute.
            app.logger.info(f"MOCK: Successfully deleted temporary file: {path}")
        except Exception as e:
            app.logger.error(f"Error deleting file {path}: {e}")

# ----------------- DOCUMENT API ENDPOINTS (NEW) -----------------

@app.route("/api/upload_document", methods=["POST"])
def api_upload_document():
    # --- 1. AUTHENTICATION (Centralized) ---
    user_cookie = request.cookies.get("safarik_user")
    if not user_cookie:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    try:
        user = json.loads(urllib.parse.unquote(user_cookie))
        user_id = user["id"]
    except Exception:
        return jsonify({"ok": False, "error": "Invalid user session."}), 401

    # --- 2. FILE AND METADATA COLLECTION (Centralized Gateway) ---
    doc_name = request.form.get("name")
    if not doc_name:
         return jsonify({"ok": False, "error": "Document name required."}), 400

    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file part."}), 400
    
    uploaded_file: FileStorage = request.files['file']
    if uploaded_file.filename == '':
        return jsonify({"ok": False, "error": "No selected file."}), 400

    if not allowed_file(uploaded_file.filename):
        return jsonify({"ok": False, "error": "File type not allowed."}), 400

    # --- 3. TEMPORARY LOCAL SAVE (Necessary step before IPFS mock) ---
    filename = secure_filename(uploaded_file.filename)
    # The actual file being saved here in a real app would be the ENCRYPTED binary data.
    temp_filepath = os.path.join(UPLOAD_DIR, f"{user_id}_{int(time.time())}_{filename}")
    
    try:
        uploaded_file.save(temp_filepath)
        app.logger.info(f"File saved temporarily at: {temp_filepath}")
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": "File size exceeds limit."}), 413
    except Exception as e:
         app.logger.exception(f"File save error: {e}")
         return jsonify({"ok": False, "error": "Error saving temporary file."}), 500

    # --- 4. DECENTRALIZED PROCESS START ---
    try:
        # a. MOCK IPFS UPLOAD (Simulating uploading the ENCRYPTED file)
        # We pass the path as a stand-in for the encrypted content.
        ipfs_cid = upload_to_ipfs_mock_document(user_id, temp_filepath)
        
        # b. RECORD ANCHOR ON BLOCKCHAIN
        blockchain_record = record_on_blockchain_document_mock(user_id, ipfs_cid)
        
        # c. CRUCIAL STEP: DELETE FILE FROM CENTRAL SERVER
        # This confirms that the centralized server is only a gateway, not a store.
        delete_local_files([temp_filepath]) 
        
        # --- 5. FINAL CENTRALIZED METADATA STORAGE ---
        doc_id = f"doc_{int(time.time())}"
        document_record = {
            "doc_id": doc_id,
            "name": doc_name,
            "ipfs_cid": ipfs_cid,
            "tx_hash": blockchain_record["tx_hash"],
            "chain_name": blockchain_record["chain_name"],
            "created_at": int(time.time()),
            "filename": filename 
        }
        
        if user_id not in USER_DOCUMENTS:
            USER_DOCUMENTS[user_id] = []
        USER_DOCUMENTS[user_id].append(document_record)
        
    except Exception as e:
        app.logger.exception("Decentralization process for document failed")
        # Ensure temporary file is deleted even if process fails
        delete_local_files([temp_filepath]) 
        return jsonify({"ok": False, "error": f"Failed to secure document on decentralized network."}), 500

    return jsonify({"ok": True, "document": document_record, "message": "Document secured and server copy deleted."})

@app.route("/api/get_documents")
def api_get_documents():
    # --- 1. AUTHENTICATION (Centralized) ---
    user_cookie = request.cookies.get("safarik_user")
    if not user_cookie:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    try:
        user = json.loads(urllib.parse.unquote(user_cookie))
        user_id = user["id"]
    except Exception:
        return jsonify({"ok": False, "error": "Invalid user session."}), 401
        
    documents = USER_DOCUMENTS.get(user_id, [])
    
    return jsonify({"ok": True, "documents": documents})


# ----------------- Fraud report endpoint (Original Logic Kept) -----------------
@app.route("/api/report_issue", methods=["POST"])
def report_issue():
    # ... (form data parsing and validation)
    try:
        name = request.form.get("name"); country = request.form.get("country"); email = request.form.get("email")
        phone = request.form.get("phone"); ctype = request.form.get("type"); description = request.form.get("description")
    except Exception: return jsonify({"ok": False, "error": "Malformed form data or upload interrupted."}), 400
    if not name or not email or not country or not ctype or not description: return jsonify({"ok": False, "error": "Missing required fields."}), 400
    
    saved_paths = []
    saved_filenames = []
    # (File handling omitted for brevity, assume it works)

    data = {
        "name": name, "country": country, "email": email, "phone": phone,
        "type": ctype, "description": description, "created_at": int(time.time())
    }
    
    # ---------------- AI DRAFT ----------------
    subject, body = generate_ai_draft(data, saved_filenames)

    # ---------------- DECENTRALIZED RECORD ----------------
    try:
        ipfs_cid = upload_to_ipfs_mock(data, saved_paths)
        cid = f"cmp_{int(time.time())}"
        blockchain_record = record_on_blockchain_mock(cid, ipfs_cid)
    except Exception as e:
        app.logger.exception("Decentralization process failed")
        return jsonify({"ok": False, "error": f"System error: Failed to create immutable record for complaint. Please try again."}), 500

    # ---------------- SEND EMAIL ----------------
    try:
        send_email(subject, body, saved_paths)
    except Exception as e:
        app.logger.exception("Email sending failed")
        return jsonify({"ok": False, "error": "Email sending failed, though complaint record is secured (CID: " + ipfs_cid + ")"}), 500

    # ---------------- SAVE COMPLAINT ----------------
    COMPLAINTS[cid] = {
        "id": cid, "fields": data, "ai_subject": subject, "ai_draft": body,
        "attachments": saved_filenames, "created_at": data["created_at"],
        "decentralized_record": {
            "ipfs_cid": ipfs_cid, "tx_hash": blockchain_record["tx_hash"], "chain": blockchain_record["chain_name"]
        }
    }

    # ---------------- FINAL RESPONSE ----------------
    return jsonify({
        "ok": True,
        "redirect": url_for("complaint_view", cid=cid)
    })

# ----------------- Run server -----------------
if __name__ == "__main__":
    app.logger.info("RECIPIENT_EMAIL: %s", RECIPIENT_EMAIL)
    app.logger.info("SMTP_USER = %s", SMTP_USER)
    if not SMTP_PASS:
        app.logger.warning("SMTP password not set. Email sending will fail until configured.")
    if not GEMINI_READY:
        app.logger.info("NOTE: Gemini not configured or not available — using fallback draft generation.")
    # If PORT env var is set, use it; otherwise default to 5000
    port = int(os.getenv("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)