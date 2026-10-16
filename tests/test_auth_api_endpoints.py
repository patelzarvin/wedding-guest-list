import os
import sys
import unittest
import tempfile
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database
import security
from app import app

class TestAuthApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix="_test_api.db")
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.test_db_path
        database.init_db()

        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()
        database.DB_PATH = self.orig_db_path
        try:
            os.close(self.test_db_fd)
            if os.path.exists(self.test_db_path):
                os.remove(self.test_db_path)
        except Exception:
            pass

    def test_01_unauthenticated_protected_routes(self):
        """Unauthenticated requests to /dashboard and /setup must redirect to /login."""
        r_dash = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(r_dash.status_code, 302)
        self.assertIn("/login", r_dash.headers.get("Location", ""))

        r_setup = self.client.get("/setup", follow_redirects=False)
        self.assertEqual(r_setup.status_code, 302)
        self.assertIn("/login", r_setup.headers.get("Location", ""))

    def test_02_send_otp_api_cooldown_enforcement(self):
        """Attempting to request a second OTP within 60s cooldown must return HTTP 429."""
        email = "cooldown@example.com"
        # 1st request
        r1 = self.client.post("/api/auth/send-otp", json={"email": email, "purpose": "REGISTER"})
        # Note: if email delivery is attempted in test, SMTP might fail or succeed depending on mock/env,
        # but let's test rate_limiter directly to verify 429
        client_ip = "127.0.0.1"
        security.rate_limiter.record_otp_request(client_ip, email)

        # 2nd immediate request within 60s
        r2 = self.client.post("/api/auth/send-otp", json={"email": email, "purpose": "REGISTER"})
        self.assertEqual(r2.status_code, 429)
        data = r2.get_json()
        self.assertFalse(data["success"])
        self.assertIn("સેકન્ડ રાહ જુઓ", data["error"])

    def test_03_reset_password_unknown_identifier_safe_response(self):
        """Requesting password recovery for unknown email returns safe generic response without account enumeration."""
        res = self.client.post("/api/auth/send-otp", json={
            "email": "unknown_random_person@example.com",
            "purpose": "RESET"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("જો આ ઇમેઇલ સાથે કોઈ એકાઉન્ટ રજીસ્ટર હશે", data["message"])

    def test_04_guest_link_idor_protection(self):
        """Cross-tenant attack on /api/send-guest-link/<token> must return 403 Forbidden."""
        # Host 1
        u1 = database.register_user("host_one", "host1@example.com", "Password#123", phone="9825111111")
        w1_id = database.save_or_update_wedding(u1["user_id"], {"groom_name": "G1", "bride_name": "B1"})
        contact1 = database.add_contact_db(w1_id, "GROOM", "FATHER", "family", "Relative One", "9825111112")
        tok1 = contact1["token"]

        # Host 2
        u2 = database.register_user("host_two", "host2@example.com", "Password#123", phone="9825222222")
        w2_id = database.save_or_update_wedding(u2["user_id"], {"groom_name": "G2", "bride_name": "B2"})

        # Log in as Host 2
        with self.client:
            self.client.post("/login", data={"username": "host_two", "password": "Password#123"})
            # Host 2 tries to trigger WhatsApp message for Host 1's relative
            res = self.client.post(f"/api/send-guest-link/{tok1}")
            self.assertEqual(res.status_code, 403)
            data = res.get_json()
            self.assertFalse(data["success"])
            self.assertIn("Unauthorized", data["error"])

if __name__ == "__main__":
    unittest.main()
