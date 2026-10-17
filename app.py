import os
import json
import re
from datetime import datetime, timedelta
import urllib.request
import urllib.error
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for

from database import (
    get_db, init_db, register_user, authenticate_user as db_auth_user,
    get_wedding_by_user, get_wedding_by_id, save_or_update_wedding,
    get_parent_sessions, get_parent_by_token, add_contact_db,
    get_contacts_by_wedding, get_contact_by_token_db, update_rsvp_db, update_guest_family_db,
    get_wedding_stats_db, log_chat_message_db, get_chat_history_db,
    generate_and_save_otp, verify_otp_db, get_user_by_email, get_user_by_phone, reset_user_password,
    consume_reset_token, verify_registration_token
)
from security import (
    validate_username, validate_email, validate_indian_phone,
    validate_password_strength, rate_limiter, clean_phone_10
)
from auth import login_required
from excel_export import export_guest_list_excel
from email_service import send_verification_email
from bot_engine import (
    process_chat_message, process_parent_collector_message,
    get_gemini_api_key, set_gemini_api_key, send_baileys_message
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "wedding_luxury_secret_key_2026_gj")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER") or not app.debug:
    app.config["SESSION_COOKIE_SECURE"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Ensure SQLite schema is ready
init_db()

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

BAILEYS_SERVICE_URL = os.environ.get("BAILEYS_SERVICE_URL", "http://127.0.0.1:5001")
_baileys_process = None

def is_port_in_use(host="127.0.0.1", port=5001):
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def ensure_baileys_running():
    global _baileys_process
    # 1. If port 5001 is already actively open and listening, service is already up!
    if is_port_in_use("127.0.0.1", 5001):
        return True

    # 2. On Render or Docker, start.sh manages the Baileys supervisor daemon
    if os.environ.get("RENDER") or os.environ.get("CONTAINER") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        return False

    # 3. For local development: ensure only a single process instance is spawned
    if _baileys_process is not None and _baileys_process.poll() is None:
        return True

    server_js = os.path.join(os.path.dirname(__file__), "whatsapp_service", "server.js")
    service_dir = os.path.join(os.path.dirname(__file__), "whatsapp_service")
    if os.path.exists(server_js):
        try:
            import subprocess
            _baileys_process = subprocess.Popen(["node", "server.js"], cwd=service_dir)
            print(f"[+] Started local Baileys service (PID: {_baileys_process.pid})")
            return True
        except Exception as e:
            print("Notice: could not auto-spawn Baileys service:", e)
    return False

def format_whatsapp_phone(raw_id):
    if not raw_id:
        return ""
    digits = "".join(filter(str.isdigit, str(raw_id).split("@")[0].split(":")[0]))
    if len(digits) >= 10:
        if digits.startswith("91") and len(digits) == 12:
            return f"+91 {digits[2:7]} {digits[7:]}"
        return f"+{digits[:-10]} {digits[-10:-5]} {digits[-5:]}".strip("+ ")
    return digits

DEFAULT_LIVE_URL = "https://wedding-guest-list-8uhm.onrender.com"

def get_base_url():
    # 1. Environment variable if explicitly set to a live public URL
    env_base = os.environ.get("BASE_URL", "").strip()
    if env_base and "127.0.0.1" not in env_base and "localhost" not in env_base:
        return env_base.rstrip('/')
        
    # 2. Render external URL environment variable
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip('/')

    # 3. Dynamic request host if present and not localhost
    try:
        if request and request.host:
            h = request.host.lower()
            if "127.0.0.1" not in h and "localhost" not in h:
                proto = request.headers.get("X-Forwarded-Proto", request.scheme)
                host = request.headers.get("X-Forwarded-Host", request.host)
                url = f"{proto}://{host}".rstrip('/')
                try:
                    from database import set_app_setting
                    set_app_setting("base_url", url)
                except Exception:
                    pass
                return url
    except Exception:
        pass

    # 4. Check saved base_url from database
    try:
        from database import get_app_setting
        saved = get_app_setting("base_url")
        if saved and "127.0.0.1" not in saved and "localhost" not in saved:
            return saved.rstrip('/')
    except Exception:
        pass

    # 5. Default to live production URL (never send 127.0.0.1 to a user's phone!)
    return DEFAULT_LIVE_URL

def call_baileys(endpoint, method="GET", data=None):
    urls = [
        f"{BAILEYS_SERVICE_URL}{endpoint}",
        f"http://127.0.0.1:5001{endpoint}",
        f"http://localhost:5001{endpoint}"
    ]
    seen = set()
    unique_urls = [u for u in urls if not (u in seen or seen.add(u))]
    
    last_err = None
    for attempt in range(2):
        for url in unique_urls:
            req_data = json.dumps(data).encode("utf-8") if data is not None else None
            headers = {"Content-Type": "application/json"} if req_data else {}
            req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8")), response.status
            except urllib.error.HTTPError as e:
                try:
                    err_body = json.loads(e.read().decode("utf-8"))
                    return err_body, e.code
                except Exception:
                    return {"error": str(e)}, e.code
            except Exception as e:
                last_err = str(e)
        if attempt == 0:
            ensure_baileys_running()
            import time
            time.sleep(1)
            
    return {"status": "offline", "error": last_err}, 503

@app.route("/api/system/health")
def system_health():
    try:
        from database import DB_PATH, get_db
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM parent_sessions")
        p_count = c.fetchone()["count"]
        c.execute("SELECT token, role, name, phone FROM parent_sessions")
        p_tokens = [dict(r) for r in c.fetchall()]
        c.execute("SELECT COUNT(*) as count FROM contacts")
        c_count = c.fetchone()["count"]
        c.execute("SELECT token, name, phone FROM contacts LIMIT 10")
        c_tokens = [dict(r) for r in c.fetchall()]
        conn.close()
        b_res, b_code = call_baileys("/status")
        return jsonify({
            "status": "healthy",
            "db_path": DB_PATH,
            "parent_count": p_count,
            "parents": p_tokens,
            "contact_count": c_count,
            "contacts": c_tokens,
            "baileys": b_res,
            "baileys_http_code": b_code
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------------------
# LANDING & AUTHENTICATION (STRICT PROTECTION)
# -------------------------------------------------------------
@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember")
        
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        
        # 1. Rate Limiting Check
        allowed, retry_after = rate_limiter.check_login_rate_limit(client_ip, username)
        if not allowed:
            return render_template(
                "login.html",
                active_tab="signin",
                error=f"ઘણા નિષ્ફળ પ્રયાસો થયા છે. સુરક્ષા માટે કૃપા કરીને {retry_after // 60 + 1} મિનિટ પછી ફરી પ્રયાસ કરો. (Too many failed login attempts)."
            ), 429
            
        # 2. Authoritative Server-Side Authentication
        user = db_auth_user(username, password)
        if user:
            # Reset failed login counter on success
            rate_limiter.reset_login_rate_limit(client_ip, username)
            
            # Defense against Session Fixation: clear prior session data
            session.clear()
            session.permanent = bool(remember)
            
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["user_role"] = "MASTER"
            session["logged_in"] = True
            
            # If user has not yet created a wedding, send to setup wizard
            wedding = get_wedding_by_user(user["id"])
            if not wedding:
                return redirect(url_for("setup_wizard"))
            return redirect(url_for("dashboard"))
        else:
            # Record failure in rate limiter
            rate_limiter.record_login_failure(client_ip, username)
            return render_template(
                "login.html",
                active_tab="signin",
                error="અમાન્ય ઓળખકર્તા અથવા પાસવર્ડ. કૃપા કરીને ફરી પ્રયાસ કરો. (Invalid username, email, or password)."
            ), 401
            
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", active_tab="signin", error=None)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        reg_token = request.form.get("reg_token", "").strip()
        otp_code = request.form.get("otp_code", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        # 1. Authoritative Server-Side Validations
        u_ok, u_err, clean_username = validate_username(username)
        if not u_ok:
            return render_template("login.html", active_tab="register", error=u_err), 400
            
        e_ok, e_err, clean_email = validate_email(email)
        if not e_ok:
            return render_template("login.html", active_tab="register", error=e_err), 400
            
        p_ok, p_err, clean_p, _ = validate_indian_phone(phone)
        if not p_ok:
            return render_template("login.html", active_tab="register", error=p_err), 400
            
        pwd_ok, pwd_err = validate_password_strength(password)
        if not pwd_ok:
            return render_template("login.html", active_tab="register", error=pwd_err), 400
            
        if password != confirm_password:
            return render_template("login.html", active_tab="register", error="પાસવર્ડ અને Confirm Password મેચ થતા નથી. (Passwords do not match)."), 400
            
        # 2. Strict OTP / Token Verification Boundary (Never trust frontend flags!)
        if reg_token:
            tok_ok, tok_err = verify_registration_token(clean_email, reg_token)
            if not tok_ok:
                return render_template("login.html", active_tab="register", error=tok_err), 400
        elif otp_code:
            is_valid, otp_err, _ = verify_otp_db(clean_email, otp_code, purpose="REGISTER")
            if not is_valid:
                return render_template("login.html", active_tab="register", error=otp_err), 400
        else:
            return render_template("login.html", active_tab="register", error="કૃપા કરીને પહેલા તમારા ઇમેઇલ પર આવેલ 6-અંકનો સુરક્ષા કોડ વેરિફાય કરો. (Email OTP verification required)."), 400
            
        # 3. Create User in Database
        res = register_user(clean_username, clean_email, password, phone=clean_p)
        if res.get("success"):
            # Session Fixation Protection
            session.clear()
            session["user_id"] = res["user_id"]
            session["username"] = clean_username
            session["user_role"] = "MASTER"
            session["logged_in"] = True
            return redirect(url_for("setup_wizard"))
        else:
            return render_template("login.html", active_tab="register", error=res.get("error", "નોંધણી નિષ્ફળ રહી.")), 400
            
    return render_template("login.html", active_tab="register", error=None)

# -------------------------------------------------------------
# EMAIL OTP AUTHENTICATION & PASSWORD RESET ENDPOINTS
# -------------------------------------------------------------
@app.route("/api/auth/send-otp", methods=["POST"])
def auth_send_otp():
    try:
        data = request.get_json(silent=True) or request.form
        raw_ident = (data.get("email") or data.get("identifier") or "").strip()
        purpose = data.get("purpose", "REGISTER").strip().upper()
        
        if not raw_ident:
            return jsonify({"success": False, "error": "કૃપા કરીને ઇમેઇલ અથવા મોબાઇલ નંબર દાખલ કરો."}), 400
            
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        
        # Rate Limiting & Cooldown Protection
        allowed, r_err, retry_after = rate_limiter.check_otp_rate_limit(client_ip, raw_ident)
        if not allowed:
            return jsonify({"success": False, "error": r_err, "retry_after": retry_after}), 429
            
        # Resolve identifier
        if "@" in raw_ident:
            e_ok, e_err, target_email = validate_email(raw_ident)
            if not e_ok:
                return jsonify({"success": False, "error": e_err}), 400
        else:
            p_ok, p_err, clean_p, _ = validate_indian_phone(raw_ident)
            if not p_ok:
                return jsonify({"success": False, "error": p_err}), 400
                
            if purpose == "REGISTER":
                return jsonify({
                    "success": False,
                    "error": "નોંધણી માટે ઇમેઇલ વેરિફિકેશન જરૂરી છે. કૃપા કરીને તમારું ઇમેઇલ એડ્રેસ દાખલ કરો."
                }), 400
                
            user = get_user_by_phone(clean_p)
            if not user or not user.get("email"):
                # Safe generic response to prevent account enumeration
                return jsonify({
                    "success": True,
                    "message": "જો આ મોબાઇલ નંબર સાથે રજીસ્ટર એકાઉન્ટ હશે, તો 6-અંકનો સુરક્ષા કોડ ઇમેઇલ પર મોકલી દેવાયો છે."
                })
            target_email = user["email"].lower()
        
        # Check purpose rules
        existing_user = get_user_by_email(target_email)
        if purpose == "REGISTER" and existing_user:
            return jsonify({
                "success": False,
                "error": "આ ઇમેઇલ પહેલેથી રજીસ્ટર થયેલો છે. કૃપા કરીને સાઇન-ઇન કરો અથવા પાસવર્ડ રીસેટ કરો."
            }), 400
        elif purpose == "RESET" and not existing_user:
            # Safe anti-enumeration response
            return jsonify({
                "success": True,
                "message": "જો આ ઇમેઇલ સાથે કોઈ એકાઉન્ટ રજીસ્ટર હશે, તો 6-અંકનો સુરક્ષા કોડ મોકલી દેવાયો છે."
            })
            
        otp_code, err = generate_and_save_otp(target_email, purpose=purpose)
        if err:
            return jsonify({"success": False, "error": err}), 400
            
        # Send real luxury HTML email via Gmail SMTP (Zero screen leakage!)
        mail_ok, mail_msg = send_verification_email(target_email, otp_code, purpose=purpose)
        if not mail_ok:
            return jsonify({
                "success": False,
                "error": f"ઇમેઇલ મોકલવામાં ભૂલ થઈ: {mail_msg}"
            }), 500
            
        # Record successful OTP dispatch in rate limiter
        rate_limiter.record_otp_request(client_ip, raw_ident)
        
        # Obfuscate email for user feedback
        parts = target_email.split("@")
        obf = parts[0][:2] + "***@" + parts[1] if len(parts) == 2 and len(parts[0]) > 2 else target_email
        
        return jsonify({
            "success": True,
            "target_email": target_email,
            "message": f"તમારા રજીસ્ટર્ડ ઇમેઇલ ({obf}) પર 6-અંકનો સુરક્ષા કોડ મોકલી દેવાયો છે. કૃપા કરીને ઇનબોક્સ તપાસો."
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"સર્વર ભૂલ: {str(e)}"}), 500

@app.route("/api/auth/verify-otp", methods=["POST"])
def auth_verify_otp():
    try:
        data = request.get_json(silent=True) or request.form
        email = (data.get("email") or data.get("identifier") or "").strip().lower()
        otp_code = data.get("otp_code", "").strip()
        purpose = data.get("purpose", "REGISTER").strip().upper()
        
        if not email:
            return jsonify({"success": False, "error": "ઇમેઇલ એડ્રેસ જરૂરી છે."}), 400
        if not otp_code or len(otp_code) != 6:
            return jsonify({"success": False, "error": "કૃપા કરીને 6-અંકનો સુરક્ષા કોડ દાખલ કરો."}), 400
            
        # If user entered phone on reset, resolve to email
        if "@" not in email:
            clean_p = clean_phone_10(email)
            u = get_user_by_phone(clean_p)
            if u and u.get("email"):
                email = u["email"].lower()
                
        is_valid, err, tok_data = verify_otp_db(email, otp_code, purpose=purpose)
        if is_valid:
            resp = {
                "success": True,
                "message": "સુરક્ષા કોડ સફળતાપૂર્વક વેરિફાય થયો!"
            }
            if "reset_token" in tok_data:
                resp["reset_token"] = tok_data["reset_token"]
            if "reg_token" in tok_data:
                resp["reg_token"] = tok_data["reg_token"]
            return jsonify(resp)
            
        return jsonify({"success": False, "error": err or "અમાન્ય અથવા ખોટો OTP કોડ."}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/auth/reset-password", methods=["POST"])
def auth_reset_password():
    """
    Two-step Password Reset Authorization:
    Accepts reset_token (from successful OTP verification) and new_password.
    Also supports fallback (identifier + otp_code + new_password).
    """
    try:
        data = request.get_json(silent=True) or request.form
        reset_token = data.get("reset_token", "").strip()
        raw_ident = (data.get("email") or data.get("identifier") or "").strip()
        otp_code = data.get("otp_code", "").strip()
        new_password = data.get("new_password", "")
        confirm_password = data.get("confirm_password", "")
        
        if confirm_password and new_password != confirm_password:
            return jsonify({"success": False, "error": "નવો પાસવર્ડ અને Confirm Password મેચ થતા નથી."}), 400
            
        # Password strength validation
        pwd_ok, pwd_err = validate_password_strength(new_password)
        if not pwd_ok:
            return jsonify({"success": False, "error": pwd_err}), 400
            
        # 1. Primary Authorization Path: via single-use reset_token
        if reset_token:
            ok, err = consume_reset_token(reset_token, new_password)
            if ok:
                session.clear()
                return jsonify({
                    "success": True,
                    "message": "પાસવર્ડ સફળતાપૂર્વક અપડેટ થઈ ગયો છે! હવે તમે સાઇન-ઇન કરી શકો છો."
                })
            return jsonify({"success": False, "error": err or "પાસવર્ડ રીસેટ કરવામાં નિષ્ફળતા."}), 400
            
        # 2. Fallback Path: via direct OTP verification
        if not raw_ident or not otp_code:
            return jsonify({"success": False, "error": "અમાન્ય રીસેટ વિનંતી. સુરક્ષા કોડ જરૂરી છે."}), 400
            
        target_email = raw_ident.lower() if "@" in raw_ident else None
        if not target_email:
            clean_p = clean_phone_10(raw_ident)
            user = get_user_by_phone(clean_p)
            if not user or not user.get("email"):
                return jsonify({"success": False, "error": "આ ઓળખકર્તા સાથે કોઈ રજીસ્ટર એકાઉન્ટ મળ્યું નથી."}), 404
            target_email = user["email"].lower()
            
        is_valid, err, tok_data = verify_otp_db(target_email, otp_code, purpose="RESET")
        if not is_valid:
            return jsonify({"success": False, "error": err or "અમાન્ય અથવા એક્સપાયર થયેલ OTP કોડ."}), 400
            
        # Consume the issued reset token
        res_tok = tok_data.get("reset_token")
        if res_tok:
            ok, err = consume_reset_token(res_tok, new_password)
        else:
            ok, err = reset_user_password(target_email, new_password)
            
        if ok:
            session.clear()
            return jsonify({
                "success": True,
                "message": "પાસવર્ડ સફળતાપૂર્વક અપડેટ થઈ ગયો છે! હવે તમે સાઇન-ઇન કરી શકો છો."
            })
        return jsonify({"success": False, "error": err or "પાસવર્ડ રીસેટ કરવામાં નિષ્ફળતા."}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def _clean_phone(p):
    if not p:
        return ""
    digits = "".join(filter(str.isdigit, str(p)))
    return digits[-10:] if len(digits) >= 10 else digits

# -------------------------------------------------------------
# SETUP WIZARD (AUTOPILOT INITIALIZATION)
# -------------------------------------------------------------
@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup_wizard():
    user_id = session.get("user_id")
    if request.method == "POST":
        form_data = {
            "groom_name": request.form.get("groom_name", "").strip(),
            "groom_phone": _clean_phone(request.form.get("groom_phone", "")),
            "groom_father_name": request.form.get("groom_father_name", "").strip(),
            "groom_father_phone": _clean_phone(request.form.get("groom_father_phone", "")),
            "groom_mother_name": request.form.get("groom_mother_name", "").strip(),
            "groom_mother_phone": _clean_phone(request.form.get("groom_mother_phone", "")),
            "bride_name": request.form.get("bride_name", "").strip(),
            "bride_phone": _clean_phone(request.form.get("bride_phone", "")),
            "bride_father_name": request.form.get("bride_father_name", "").strip(),
            "bride_father_phone": _clean_phone(request.form.get("bride_father_phone", "")),
            "bride_mother_name": request.form.get("bride_mother_name", "").strip(),
            "bride_mother_phone": _clean_phone(request.form.get("bride_mother_phone", "")),
            "wedding_date": request.form.get("wedding_date", "").strip(),
            "wedding_city": request.form.get("wedding_city", "").strip()
        }
        
        wedding_id = save_or_update_wedding(user_id, form_data)
        
        # Check if WhatsApp is already connected
        whatsapp_is_connected = False
        try:
            b_res, _ = call_baileys("/status")
            whatsapp_is_connected = (b_res.get("status") == "connected")
        except Exception:
            pass

        parents = get_parent_sessions(wedding_id)
        base_url = get_base_url()
        groom = form_data.get("groom_name", "").strip() or "વરરાજા"
        bride = form_data.get("bride_name", "").strip() or "કન્યા"

        if whatsapp_is_connected:
            # 100% AUTOPILOT: Automatically dispatch parent collector links via Baileys WhatsApp
            for p in parents:
                p_phone = p.get("phone", "").strip()
                p_token = p.get("token", "")
                p_name = p.get("name", "").strip()
                p_role = p.get("role", "FATHER")
                
                if p_phone and p_token and p_name:
                    role_gu = "પિતાશ્રી (પપ્પા)" if p_role == "FATHER" else "માતાશ્રી (મમ્મી)"
                    side_gu = "પિતા પક્ષ" if p_role == "FATHER" else "માતા પક્ષ"
                    collector_url = f"{base_url}/collector/{p_token}"
                    
                    collector_msg = (
                        f"💍 જય શ્રી કૃષ્ણ પૂજ્ય {p_name} {role_gu}! 🙏\n\n"
                        f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા અમારો AI સહાયક તૈયાર છે.\n\n"
                        f"આપ ફક્ત નીચે આપેલી લિંક પર ક્લિક કરીને આપના સંબંધીઓ (ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો) ના નામ જણાવશો:\n\n"
                        f"{collector_url}\n\n"
                        f"(જો લિંક વાદળી/ક્લિક ન થાય, તો આ નંબર સેવ કરો અથવા આ મેસેજ પર 'હા' લખીને મોકલો, જેથી લિંક ખૂલી જશે.)\n\n"
                        f"આપ જે નામ આપશો એ આપોઆપ લગ્નના ગેસ્ટ લિસ્ટમાં ગોઠવાઈ જશે! 🌸"
                    )
                    try:
                        send_baileys_message(p_phone, collector_msg)
                    except Exception as e:
                        print("Autopilot parent dispatch notice:", e)
                        
            return redirect(url_for("dashboard"))
        else:
            # Redirect with need_whatsapp=1 so Dashboard immediately pops up the QR code to link
            return redirect(url_for("dashboard", need_whatsapp=1))
        
    wedding = get_wedding_by_user(user_id)
    return render_template("setup.html", wedding=wedding)

# -------------------------------------------------------------
# HOST DASHBOARD (STRICT AUTHENTICATION REQUIRED)
# -------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    if not wedding:
        return redirect(url_for("setup_wizard"))
        
    wid = wedding["id"]
    stats = get_wedding_stats_db(wid)
    parents = get_parent_sessions(wid)
    contacts = get_contacts_by_wedding(wid)
    
    # Calculate relatives collected per parent
    for p in parents:
        p["collected_count"] = sum(1 for c in contacts if c.get("added_by") == p.get("role") and c.get("side") == p.get("side"))
    
    # Check Baileys WhatsApp connection status
    ensure_baileys_running()
    b_res, _ = call_baileys("/status")
    whatsapp_status = "connected" if b_res.get("status") == "connected" else "offline"
    raw_phone = b_res.get("user", {}).get("id", "") if b_res.get("user") else ""
    whatsapp_phone = format_whatsapp_phone(raw_phone)
    
    return render_template(
        "dashboard.html",
        wedding=wedding,
        stats=stats,
        parents=parents,
        contacts=contacts,
        whatsapp_status=whatsapp_status,
        whatsapp_phone=whatsapp_phone,
        gemini_configured=bool(get_gemini_api_key())
    )

# -------------------------------------------------------------
# PARENT COLLECTOR WEB CHATBOT (/collector/<token>)
# Strict Token-Based Isolation: No credentials needed for parents
# -------------------------------------------------------------
@app.route("/collector/<token>")
@app.route("/collector/<token>/")
def parent_collector_view(token):
    clean_token = str(token).strip().rstrip('/')
    parent = get_parent_by_token(clean_token)
    if not parent:
        return render_template("error_link.html"), 404
        
    history = get_chat_history_db(clean_token)
    if not history:
        role_gu = "પિતાશ્રી (પપ્પા)" if parent.get("role") == "FATHER" else "માતાશ્રી (મમ્મી)"
        side_gu = "પિતા પક્ષ (Father's Side)" if parent.get("role") == "FATHER" else "માતા પક્ષ (Mother's Side)"
        groom = parent.get("groom_name", "").strip() or "વરરાજા"
        bride = parent.get("bride_name", "").strip() or "કન્યા"
        
        initial_greeting = (
            f"💍 જય શ્રી કૃષ્ણ પૂજ્ય {parent.get('name', '')} {role_gu}! 🙏\n\n"
            f"{groom} અને {bride} ના લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા માટે હું આપનો સહાયક છું.\n\n"
            f"આપ આપના સંબંધીઓ (આપના ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો વગેરે) ના નામ અને મોબાઈલ નંબર અહીં જણાવો.\n\n"
            f"દાખલા તરીકે:\n"
            f"• 'મારા ભાઈ રમેશભાઈ 9825012345'\n"
            f"• 'કાકા મહેશભાઈ 9824012345'\n\n"
            f"શરૂ કરવા માટે જણાવશો, આપના ભાઈઓ કે કાકા કોણ કોણ છે?"
        )
        log_chat_message_db(clean_token, "bot", initial_greeting)
        history = get_chat_history_db(clean_token)
        
    return render_template("parent_collector.html", parent=parent, history=history)

@app.route("/api/collector-chat/<token>", methods=["POST"])
@app.route("/api/collector-chat/<token>/", methods=["POST"])
def api_collector_chat(token):
    clean_token = str(token).strip().rstrip('/')
    parent = get_parent_by_token(clean_token)
    if not parent:
        return jsonify({"error": "Invalid token"}), 404
        
    payload = request.get_json() or {}
    message = payload.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
        
    result = process_parent_collector_message(parent, message)
    return jsonify(result)

# -------------------------------------------------------------
# GUEST RSVP WEB CHATBOT (/chat/<token>)
# Zero Data Leakage: Guest only sees their family chat
# -------------------------------------------------------------
@app.route("/chat/<token>")
@app.route("/chat/<token>/")
def guest_chat_view(token):
    clean_token = str(token).strip().rstrip('/')
    contact = get_contact_by_token_db(clean_token)
    if not contact:
        return render_template("error_link.html"), 404
        
    history = get_chat_history_db(clean_token)
    if not history:
        groom = contact.get("groom_name", "").strip() or "વરરાજા"
        bride = contact.get("bride_name", "").strip() or "કન્યા"
        side_label = "વર પક્ષ" if contact.get("side") == "GROOM" else "કન્યા પક્ષ"
        cat_name = contact.get("category", "").title()
        
        initial_greeting = (
            f"💍 જય શ્રી કૃષ્ણ {contact.get('name', '')}! 🙏\n\n"
            f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગે આપનું અને આપના પરિવારનું હાર્દિક સ્વાગત છે ({side_label} ➔ {cat_name}).\n\n"
            f"લગ્નમાં આપના પરિવારમાંથી કોણ કોણ પધારી રહ્યું છે તેની વિગત માટે હું આપને માત્ર ૨ સરળ સવાલ પૂછીશ.\n\n"
            f"👉 ૧. શું આપના માતા-પિતા (વડીલો) આપની સાથે આવશે? (હા / ના)"
        )
        log_chat_message_db(clean_token, "bot", initial_greeting)
        history = get_chat_history_db(clean_token)
        
    return render_template("chat.html", contact=contact, history=history)

@app.route("/api/guest-chat/<token>", methods=["POST"])
@app.route("/api/guest-chat/<token>/", methods=["POST"])
def api_guest_chat(token):
    clean_token = str(token).strip().rstrip('/')
    contact = get_contact_by_token_db(clean_token)
    if not contact:
        return jsonify({"error": "Invalid token"}), 404
        
    payload = request.get_json() or {}
    message = payload.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
        
    result = process_chat_message(token, message)
    if isinstance(result, dict):
        if "reply" not in result and "bot_reply" in result:
            result["reply"] = result["bot_reply"]
        elif "bot_reply" not in result and "reply" in result:
            result["bot_reply"] = result["reply"]
    return jsonify(result)

@app.route("/preview")
def preview_theme():
    return render_template("preview_theme.html")

# -------------------------------------------------------------
# AUTH-PROTECTED API ROUTES
# -------------------------------------------------------------
@app.route("/api/send-parent-link/<token>", methods=["POST"])
@login_required
def api_send_parent_link(token):
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    if not wedding:
        return jsonify({"success": False, "error": "No wedding suite configured for this user."}), 403

    parent = get_parent_by_token(token)
    if not parent or not parent.get("phone"):
        return jsonify({"success": False, "error": "Parent phone number is not configured."}), 400
        
    # Strict Authorization / Anti-IDOR Check: Tenant Isolation
    if parent.get("wedding_id") != wedding["id"]:
        return jsonify({"success": False, "error": "Unauthorized: Access denied to this wedding resource."}), 403
        
    role_gu = "પિતાશ્રી (પપ્પા)" if parent["role"] == "FATHER" else "માતાશ્રી (મમ્મી)"
    side_gu = "પિતા પક્ષ" if parent["role"] == "FATHER" else "માતા પક્ષ"
    base_url = get_base_url()
    collector_url = f"{base_url}/collector/{token}"
    groom = parent.get("groom_name", "").strip() or "વરરાજા"
    bride = parent.get("bride_name", "").strip() or "કન્યા"
    
    collector_msg = (
        f"જય શ્રી કૃષ્ણ પૂજ્ય {parent['name']} {role_gu}!\n\n"
        f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા અમારો AI સહાયક તૈયાર છે.\n\n"
        f"આપ ફક્ત નીચે આપેલી લિંક પર ક્લિક કરીને આપના સંબંધીઓ (ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો) ના નામ જણાવશો:\n\n"
        f"{collector_url}\n\n"
        f"(નોંધ: જો લિંક વાદળી/ક્લિક ન થાય, તો આ નંબર સેવ કરો અથવા આ મેસેજ પર 'હા' લખીને મોકલો, જેથી લિંક ખૂલી જશે.)\n\n"
        f"આપ જે નામ આપશો એ આપોઆપ લગ્નના ગેસ્ટ લિસ્ટમાં ગોઠવાઈ જશે!"
    )
    
    clean_p = clean_phone_10(parent["phone"])
    wa_direct_url = f"https://wa.me/91{clean_p}?text={urllib.parse.quote(collector_msg)}"
    
    res, code = send_baileys_message(parent["phone"], collector_msg)
    if code in [200, 201] and res.get("success"):
        return jsonify({
            "success": True,
            "message": f"Link sent to {parent['name']} ({parent['phone']}) on WhatsApp!",
            "wa_direct_url": wa_direct_url,
            "response": res
        }), 200
    else:
        err_msg = res.get("error", "WhatsApp service unavailable or not linked.")
        not_conn = res.get("not_connected", code == 503)
        return jsonify({
            "success": False,
            "error": err_msg,
            "not_connected": not_conn,
            "wa_direct_url": wa_direct_url,
            "response": res
        }), 200

@app.route("/api/send-guest-link/<token>", methods=["POST"])
@login_required
def api_send_guest_link(token):
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    if not wedding:
        return jsonify({"success": False, "error": "No wedding suite configured for this user."}), 403

    contact = get_contact_by_token_db(token)
    if not contact or not contact.get("phone"):
        return jsonify({"success": False, "error": "Contact phone number is not available."}), 400
        
    # Strict Authorization / Anti-IDOR Check: Tenant Isolation
    if contact.get("wedding_id") != wedding["id"]:
        return jsonify({"success": False, "error": "Unauthorized: Access denied to this wedding resource."}), 403
        
    base_url = get_base_url()
    chat_url = f"{base_url}/chat/{contact['token']}"
    groom = contact.get("groom_name", "").strip() or "વરરાજા"
    bride = contact.get("bride_name", "").strip() or "કન્યા"
    guest_name = contact.get("name", "").strip()
    
    invite_text = (
        f"જય શ્રી કૃષ્ણ {guest_name}!\n\n"
        f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગે આપનું અને આપના પરિવારનું હાર્દિક સ્વાગત છે.\n\n"
        f"લગ્નમાં આપના પરિવારમાંથી કોણ કોણ પધારવાના છો તેની વિગત આપવા માટે નીચે આપેલી લિંક પર ક્લિક કરશો:\n\n"
        f"{chat_url}\n\n"
        f"(નોંધ: જો લિંક વાદળી/ક્લિક ન થાય, તો આ નંબર સેવ કરો અથવા આ મેસેજ પર 'હા' લખીને મોકલો, જેથી લિંક ખૂલી જશે.)\n\n"
        f"આપના સ્નેહીજનો તરફથી"
    )
    
    clean_phone = clean_phone_10(contact["phone"])
    wa_direct_url = f"https://wa.me/91{clean_phone}?text={urllib.parse.quote(invite_text)}"
    
    res, code = send_baileys_message(clean_phone, invite_text)
    if code in [200, 201] and res.get("success"):
        return jsonify({
            "success": True,
            "message": f"Invitation link sent to {guest_name} ({clean_phone}) on WhatsApp!",
            "wa_direct_url": wa_direct_url,
            "response": res
        }), 200
    else:
        err_msg = res.get("error", "WhatsApp service unavailable or not linked.")
        not_conn = res.get("not_connected", code == 503)
        return jsonify({
            "success": False,
            "error": err_msg,
            "not_connected": not_conn,
            "wa_direct_url": wa_direct_url,
            "response": res
        }), 200

@app.route("/api/dispatch-all-parents", methods=["POST"])
@login_required
def api_dispatch_all_parents():
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    if not wedding:
        return jsonify({"success": False, "error": "No wedding found."}), 404
        
    wid = wedding["id"]
    parents = get_parent_sessions(wid)
    base_url = get_base_url()
    groom = wedding.get("groom_name", "").strip() or "વરરાજા"
    bride = wedding.get("bride_name", "").strip() or "કન્યા"
    
    dispatched = []
    errors = []
    
    for p in parents:
        p_phone = p.get("phone", "").strip()
        p_token = p.get("token", "")
        p_name = p.get("name", "").strip()
        p_role = p.get("role", "FATHER")
        
        if p_phone and p_token and p_name:
            role_gu = "પિતાશ્રી (પપ્પા)" if p_role == "FATHER" else "માતાશ્રી (મમ્મી)"
            side_gu = "પિતા પક્ષ" if p_role == "FATHER" else "માતા પક્ષ"
            collector_url = f"{base_url}/collector/{p_token}"
            collector_msg = (
                f"💍 જય શ્રી કૃષ્ણ પૂજ્ય {p_name} {role_gu}! 🙏\n\n"
                f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા અમારો AI સહાયક તૈયાર છે.\n\n"
                f"આપ ફક્ત નીચે આપેલી લિંક પર ક્લિક કરીને આપના સંબંધીઓ (ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો) ના નામ જણાવશો:\n\n"
                f"{collector_url}\n\n"
                f"(નોંધ: જો લિંક વાદળી/ક્લિક ન થાય, તો આ નંબર સેવ કરો અથવા આ મેસેજ પર 'હા' લખીને મોકલો, જેથી લિંક ખૂલી જશે.)\n\n"
                f"આપ જે નામ આપશો એ આપોઆપ લગ્નના ગેસ્ટ લિસ્ટમાં ગોઠવાઈ જશે! 🌸"
            )
            res, code = send_baileys_message(p_phone, collector_msg)
            if code in [200, 201] and res.get("success"):
                dispatched.append(p_name)
            else:
                errors.append(f"{p_name}: {res.get('error', 'Failed')}")
                
    return jsonify({
        "success": len(dispatched) > 0,
        "dispatched": dispatched,
        "errors": errors
    })

@app.route("/api/update-guest-family/<int:contact_id>", methods=["POST"])
@login_required
def api_update_guest_family(contact_id):
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    if not wedding:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    payload = request.get_json() or {}
    new_headcount = update_guest_family_db(contact_id, payload)
    return jsonify({
        "success": True,
        "message": "Family details updated successfully",
        "headcount": new_headcount
    })

@app.route("/api/whatsapp-status")
@login_required
def api_whatsapp_status():
    ensure_baileys_running()
    b_res, code = call_baileys("/status")
    status = b_res.get("status", "offline")
    qr = b_res.get("qr")
    raw_phone = b_res.get("user", {}).get("id", "") if b_res.get("user") else ""
    phone = format_whatsapp_phone(raw_phone)
    return jsonify({
        "status": status,
        "connected": status == "connected",
        "qr": qr,
        "phone": phone
    })

@app.route("/api/whatsapp-disconnect", methods=["POST"])
@login_required
def api_whatsapp_disconnect():
    b_res, code = call_baileys("/disconnect", method="POST")
    return jsonify(b_res), code

@app.route("/api/whatsapp-refresh-qr", methods=["POST"])
@login_required
def api_whatsapp_refresh_qr():
    ensure_baileys_running()
    b_res, code = call_baileys("/refresh-qr", method="POST")
    return jsonify(b_res), code

@app.route("/api/ai-status")
@login_required
def api_ai_status():
    key = get_gemini_api_key()
    return jsonify({
        "configured": bool(key),
        "masked_key": f"{key[:5]}...{key[-4:]}" if len(key) > 9 else ("Active" if key else "")
    })

@app.route("/api/save-gemini-key", methods=["POST"])
@login_required
def api_save_gemini_key():
    data = request.get_json() or {}
    key = data.get("key", "").strip()
    set_gemini_api_key(key)
    has_k = bool(get_gemini_api_key())
    return jsonify({"success": True, "has_key": has_k})

@app.route("/api/export")
@login_required
def api_export():
    user_id = session.get("user_id")
    wedding = get_wedding_by_user(user_id)
    wedding_id = wedding["id"] if wedding else None
    
    filename = "Wedding_Guest_List_Master.xlsx"
    output_path = os.path.join(os.path.dirname(__file__), filename)
    export_guest_list_excel(output_path=output_path, base_url=get_base_url(), wedding_id=wedding_id)
    return send_file(output_path, as_attachment=True, download_name=filename)

# -------------------------------------------------------------
# BAILEYS WHATSAPP BOT WEBHOOK
# -------------------------------------------------------------
@app.route("/api/baileys-webhook", methods=["POST"])
def baileys_webhook():
    payload = request.get_json() or {}
    sender = payload.get("sender", "").strip()
    raw_jid = payload.get("raw_jid", "").strip()
    participant = payload.get("participant", "").strip()
    message = payload.get("message", "").strip()
    
    if not message:
        return jsonify({"success": False, "error": "Missing message"}), 400
        
    candidate_digits = []
    for s in [sender, raw_jid, participant]:
        digs = "".join(filter(str.isdigit, s))
        if len(digs) >= 10:
            candidate_digits.append(digs[-10:])
            
    conn = get_db()
    c = conn.cursor()
    
    # 1. Match against parent sessions
    parent_token = None
    for cand in candidate_digits:
        c.execute("SELECT token FROM parent_sessions WHERE phone LIKE ? LIMIT 1", (f"%{cand}%",))
        row = c.fetchone()
        if row:
            parent_token = row["token"]
            break
            
    if parent_token:
        conn.close()
        result = process_parent_collector_message(parent_token, message)
        return jsonify({
            "success": True,
            "auto_send": True,
            "type": "parent_collector",
            "reply": result.get("reply", "ધન્યવાદ! વિગત નોંધી લીધી છે.")
        })
        
    # 2. Match against guest contacts
    contact_token = None
    for cand in candidate_digits:
        c.execute("SELECT token FROM contacts WHERE phone LIKE ? LIMIT 1", (f"%{cand}%",))
        row = c.fetchone()
        if row:
            contact_token = row["token"]
            break
            
    conn.close()
    
    if contact_token:
        result = process_chat_message(contact_token, message)
        return jsonify({
            "success": True,
            "auto_send": True,
            "type": "guest_rsvp",
            "reply": result.get("bot_reply", "ધન્યવાદ!")
        })
        
    # 3. Unknown sender: DO NOT auto-reply to personal WhatsApp chats!
    # Only reply if the message explicitly contains wedding/rsvp keywords
    low_msg = message.lower()
    wedding_trigger_keywords = ["rsvp", "લગ્ન", "wedding", "invitation", "nimantran", "કંકોત્રી", "kankotri"]
    if any(k in low_msg for k in wedding_trigger_keywords):
        fallback_msg = (
            f"💍 જય શ્રી કૃષ્ણ! 🙏\n\n"
            f"આ લગ્ન પ્રસંગનો ડિજિટલ સહાયક છે.\n"
            f"આપનું શુભ નામ અને પરિવારની વિગત જણાવશો જેથી અમે યાદીમાં નોંધી શકીએ."
        )
        return jsonify({
            "success": True,
            "auto_send": True,
            "type": "wedding_inquiry",
            "reply": fallback_msg
        })

    # For all other personal chats, stay silent and do not auto-reply
    return jsonify({
        "success": True,
        "auto_send": False,
        "type": "personal_message_ignored",
        "message": "Sender is neither a parent nor an invited guest. Skipped auto-reply to protect personal chat privacy."
    })

if __name__ == "__main__":
    print("=============================================================")
    print("[*] Gujarati Wedding Guest List & WhatsApp Concierge System")
    print("[*] Authentication: http://127.0.0.1:5000/login")
    print("[*] Setup Wizard: http://127.0.0.1:5000/setup")
    print("[*] Host Dashboard: http://127.0.0.1:5000/dashboard")
    print("=============================================================")
    app.run(host="127.0.0.1", port=5000, debug=True)
