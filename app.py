import os
import json
import re
from datetime import datetime
import urllib.request
import urllib.error
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for

from database import (
    get_db, init_db, register_user, authenticate_user as db_auth_user,
    get_wedding_by_user, get_wedding_by_id, save_or_update_wedding,
    get_parent_sessions, get_parent_by_token, add_contact_db,
    get_contacts_by_wedding, get_contact_by_token_db, update_rsvp_db,
    get_wedding_stats_db, log_chat_message_db, get_chat_history_db
)
from auth import login_required
from excel_export import export_guest_list_excel
from bot_engine import (
    process_chat_message, process_parent_collector_message,
    get_gemini_api_key, set_gemini_api_key, send_baileys_message
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "wedding_luxury_secret_key_2026_gj")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Ensure SQLite schema is ready
init_db()

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

BAILEYS_SERVICE_URL = os.environ.get("BAILEYS_SERVICE_URL", "http://127.0.0.1:5001")

def ensure_baileys_running():
    try:
        req = urllib.request.Request(f"{BAILEYS_SERVICE_URL}/status")
        with urllib.request.urlopen(req, timeout=1):
            return True
    except Exception:
        server_js = os.path.join(os.path.dirname(__file__), "whatsapp_service", "server.js")
        service_dir = os.path.join(os.path.dirname(__file__), "whatsapp_service")
        if os.path.exists(server_js):
            try:
                import subprocess
                subprocess.Popen(["node", "server.js"], cwd=service_dir, shell=True)
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

def get_base_url():
    env_base = os.environ.get("BASE_URL")
    if env_base:
        return env_base.rstrip('/')
    try:
        if request:
            proto = request.headers.get("X-Forwarded-Proto", request.scheme)
            host = request.headers.get("X-Forwarded-Host", request.host)
            return f"{proto}://{host}".rstrip('/')
    except Exception:
        pass
    return "http://127.0.0.1:5000"

def call_baileys(endpoint, method="GET", data=None):
    url = f"{BAILEYS_SERVICE_URL}{endpoint}"
    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if req_data else {}
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8")), response.status
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            return err_body, e.code
        except Exception:
            return {"error": str(e)}, e.code
    except Exception as e:
        ensure_baileys_running()
        return {"status": "offline", "error": str(e)}, 503

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
        
        user = db_auth_user(username, password)
        if user:
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
            return render_template(
                "login.html",
                active_tab="signin",
                error="અમાન્ય યુઝરનેમ અથવા પાસવર્ડ. કૃપા કરીને ફરી પ્રયાસ કરો. (Invalid username or password)."
            )
            
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", active_tab="signin", error=None)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        
        if len(username) < 3:
            return render_template("login.html", active_tab="register", error="Username must be at least 3 characters.")
        if len(password) < 6:
            return render_template("login.html", active_tab="register", error="Password must be at least 6 characters.")
            
        res = register_user(username, email, password)
        if res.get("success"):
            session["user_id"] = res["user_id"]
            session["username"] = username
            session["user_role"] = "MASTER"
            session["logged_in"] = True
            return redirect(url_for("setup_wizard"))
        else:
            return render_template("login.html", active_tab="register", error=res.get("error", "Registration failed."))
            
    return render_template("login.html", active_tab="register", error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

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
            "groom_phone": request.form.get("groom_phone", "").strip(),
            "groom_father_name": request.form.get("groom_father_name", "").strip(),
            "groom_father_phone": request.form.get("groom_father_phone", "").strip(),
            "groom_mother_name": request.form.get("groom_mother_name", "").strip(),
            "groom_mother_phone": request.form.get("groom_mother_phone", "").strip(),
            "bride_name": request.form.get("bride_name", "").strip(),
            "bride_phone": request.form.get("bride_phone", "").strip(),
            "bride_father_name": request.form.get("bride_father_name", "").strip(),
            "bride_father_phone": request.form.get("bride_father_phone", "").strip(),
            "bride_mother_name": request.form.get("bride_mother_name", "").strip(),
            "bride_mother_phone": request.form.get("bride_mother_phone", "").strip(),
            "wedding_date": request.form.get("wedding_date", "").strip(),
            "wedding_city": request.form.get("wedding_city", "").strip()
        }
        
        wedding_id = save_or_update_wedding(user_id, form_data)
        
        # 100% AUTOPILOT: Automatically dispatch parent collector links via Baileys WhatsApp
        parents = get_parent_sessions(wedding_id)
        base_url = get_base_url()
        groom = form_data.get("groom_name", "").strip() or "વરરાજા"
        bride = form_data.get("bride_name", "").strip() or "કન્યા"
        
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
                    f"{groom} અને {bride} ના લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા માટે અમારો AI આસિસ્ટન્ટ તૈયાર છે.\n\n"
                    f"આપ ફક્ત નીચે આપેલી લિંક પર ક્લિક કરીને આપના સંબંધીઓ (ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો) ના નામ જણાવશો:\n"
                    f"👉 {collector_url}\n\n"
                    f"આપ જે નામ આપશો એ આપોઆપ લગ્નના ગેસ્ટ લિસ્ટમાં ગોઠવાઈ જશે! 🌸"
                )
                try:
                    send_baileys_message(p_phone, collector_msg)
                except Exception as e:
                    print("Autopilot parent dispatch notice:", e)
                    
        return redirect(url_for("dashboard"))
        
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
        whatsapp_phone=whatsapp_phone
    )

