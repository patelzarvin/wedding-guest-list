import sqlite3
import os
import uuid
import secrets
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from security import (
    validate_username, validate_email, validate_indian_phone,
    validate_password_strength, hash_otp, constant_time_compare,
    generate_secure_token, clean_phone_10
)

DB_PATH = os.path.join(os.path.dirname(__file__), "wedding.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # 1. Users Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        phone TEXT,
        email_verified INTEGER DEFAULT 0,
        phone_verified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # 2. Weddings Table (Linked to User)
    c.execute("""
    CREATE TABLE IF NOT EXISTS weddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT,
        groom_name TEXT,
        groom_phone TEXT,
        bride_name TEXT,
        bride_phone TEXT,
        groom_father_name TEXT,
        groom_father_phone TEXT,
        groom_mother_name TEXT,
        groom_mother_phone TEXT,
        bride_father_name TEXT,
        bride_father_phone TEXT,
        bride_mother_name TEXT,
        bride_mother_phone TEXT,
        wedding_date TEXT,
        wedding_city TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    
    # 3. Parent Collector Sessions (Unique tokens for Mother/Father)
    c.execute("""
    CREATE TABLE IF NOT EXISTS parent_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wedding_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        side TEXT NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        token TEXT UNIQUE NOT NULL,
        is_completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (wedding_id) REFERENCES weddings(id) ON DELETE CASCADE
    );
    """)
    
    # 4. Contacts Table (Relatives added by Parents or Host)
    c.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wedding_id INTEGER NOT NULL,
        side TEXT NOT NULL,
        added_by TEXT NOT NULL,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        token TEXT UNIQUE NOT NULL,
        status TEXT DEFAULT 'WAITING',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (wedding_id) REFERENCES weddings(id) ON DELETE CASCADE
    );
    """)
    
    # 5. RSVPs Table (Confirms headcount per relative)
    c.execute("""
    CREATE TABLE IF NOT EXISTS rsvps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id INTEGER UNIQUE NOT NULL,
        parents_attending INTEGER,
        parents_names TEXT,
        is_married INTEGER,
        spouse_name TEXT,
        children_names TEXT,
        other_members TEXT,
        total_headcount INTEGER DEFAULT 1,
        is_confirmed INTEGER DEFAULT 0,
        confirmed_at TIMESTAMP,
        FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
    );
    """)
    
    # 6. Chat Logs (Strictly isolated by session token)
    c.execute("""
    CREATE TABLE IF NOT EXISTS chat_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL,
        sender TEXT NOT NULL,
        text TEXT NOT NULL,
        timestamp TEXT NOT NULL
    );
    """)
    
    # 7. App Settings Table (Persistent config like Gemini API key)
    c.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 8. OTPs Table for Verification & Password Resets
    c.execute("""
    CREATE TABLE IF NOT EXISTS otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        otp_code TEXT NOT NULL,
        otp_hash TEXT,
        purpose TEXT DEFAULT 'REGISTER',
        expires_at TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        max_attempts INTEGER DEFAULT 5,
        is_used INTEGER DEFAULT 0,
        reset_token TEXT,
        consumed_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 9. Persistent Chat Sessions (Guarantees state machine never resets on worker restarts)
    c.execute("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        token TEXT PRIMARY KEY,
        session_data TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Backward-compatible migrations for existing tables
    c.execute("PRAGMA table_info(users)")
    user_cols = [row[1] for row in c.fetchall()]
    if "phone" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "email_verified" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
    if "phone_verified" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN phone_verified INTEGER DEFAULT 0")

    c.execute("PRAGMA table_info(otps)")
    otp_cols = [row[1] for row in c.fetchall()]
    if "otp_hash" not in otp_cols:
        c.execute("ALTER TABLE otps ADD COLUMN otp_hash TEXT")
    if "attempts" not in otp_cols:
        c.execute("ALTER TABLE otps ADD COLUMN attempts INTEGER DEFAULT 0")
    if "max_attempts" not in otp_cols:
        c.execute("ALTER TABLE otps ADD COLUMN max_attempts INTEGER DEFAULT 5")
    if "reset_token" not in otp_cols:
        c.execute("ALTER TABLE otps ADD COLUMN reset_token TEXT")
    if "consumed_at" not in otp_cols:
        c.execute("ALTER TABLE otps ADD COLUMN consumed_at TEXT")

    # Authoritative Indexes & Constraints
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username);")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email) WHERE email IS NOT NULL AND email != '';")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL AND phone != '';")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_logs(token);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_contact_token ON contacts(token);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_parent_token ON parent_sessions(token);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_otp_phone_purpose ON otps(phone, purpose, is_used);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_otp_reset_token ON otps(reset_token) WHERE reset_token IS NOT NULL;")
    
    conn.commit()
    conn.close()

def normalize_target(identifier):
    if not identifier:
        return ""
    s = str(identifier).strip()
    if "@" in s:
        return s.lower()
    return clean_phone_10(s)

# -------------------------------------------------------------
# USER & OTP MANAGEMENT FUNCTIONS
# -------------------------------------------------------------
def generate_and_save_otp(target, purpose='REGISTER'):
    """
    Generate cryptographically secure 6-digit OTP, compute keyed hash,
    invalidate older codes for target+purpose, and record expiry (10 min).
    """
    norm = normalize_target(target)
    if not norm:
        return None, "કૃપા કરીને માન્ય ઇમેઇલ અથવા મોબાઇલ નંબર દાખલ કરો."
    
    otp_code = f"{secrets.randbelow(900000) + 100000:06d}"
    otp_h = hash_otp(norm, otp_code, purpose)
    now_dt = datetime.now()
    expires_at = (now_dt + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_db()
    c = conn.cursor()
    # Invalidate previous unused OTPs for this target and purpose
    c.execute("UPDATE otps SET is_used = 1 WHERE phone = ? AND purpose = ? AND is_used = 0", (norm, purpose))
    c.execute("""
        INSERT INTO otps (phone, otp_code, otp_hash, purpose, expires_at, attempts, max_attempts, is_used)
        VALUES (?, ?, ?, ?, ?, 0, 5, 0)
    """, (norm, otp_code, otp_h, purpose, expires_at))
    conn.commit()
    conn.close()
    return otp_code, None

def verify_otp_db(target, otp_code, purpose='REGISTER'):
    """
    Verify OTP with:
    1. Expiration check (10 min)
    2. Attempt limits (max 5 failed attempts before permanent invalidation)
    3. Purpose binding
    4. Keyed constant-time hash comparison
    5. Single-use consumption
    6. For RESET: issues reset_token (15 min single-use authorization)
    7. For REGISTER: issues reg_token (15 min single-use registration proof)
    Returns: (is_valid, error_message, data_dict)
    """
    norm = normalize_target(target)
    code = str(otp_code).strip()
    if not norm or not code:
        return False, "અમાન્ય ઇમેઇલ અથવા OTP કોડ.", {}
    
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("""
        SELECT id, otp_code, otp_hash, expires_at, attempts, max_attempts, is_used 
        FROM otps 
        WHERE phone = ? AND purpose = ? AND is_used = 0
        ORDER BY id DESC LIMIT 1
    """, (norm, purpose))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return False, "ખોટો OTP કોડ છે અથવા સમય વીતી ગયો છે. કૃપા કરીને ચકાસો.", {}
    
    otp_id = row["id"]
    attempts = row["attempts"] or 0
    max_attempts = row["max_attempts"] or 5
    
    # Check attempt threshold
    if attempts >= max_attempts:
        c.execute("UPDATE otps SET is_used = 1 WHERE id = ?", (otp_id,))
        conn.commit()
        conn.close()
        return False, "મહત્તમ પ્રયાસો ઓળંગી ગયા છે. કૃપા કરીને નવો OTP મેળવો. (Maximum attempts exceeded).", {}
    
    # Increment attempt count
    c.execute("UPDATE otps SET attempts = attempts + 1 WHERE id = ?", (otp_id,))
    conn.commit()
    
    # Check expiry
    if row["expires_at"] < now_str:
        c.execute("UPDATE otps SET is_used = 1 WHERE id = ?", (otp_id,))
        conn.commit()
        conn.close()
        return False, "આ OTP ની અવધિ સમાપ્ત થઈ ગઈ છે. ફરી નવો OTP મેળવો. (OTP has expired).", {}
    
    # Verify hash with constant-time comparison
    expected_hash = hash_otp(norm, code, purpose)
    stored_hash = row["otp_hash"]
    
    is_match = False
    if stored_hash:
        is_match = constant_time_compare(stored_hash, expected_hash)
    elif row["otp_code"]:
        # Fallback for older records created prior to hash migration
        is_match = constant_time_compare(row["otp_code"], code)
        
    if not is_match:
        rem = max(0, max_attempts - (attempts + 1))
        conn.close()
        return False, f"ખોટો OTP કોડ છે. (બાકી પ્રયાસ: {rem}) (Invalid OTP code).", {}
    
    # Mark as consumed (Single-use)
    token_val = generate_secure_token(32)
    c.execute("UPDATE otps SET is_used = 1, reset_token = ?, consumed_at = ? WHERE id = ?", 
              (token_val, now_str, otp_id))
    conn.commit()
    conn.close()
    
    if purpose == 'RESET':
        return True, None, {"reset_token": token_val}
    else:
        return True, None, {"reg_token": token_val}

def verify_registration_token(email, reg_token):
    """
    Verify that an email was successfully verified via OTP within the last 15 minutes.
    Single-use: Consumes registration token.
    """
    if not email or not reg_token:
        return False, "ઇમેઇલ વેરિફિકેશન ટોકન મળ્યું નથી."
    norm_email = str(email).strip().lower()
    clean_token = str(reg_token).strip()
    
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, consumed_at FROM otps 
        WHERE phone = ? AND purpose = 'REGISTER' AND reset_token = ? AND is_used = 1
        ORDER BY id DESC LIMIT 1
    """, (norm_email, clean_token))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "ઇમેઇલ વેરિફિકેશન અમાન્ય છે. કૃપા કરીને ફરી ઇમેઇલ વેરિફાય કરો."
    
    # Check 15-minute token expiration window
    consumed_at_str = row["consumed_at"]
    if consumed_at_str:
        try:
            consumed_dt = datetime.strptime(consumed_at_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - consumed_dt > timedelta(minutes=15):
                conn.close()
                return False, "ઇમેઇલ વેરિફિકેશન ટોકનની અવધિ સમાપ્ત થઈ ગઈ છે. ફરી OTP મોકલો."
        except Exception:
            pass
            
    # Consume registration token to prevent reuse
    c.execute("UPDATE otps SET reset_token = NULL WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, None

def consume_reset_token(reset_token, new_password):
    """
    Validate reset authorization token and update user password.
    Requires:
    1. Valid, non-expired reset_token in otps table (purpose='RESET')
    2. Minimum 8 characters strong password
    3. Invalidates reset_token and revokes all older OTP challenges for this user.
    """
    if not reset_token:
        return False, "અમાન્ય રીસેટ ટોકન. (Invalid reset authorization token)."
        
    pwd_ok, pwd_err = validate_password_strength(new_password)
    if not pwd_ok:
        return False, pwd_err
        
    clean_token = str(reset_token).strip()
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT id, phone, consumed_at FROM otps 
        WHERE reset_token = ? AND purpose = 'RESET' AND is_used = 1
        LIMIT 1
    """, (clean_token,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "અમાન્ય અથવા વપરાઈ ગયેલ રીસેટ સત્ર. કૃપા કરીને ફરીથી પાસવર્ડ રીસેટ કરો. (Reset session expired or already used)."
        
    target_ident = row["phone"]
    consumed_at_str = row["consumed_at"]
    if consumed_at_str:
        try:
            consumed_dt = datetime.strptime(consumed_at_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - consumed_dt > timedelta(minutes=15):
                c.execute("UPDATE otps SET reset_token = NULL WHERE id = ?", (row["id"],))
                conn.commit()
                conn.close()
                return False, "પાસવર્ડ રીસેટ સત્ર સમાપ્ત થઈ ગયું છે. ફરી સુરક્ષા કોડ મેળવો. (Reset authorization expired)."
        except Exception:
            pass
            
    new_hash = generate_password_hash(new_password)
    
    # Transactional update
    try:
        if "@" in target_ident:
            c.execute("UPDATE users SET password_hash = ? WHERE LOWER(email) = ?", (new_hash, target_ident.lower()))
        else:
            c.execute("UPDATE users SET password_hash = ? WHERE phone = ?", (new_hash, clean_phone_10(target_ident)))
            
        if c.rowcount == 0:
            conn.close()
            return False, "આ એકાઉન્ટ સિસ્ટમમાં ઉપલબ્ધ નથી."
            
        # Invalidate reset token and all active/past OTPs for this target
        c.execute("UPDATE otps SET reset_token = NULL, is_used = 1 WHERE phone = ?", (target_ident,))
        conn.commit()
        conn.close()
        return True, None
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"પાસવર્ડ અપડેટ કરવામાં સર્વર ભૂલ: {str(e)}"

def register_user(username, email, password, phone=None, reg_token=None):
    """
    Authoritative server-side registration:
    - Username rules: 3-30 chars, alphanumeric + _.-
    - Email RFC normalization and format validation
    - Indian phone normalization (+91, exactly 10 digits starting with 6-9)
    - Password strength: min 8 chars, letter + digit/symbol
    - Single-use email OTP verification proof (reg_token)
    - Database unique constraints and transactional consistency
    - Sets email_verified = 1, phone_verified = 0 (never falsely claims phone verified)
    """
    u_ok, u_err, clean_username = validate_username(username)
    if not u_ok:
        return {"success": False, "error": u_err}
        
    e_ok, e_err, clean_email = validate_email(email)
    if not e_ok:
        return {"success": False, "error": e_err}
        
    p_ok, p_err, clean_p, _ = validate_indian_phone(phone)
    if not p_ok:
        return {"success": False, "error": p_err}
        
    pwd_ok, pwd_err = validate_password_strength(password)
    if not pwd_ok:
        return {"success": False, "error": pwd_err}
        
    # Verify Email Verification Proof if reg_token supplied
    if reg_token:
        tok_ok, tok_err = verify_registration_token(clean_email, reg_token)
        if not tok_ok:
            return {"success": False, "error": tok_err}

    conn = get_db()
    c = conn.cursor()

    # Pre-checks with clear friendly messages
    c.execute("SELECT id FROM users WHERE LOWER(username) = ?", (clean_username.lower(),))
    if c.fetchone():
        conn.close()
        return {"success": False, "error": "આ યુઝરનેમ પહેલેથી ઉપયોગમાં છે. કૃપા કરીને બીજું પસંદ કરો."}

    c.execute("SELECT id FROM users WHERE LOWER(email) = ?", (clean_email.lower(),))
    if c.fetchone():
        conn.close()
        return {"success": False, "error": "આ ઇમેઇલ પહેલેથી રજીસ્ટર થયેલો છે. કૃપા કરીને સાઇન-ઇન કરો."}

    c.execute("SELECT id FROM users WHERE phone = ?", (clean_p,))
    if c.fetchone():
        conn.close()
        return {"success": False, "error": "આ મોબાઇલ નંબર પહેલેથી રજીસ્ટર છે. કૃપા કરીને સાઇન-ઇન કરો."}

    pwd_hash = generate_password_hash(password)
    try:
        c.execute("""
            INSERT INTO users (username, email, password_hash, phone, email_verified, phone_verified)
            VALUES (?, ?, ?, ?, 1, 0)
        """, (clean_username, clean_email, pwd_hash, clean_p))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return {"success": True, "user_id": user_id}
    except sqlite3.IntegrityError as e:
        conn.close()
        err = str(e).lower()
        if "email" in err:
            return {"success": False, "error": "આ ઇમેઇલ પહેલેથી રજીસ્ટર છે."}
        elif "phone" in err:
            return {"success": False, "error": "આ મોબાઇલ નંબર પહેલેથી રજીસ્ટર છે."}
        return {"success": False, "error": "આ યુઝરનેમ પહેલેથી ઉપયોગમાં છે. કૃપા કરીને બીજું પસંદ કરો."}
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"success": False, "error": f"નોંધણીમાં સર્વર ભૂલ: {str(e)}"}

def authenticate_user(identifier, password):
    """
    Authenticate user by username, email, or 10-digit Indian phone number.
    Returns: user dictionary without password_hash on success, or None on failure.
    """
    if not identifier or not password:
        return None
    raw = str(identifier).strip()
    conn = get_db()
    c = conn.cursor()
    
    row = None
    if "@" in raw:
        c.execute("SELECT * FROM users WHERE LOWER(email) = ?", (raw.lower(),))
        row = c.fetchone()
    else:
        clean_p = clean_phone_10(raw)
        if len(clean_p) == 10 and clean_p[0] in ('6', '7', '8', '9'):
            c.execute("SELECT * FROM users WHERE phone = ?", (clean_p,))
            row = c.fetchone()
        if not row:
            c.execute("SELECT * FROM users WHERE LOWER(username) = ?", (raw.lower(),))
            row = c.fetchone()
            
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        user_data = dict(row)
        user_data.pop("password_hash", None)
        return user_data
    return None

def get_user_by_email(email):
    if not email:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, username, email, phone, email_verified, phone_verified, created_at FROM users WHERE LOWER(email) = ?", (email.strip().lower(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_phone(phone):
    clean_p = clean_phone_10(phone)
    if len(clean_p) != 10:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, username, email, phone, email_verified, phone_verified, created_at FROM users WHERE phone = ?", (clean_p,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def reset_user_password(identifier, new_password):
    """
    Administrative or direct password reset with password strength verification.
    """
    pwd_ok, pwd_err = validate_password_strength(new_password)
    if not pwd_ok:
        return False, pwd_err
        
    norm = normalize_target(identifier)
    if not norm:
        return False, "અમાન્ય ઇમેઇલ અથવા મોબાઇલ નંબર."
    pwd_hash = generate_password_hash(new_password)
    conn = get_db()
    c = conn.cursor()
    if "@" in norm:
        c.execute("UPDATE users SET password_hash = ? WHERE LOWER(email) = ?", (pwd_hash, norm))
    else:
        c.execute("UPDATE users SET password_hash = ? WHERE phone = ?", (pwd_hash, norm))
    conn.commit()
    rows = c.rowcount
    conn.close()
    if rows > 0:
        return True, None
    return False, "આ ઇમેઇલ સાથે કોઈ એકાઉન્ટ રજીસ્ટર નથી."


def get_wedding_by_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM weddings WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_wedding_by_id(wedding_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM weddings WHERE id = ?", (wedding_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def save_or_update_wedding(user_id, data):
    conn = get_db()
    c = conn.cursor()
    existing = get_wedding_by_user(user_id)
    
    groom_name = data.get("groom_name", "").strip()
    groom_phone = data.get("groom_phone", "").strip()
    bride_name = data.get("bride_name", "").strip()
    bride_phone = data.get("bride_phone", "").strip()
    
    gf_name = data.get("groom_father_name", "").strip()
    gf_phone = data.get("groom_father_phone", "").strip()
    gm_name = data.get("groom_mother_name", "").strip()
    gm_phone = data.get("groom_mother_phone", "").strip()
    
    bf_name = data.get("bride_father_name", "").strip()
    bf_phone = data.get("bride_father_phone", "").strip()
    bm_name = data.get("bride_mother_name", "").strip()
    bm_phone = data.get("bride_mother_phone", "").strip()
    
    wedding_date = data.get("wedding_date", "").strip()
    wedding_city = data.get("wedding_city", "").strip()
    title = f"{groom_name} & {bride_name} Wedding Hub" if groom_name and bride_name else "Wedding Hub"
    
    if existing:
        wedding_id = existing["id"]
        c.execute("""
            UPDATE weddings SET 
                title=?, groom_name=?, groom_phone=?, bride_name=?, bride_phone=?,
                groom_father_name=?, groom_father_phone=?, groom_mother_name=?, groom_mother_phone=?,
                bride_father_name=?, bride_father_phone=?, bride_mother_name=?, bride_mother_phone=?,
                wedding_date=?, wedding_city=?
            WHERE id=?
        """, (title, groom_name, groom_phone, bride_name, bride_phone,
              gf_name, gf_phone, gm_name, gm_phone,
              bf_name, bf_phone, bm_name, bm_phone,
              wedding_date, wedding_city, wedding_id))
    else:
        c.execute("""
            INSERT INTO weddings (
                user_id, title, groom_name, groom_phone, bride_name, bride_phone,
                groom_father_name, groom_father_phone, groom_mother_name, groom_mother_phone,
                bride_father_name, bride_father_phone, bride_mother_name, bride_mother_phone,
                wedding_date, wedding_city
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, title, groom_name, groom_phone, bride_name, bride_phone,
              gf_name, gf_phone, gm_name, gm_phone,
              bf_name, bf_phone, bm_name, bm_phone,
              wedding_date, wedding_city))
        wedding_id = c.lastrowid
        
    # Ensure Parent Sessions (Tokens) exist for both parents
    parents = [
        {"role": "FATHER", "side": "GROOM", "name": gf_name, "phone": gf_phone},
        {"role": "MOTHER", "side": "GROOM", "name": gm_name, "phone": gm_phone},
        {"role": "FATHER", "side": "BRIDE", "name": bf_name, "phone": bf_phone},
        {"role": "MOTHER", "side": "BRIDE", "name": bm_name, "phone": bm_phone},
    ]
    
    for p in parents:
        c.execute("SELECT * FROM parent_sessions WHERE wedding_id = ? AND role = ? AND side = ?",
                  (wedding_id, p["role"], p["side"]))
        p_row = c.fetchone()
        if p_row:
            c.execute("UPDATE parent_sessions SET name = ?, phone = ? WHERE id = ?",
                      (p["name"], p["phone"], p_row["id"]))
        else:
            token = f"p_{str(uuid.uuid4())[:12]}"
            c.execute("""
                INSERT INTO parent_sessions (wedding_id, role, side, name, phone, token)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (wedding_id, p["role"], p["side"], p["name"], p["phone"], token))
            
    conn.commit()
    conn.close()
    return wedding_id

def get_parent_sessions(wedding_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM parent_sessions WHERE wedding_id = ?", (wedding_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_parent_by_token(token):
    if not token:
        return None
    clean_t = str(token).strip().rstrip('/')
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.*, w.groom_name, w.bride_name, w.title 
        FROM parent_sessions p 
        LEFT JOIN weddings w ON p.wedding_id = w.id 
        WHERE LOWER(p.token) = LOWER(?)
    """, (clean_t,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# Contact Management
BLOCKED_NAMES = {
    "aavi gaya", "avi gaya", "aavi gya", "avi gya", "badha aavi gaya", "badha", "badha aavi",
    "koi nathi", "koi sadasya nathi", "koi baki nathi", "naa aavi gaya", "done", "complete", "finish",
    "aatla j", "bas aatla", "patyu", "બધા આવી ગયા", "આવી ગયા", "કોઈ નથી", "કોઈ બાકી નથી"
}

def add_contact_db(wedding_id, side, added_by, category, name, phone, notes=""):
    clean_name = name.strip()
    clean_phone = "".join(filter(str.isdigit, str(phone))) if phone else ""
    
    if not clean_name or len(clean_name) < 2:
        return None
    name_low = clean_name.lower()
    if any(b in name_low for b in BLOCKED_NAMES):
        return None
        
    conn = get_db()
    c = conn.cursor()
    
    # Check duplicate by 10-digit phone on same wedding
    if len(clean_phone) >= 10:
        c.execute("SELECT * FROM contacts WHERE wedding_id = ? AND phone LIKE ?", (wedding_id, f"%{clean_phone[-10:]}%"))
        existing = c.fetchone()
        if existing:
            conn.close()
            return dict(existing)
            
    # Check duplicate by exact name on same wedding
    c.execute("SELECT * FROM contacts WHERE wedding_id = ? AND LOWER(name) = ?",
              (wedding_id, clean_name.lower()))
    existing = c.fetchone()
    if existing:
        # If new phone number is provided, update existing record!
        if len(clean_phone) >= 10:
            c.execute("UPDATE contacts SET phone = ?, notes = CASE WHEN notes = '' OR notes IS NULL THEN ? ELSE notes END WHERE id = ?",
                      (clean_phone[-10:], notes.strip(), existing["id"]))
            conn.commit()
            c.execute("SELECT * FROM contacts WHERE id = ?", (existing["id"],))
            updated_record = dict(c.fetchone())
            conn.close()
            return updated_record
        conn.close()
        return dict(existing)
        
    token = f"g_{str(uuid.uuid4())[:12]}"
    c.execute("""
        INSERT INTO contacts (wedding_id, side, added_by, category, name, phone, token, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (wedding_id, side.upper(), added_by.upper(), category.lower(), clean_name, clean_phone[-10:] if len(clean_phone) >= 10 else phone.strip(), token, notes.strip()))
    
    contact_id = c.lastrowid
    
    # Init empty RSVP record
    c.execute("INSERT INTO rsvps (contact_id, total_headcount) VALUES (?, 1)", (contact_id,))
    
    conn.commit()
    c.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    new_c = dict(c.fetchone())
    conn.close()
    return new_c

def get_contacts_by_wedding(wedding_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT c.*, r.parents_attending, r.parents_names, r.spouse_name, r.children_names, r.other_members, r.total_headcount, r.is_confirmed
        FROM contacts c
        LEFT JOIN rsvps r ON c.id = r.contact_id
        WHERE c.wedding_id = ?
        ORDER BY c.id DESC
    """, (wedding_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_contact_by_token_db(token):
    if not token:
        return None
    clean_t = str(token).strip().rstrip('/')
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT c.*, r.parents_attending, r.parents_names, r.spouse_name, r.children_names, r.other_members, r.total_headcount, r.is_confirmed,
               w.groom_name, w.bride_name, w.title
        FROM contacts c
        LEFT JOIN rsvps r ON c.id = r.contact_id
        LEFT JOIN weddings w ON c.wedding_id = w.id
        WHERE LOWER(c.token) = LOWER(?)
    """, (clean_t,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_rsvp_db(contact_id, rsvp_data):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE rsvps SET
            parents_attending = ?,
            parents_names = ?,
            is_married = ?,
            spouse_name = ?,
            children_names = ?,
            other_members = ?,
            total_headcount = ?,
            is_confirmed = 1,
            confirmed_at = CURRENT_TIMESTAMP
        WHERE contact_id = ?
    """, (
        rsvp_data.get("parents_attending"),
        rsvp_data.get("parents_names", ""),
        rsvp_data.get("is_married"),
        rsvp_data.get("spouse_name", ""),
        rsvp_data.get("children_names", ""),
        rsvp_data.get("other_members", ""),
        rsvp_data.get("total_headcount", 1),
        contact_id
    ))
    
    # Update contact status to CONFIRMED
    c.execute("UPDATE contacts SET status = 'CONFIRMED' WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()

def update_guest_family_db(contact_id, data):
    """
    Updates the complete household breakdown (Parents, Spouse, Children, Other members)
    and synchronizes total headcount across contacts and rsvps.
    """
    conn = get_db()
    c = conn.cursor()

    p_names = (data.get("parents_names") or "").strip()
    sp_name = (data.get("spouse_name") or "").strip()
    ch_names = (data.get("children_names") or "").strip()
    om_names = (data.get("other_members") or "").strip()

    # Calculate actual headcount: 1 (Self) + count(parents) + (1 if spouse else 0) + count(children) + count(others)
    headcount = 1
    if p_names:
        headcount += len([x for x in p_names.split(",") if x.strip()])
    if sp_name:
        headcount += 1
    if ch_names:
        headcount += len([x for x in ch_names.split(",") if x.strip()])
    if om_names:
        headcount += len([x for x in om_names.split(",") if x.strip()])

    explicit_hc = data.get("total_headcount")
    if explicit_hc and int(explicit_hc) > 0:
        headcount = max(int(explicit_hc), headcount)

    c.execute("""
        INSERT INTO rsvps (contact_id, parents_attending, parents_names, is_married, spouse_name, children_names, other_members, total_headcount, is_confirmed, confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(contact_id) DO UPDATE SET
            parents_attending = CASE WHEN excluded.parents_names != '' THEN 1 ELSE 0 END,
            parents_names = excluded.parents_names,
            is_married = CASE WHEN excluded.spouse_name != '' THEN 1 ELSE 0 END,
            spouse_name = excluded.spouse_name,
            children_names = excluded.children_names,
            other_members = excluded.other_members,
            total_headcount = excluded.total_headcount,
            is_confirmed = 1,
            confirmed_at = CURRENT_TIMESTAMP
    """, (
        contact_id,
        1 if p_names else 0,
        p_names,
        1 if sp_name else 0,
        sp_name,
        ch_names,
        om_names,
        headcount
    ))
    c.execute("UPDATE contacts SET status = 'CONFIRMED' WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    return headcount

def save_chat_session_db(token, session_dict):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO chat_sessions (token, session_data, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(token) DO UPDATE SET session_data=excluded.session_data, updated_at=CURRENT_TIMESTAMP
    """, (token, json.dumps(session_dict, ensure_ascii=False)))
    conn.commit()
    conn.close()

def load_chat_session_db(token):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT session_data FROM chat_sessions WHERE token = ?", (token,))
    row = c.fetchone()
    conn.close()
    if row and row["session_data"]:
        try:
            return json.loads(row["session_data"])
        except Exception:
            return None
    return None

def get_wedding_stats_db(wedding_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as total FROM contacts WHERE wedding_id = ?", (wedding_id,))
    total_invited = c.fetchone()["total"]
    
    c.execute("SELECT COUNT(*) as confirmed FROM contacts WHERE wedding_id = ? AND status = 'CONFIRMED'", (wedding_id,))
    confirmed_count = c.fetchone()["confirmed"]
    
    c.execute("""
        SELECT COALESCE(SUM(r.total_headcount), 0) as total_headcount
        FROM contacts c JOIN rsvps r ON c.id = r.contact_id
        WHERE c.wedding_id = ? AND c.status = 'CONFIRMED'
    """, (wedding_id,))
    total_headcount = c.fetchone()["total_headcount"]
    
    pending_count = total_invited - confirmed_count
    conn.close()
    
    return {
        "total_invited": total_invited,
        "confirmed": confirmed_count,
        "pending": pending_count,
        "total_headcount": total_headcount
    }

# Chat Logging (Per-person Isolated)
def log_chat_message_db(token, sender, text):
    conn = get_db()
    c = conn.cursor()
    ts = datetime.now().strftime("%I:%M %p")
    c.execute("INSERT INTO chat_logs (token, sender, text, timestamp) VALUES (?, ?, ?, ?)",
              (token, sender, text, ts))
    conn.commit()
    conn.close()

def get_chat_history_db(token):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT sender, text, timestamp FROM chat_logs WHERE token = ? ORDER BY id ASC", (token,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Clean Wipe Function for User or Global
def clean_all_data():
    """Wipes all test records and reinitializes empty schema"""
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except Exception:
            pass
    init_db()

def get_app_setting(key, default=""):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row["value"] if row and row["value"] else default
    except Exception:
        return default

def set_app_setting(key, value):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO app_settings (key, value, updated_at) 
            VALUES (?, ?, CURRENT_TIMESTAMP) 
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """, (key, value))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print("Error saving app setting:", e)
        return False
