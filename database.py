import sqlite3
import os
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

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
    
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_logs(token);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_contact_token ON contacts(token);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_parent_token ON parent_sessions(token);")
    
    conn.commit()
    conn.close()

# User Management Functions
def register_user(username, email, password):
    conn = get_db()
    c = conn.cursor()
    pwd_hash = generate_password_hash(password)
    try:
        c.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                  (username.strip(), email.strip().lower() if email else None, pwd_hash))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return {"success": True, "user_id": user_id}
    except sqlite3.IntegrityError:
        conn.close()
        return {"success": False, "error": "Username or email already exists."}

def authenticate_user(username, password):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ? OR email = ?", (username.strip(), username.strip().lower()))
    row = c.fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return dict(row)
    return None

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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT p.*, w.groom_name, w.bride_name, w.title FROM parent_sessions p JOIN weddings w ON p.wedding_id = w.id WHERE p.token = ?", (token,))
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
            
    # Check duplicate by exact name & category on same wedding
    c.execute("SELECT * FROM contacts WHERE wedding_id = ? AND category = ? AND LOWER(name) = ?",
              (wedding_id, category.lower(), clean_name.lower()))
    existing = c.fetchone()
    if existing:
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
        SELECT c.*, r.parents_attending, r.spouse_name, r.children_names, r.other_members, r.total_headcount, r.is_confirmed
        FROM contacts c
        LEFT JOIN rsvps r ON c.id = r.contact_id
        WHERE c.wedding_id = ?
        ORDER BY c.id DESC
    """, (wedding_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_contact_by_token_db(token):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT c.*, r.parents_attending, r.spouse_name, r.children_names, r.other_members, r.total_headcount, r.is_confirmed,
               w.groom_name, w.bride_name, w.title
        FROM contacts c
        LEFT JOIN rsvps r ON c.id = r.contact_id
        JOIN weddings w ON c.wedding_id = w.id
        WHERE c.token = ?
    """, (token,))
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
