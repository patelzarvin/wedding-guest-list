"""
Helper script to inspect all user accounts in wedding.db
Run: python show_users.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "wedding.db")

def show_users():
    if not os.path.exists(DB_PATH):
        print(f"[-] Database file not found at: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    try:
        c.execute("""
            SELECT id, username, email, phone, email_verified, phone_verified, created_at 
            FROM users 
            ORDER BY id ASC
        """)
        users = [dict(r) for r in c.fetchall()]
        print("=" * 70)
        print(f"DATABASE: {DB_PATH}")
        print(f"TOTAL REGISTERED ACCOUNTS: {len(users)}")
        print("=" * 70)
        if not users:
            print("No users registered in this local database yet.")
        else:
            for u in users:
                verified_str = "Verified" if u.get("email_verified") else "Unverified"
                print(f"ID #{u['id']:<3} | User: {u['username']:<15} | Email: {u.get('email', '-'):<25} | Phone: {u.get('phone', '-'):<12} | {verified_str} | Joined: {u.get('created_at', '-')}")
        print("=" * 70)
    except sqlite3.OperationalError as e:
        print("[-] Could not read users table:", e)
    finally:
        conn.close()

if __name__ == "__main__":
    show_users()