# -------------------------------------------------------------
# PARENT COLLECTOR WEB CHATBOT (/collector/<token>)
# Strict Token-Based Isolation: No credentials needed for parents
# -------------------------------------------------------------
@app.route("/collector/<token>")
def parent_collector_view(token):
    parent = get_parent_by_token(token)
    if not parent:
        return "અમાન્ય અથવા એક્સપાયર થયેલી લિંક (Invalid or expired link).", 404
        
    history = get_chat_history_db(token)
    if not history:
        role_gu = "પિતાશ્રી (પપ્પા)" if parent["role"] == "FATHER" else "માતાશ્રી (મમ્મી)"
        side_gu = "પિતા પક્ષ (Father's Side)" if parent["role"] == "FATHER" else "માતા પક્ષ (Mother's Side)"
        groom = parent.get("groom_name", "").strip() or "વરરાજા"
        bride = parent.get("bride_name", "").strip() or "કન્યા"
        
        initial_greeting = (
            f"💍 જય શ્રી કૃષ્ણ પૂજ્ય {parent['name']} {role_gu}! 🙏\n\n"
            f"{groom} અને {bride} ના લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા માટે હું આપનો સહાયક છું.\n\n"
            f"આપ આપના સંબંધીઓ (આપના ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો વગેરે) ના નામ અને મોબાઈલ નંબર અહીં જણાવો.\n\n"
            f"દાખલા તરીકે:\n"
            f"• 'મારા ભાઈ રમેશભાઈ 9825012345'\n"
            f"• 'કાકા મહેશભાઈ 9824012345'\n\n"
            f"શરૂ કરવા માટે જણાવશો, આપના ભાઈઓ કે કાકા કોણ કોણ છે?"
        )
        log_chat_message_db(token, "bot", initial_greeting)
        history = get_chat_history_db(token)
        
    return render_template("parent_collector.html", parent=parent, history=history)

@app.route("/api/collector-chat/<token>", methods=["POST"])
def api_collector_chat(token):
    parent = get_parent_by_token(token)
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
def guest_chat_view(token):
    contact = get_contact_by_token_db(token)
    if not contact:
        return "અમાન્ય અથવા એક્સપાયર થયેલી લિંક (Invalid or expired link).", 404
        
    history = get_chat_history_db(token)
    if not history:
        groom = contact.get("groom_name", "").strip() or "વરરાજા"
        bride = contact.get("bride_name", "").strip() or "કન્યા"
        side_label = "વર પક્ષ" if contact.get("side") == "GROOM" else "કન્યા પક્ષ"
        
        initial_greeting = (
            f"💍 જય શ્રી કૃષ્ણ {contact['name']}! 🙏\n\n"
            f"{groom} અને {bride} ના શુભ લગ્ન પ્રસંગે આપનું અને આપના પરિવારનું હાર્દિક સ્વાગત છે ({side_label} ➔ {contact['category'].title()}).\n\n"
            f"લગ્નમાં આપના પરિવારમાંથી કોણ કોણ પધારી રહ્યું છે તેની વિગત માટે હું આપને માત્ર ૨ સરળ સવાલ પૂછીશ.\n\n"
            f"👉 ૧. શું આપના માતા-પિતા (વડીલો) આપની સાથે આવશે? (હા / ના)"
        )
        log_chat_message_db(token, "bot", initial_greeting)
        history = get_chat_history_db(token)
        
    return render_template("chat.html", contact=contact, history=history)

@app.route("/api/guest-chat/<token>", methods=["POST"])
def api_guest_chat(token):
    contact = get_contact_by_token_db(token)
    if not contact:
        return jsonify({"error": "Invalid token"}), 404
        
    payload = request.get_json() or {}
    message = payload.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
        
    result = process_chat_message(token, message)
    return jsonify(result)

# -------------------------------------------------------------
# AUTH-PROTECTED API ROUTES
# -------------------------------------------------------------
@app.route("/api/send-parent-link/<token>", methods=["POST"])
@login_required
def api_send_parent_link(token):
    parent = get_parent_by_token(token)
    if not parent or not parent.get("phone"):
        return jsonify({"success": False, "error": "Parent phone number is not configured."}), 400
        
    role_gu = "પિતાશ્રી (પપ્પા)" if parent["role"] == "FATHER" else "માતાશ્રી (મમ્મી)"
    side_gu = "પિતા પક્ષ" if parent["role"] == "FATHER" else "માતા પક્ષ"
    base_url = get_base_url()
    collector_url = f"{base_url}/collector/{token}"
    groom = parent.get("groom_name", "").strip() or "વરરાજા"
    bride = parent.get("bride_name", "").strip() or "કન્યા"
    
    collector_msg = (
        f"💍 જય શ્રી કૃષ્ણ પૂજ્ય {parent['name']} {role_gu}! 🙏\n\n"
        f"{groom} અને {bride} ના લગ્ન પ્રસંગ માટે આપના {side_gu} ના તમામ સંબંધીઓની યાદી તૈયાર કરવા માટે અમારો AI આસિસ્ટન્ટ તૈયાર છે.\n\n"
        f"આપ ફક્ત નીચે આપેલી લિંક પર ક્લિક કરીને આપના સંબંધીઓ (ભાઈઓ, બહેનો, કાકા, ફોઈ, મામા, માસી, મિત્રો) ના નામ જણાવશો:\n"
        f"👉 {collector_url}\n\n"
        f"આપ જે નામ આપશો એ આપોઆપ લગ્નના ગેસ્ટ લિસ્ટમાં ગોઠવાઈ જશે! 🌸"
    )
    
    res, code = send_baileys_message(parent["phone"], collector_msg)
    return jsonify({"success": code in [200, 201], "response": res}), 200

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
        
    # 3. Fallback greeting
    fallback_msg = (
        f"જય શ્રી કૃષ્ણ! 🙏\n"
        f"આ લગ્ન પ્રસંગનો ડિજિટલ સહાયક છે.\n"
        f"આપનું શુભ નામ અને પરિવારની વિગત જણાવશો જેથી અમે યાદીમાં નોંધી શકીએ."
    )
    return jsonify({
        "success": True,
        "auto_send": True,
        "type": "unknown",
        "reply": fallback_msg
    })

if __name__ == "__main__":
    print("=============================================================")
    print("[*] Gujarati Wedding Guest List & WhatsApp Concierge System")
    print("[*] Authentication: http://127.0.0.1:5000/login")
    print("[*] Setup Wizard: http://127.0.0.1:5000/setup")
    print("[*] Host Dashboard: http://127.0.0.1:5000/dashboard")
    print("=============================================================")
    app.run(host="127.0.0.1", port=5000, debug=True)
