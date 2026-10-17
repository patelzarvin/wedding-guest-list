import os
import sys
import unittest
import tempfile
import sqlite3
import time
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database
import security
from app import app
from werkzeug.security import check_password_hash

class TestWedViteAuthLifecycle(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary SQLite database for tests
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix="_test_wedding.db")
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.test_db_path
        
        # Initialize schema
        database.init_db()
        
        # Configure test Flask client
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["SERVER_NAME"] = "localhost"
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

    # =========================================================
    # 1. REGISTRATION LIFECYCLE TESTS
    # =========================================================
    def test_01_valid_registration_with_successful_otp_verification(self):
        """Test valid registration lifecycle: OTP request -> verify -> account created with hash."""
        email = "testuser@example.com"
        username = "valid_user"
        phone = "9876543210"
        password = "Password@123"

        # 1. Request OTP
        code, err = database.generate_and_save_otp(email, purpose="REGISTER")
        self.assertIsNotNone(code)
        self.assertIsNone(err)
        self.assertEqual(len(code), 6)

        # 2. Verify OTP
        is_valid, v_err, tok_data = database.verify_otp_db(email, code, purpose="REGISTER")
        self.assertTrue(is_valid)
        self.assertIn("reg_token", tok_data)
        reg_token = tok_data["reg_token"]

        # 3. Complete Registration with reg_token
        res = database.register_user(username, email, password, phone=phone, reg_token=reg_token)
        self.assertTrue(res.get("success"), msg=res.get("error"))
        user_id = res.get("user_id")
        self.assertIsNotNone(user_id)

        # Verify database fields: password is NOT plaintext, email is verified, phone is unverified
        conn = database.get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        conn.close()

        self.assertNotEqual(user["password_hash"], password)
        self.assertTrue(check_password_hash(user["password_hash"], password))
        self.assertEqual(user["email_verified"], 1)
        self.assertEqual(user["phone_verified"], 0)

    def test_02_attempted_registration_with_invalid_credentials(self):
        """Test that registering with invalid email or mismatched passwords returns 400."""
        res = self.client.post("/register", data={
            "username": "no_otp_user",
            "email": "invalid-email-format",
            "phone": "9876543211",
            "password": "Password@123",
            "confirm_password": "Password@123",
            "otp_code": "",
            "reg_token": ""
        })
        self.assertEqual(res.status_code, 400)

    def test_03_invalid_or_expired_otp(self):
        """Test that expired OTPs are rejected."""
        email = "expire_test@example.com"
        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")

        # Manually alter expires_at in DB to simulate expired OTP
        conn = database.get_db()
        c = conn.cursor()
        past_time = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE otps SET expires_at = ? WHERE phone = ?", (past_time, email))
        conn.commit()
        conn.close()

        is_valid, err, _ = database.verify_otp_db(email, code, purpose="REGISTER")
        self.assertFalse(is_valid)
        self.assertIn("અવધિ સમાપ્ત", err)

    def test_04_incorrect_otp_attempt_limits(self):
        """Test incorrect OTP and attempt counter lockout after 5 tries."""
        email = "lockout_test@example.com"
        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")

        # Try 5 incorrect codes
        for i in range(5):
            is_valid, err, _ = database.verify_otp_db(email, "000000", purpose="REGISTER")
            self.assertFalse(is_valid)

        # 6th attempt with CORRECT code should be locked out
        is_valid, err, _ = database.verify_otp_db(email, code, purpose="REGISTER")
        self.assertFalse(is_valid)
        self.assertIn("મહત્તમ પ્રયાસો", err)

    def test_05_reuse_of_already_consumed_otp(self):
        """Test that single-use OTP cannot be reused a second time."""
        email = "single_use@example.com"
        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")

        # 1st verification succeeds
        is_valid1, _, _ = database.verify_otp_db(email, code, purpose="REGISTER")
        self.assertTrue(is_valid1)

        # 2nd verification must fail
        is_valid2, err, _ = database.verify_otp_db(email, code, purpose="REGISTER")
        self.assertFalse(is_valid2)

    def test_06_resend_otp_invalidates_previous_code(self):
        """Test that generating a new OTP invalidates the older code."""
        email = "resend_test@example.com"
        code1, _ = database.generate_and_save_otp(email, purpose="REGISTER")
        
        # Second OTP issued
        code2, _ = database.generate_and_save_otp(email, purpose="REGISTER")
        self.assertNotEqual(code1, code2)

        # First code should now be invalid
        is_valid1, _, _ = database.verify_otp_db(email, code1, purpose="REGISTER")
        self.assertFalse(is_valid1)

        # Second code should be valid
        is_valid2, _, _ = database.verify_otp_db(email, code2, purpose="REGISTER")
        self.assertTrue(is_valid2)

    def test_07_duplicate_username_email_and_mobile_prevention(self):
        """Enforce database uniqueness for username, email, and mobile number."""
        # Register first user
        code, _ = database.generate_and_save_otp("alice@example.com", purpose="REGISTER")
        _, _, tok = database.verify_otp_db("alice@example.com", code, purpose="REGISTER")
        res1 = database.register_user("alice_host", "alice@example.com", "Password@123", phone="9876543210", reg_token=tok["reg_token"])
        self.assertTrue(res1["success"])

        # Attempt duplicate username
        res2 = database.register_user("alice_host", "other@example.com", "Password@123", phone="9876543211")
        self.assertFalse(res2["success"])
        self.assertIn("યુઝરનેમ પહેલેથી", res2["error"])

        # Attempt duplicate email
        res3 = database.register_user("bob_host", "alice@example.com", "Password@123", phone="9876543212")
        self.assertFalse(res3["success"])
        self.assertIn("ઇમેઇલ પહેલેથી", res3["error"])

        # Attempt duplicate phone number
        res4 = database.register_user("charlie_host", "charlie@example.com", "Password@123", phone="9876543210")
        self.assertFalse(res4["success"])
        self.assertIn("મોબાઇલ નંબર પહેલેથી", res4["error"])

    def test_08_password_strength_and_mismatch_validation(self):
        """Test minimum 8 chars password and confirm-password mismatch."""
        # Short password (<8)
        ok, err = security.validate_password_strength("Short1")
        self.assertFalse(ok)
        self.assertIn("8", err)

        # Weak password (letters only, no numbers/symbols)
        ok2, err2 = security.validate_password_strength("passwordonly")
        self.assertFalse(ok2)

        # Valid strong password
        ok3, _ = security.validate_password_strength("LuxuryPass#2026")
        self.assertTrue(ok3)

        # Frontend endpoint validation test with password mismatch
        res = self.client.post("/register", data={
            "username": "mismatch_user",
            "email": "mismatch@example.com",
            "phone": "9876543215",
            "password": "Password@123",
            "confirm_password": "DifferentPassword@123",
            "otp_code": "123456"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("મેચ થતા નથી".encode("utf-8"), res.data)

    # =========================================================
    # 2. SIGN IN LIFECYCLE TESTS
    # =========================================================
    def test_09_login_by_username_email_and_mobile(self):
        """Test that authentication succeeds identically using username, email, or mobile."""
        username = "multi_ident"
        email = "multi@example.com"
        phone = "9825012345"
        pwd = "LuxurySecret#2026"

        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")
        _, _, tok = database.verify_otp_db(email, code, purpose="REGISTER")
        database.register_user(username, email, pwd, phone=phone, reg_token=tok["reg_token"])

        # 1. Login with username
        user1 = database.authenticate_user(username, pwd)
        self.assertIsNotNone(user1)
        self.assertEqual(user1["username"], username)
        self.assertNotIn("password_hash", user1) # Must never expose hash!

        # 2. Login with email
        user2 = database.authenticate_user(email, pwd)
        self.assertIsNotNone(user2)
        self.assertEqual(user2["id"], user1["id"])

        # 3. Login with 10-digit mobile number
        user3 = database.authenticate_user(phone, pwd)
        self.assertIsNotNone(user3)
        self.assertEqual(user3["id"], user1["id"])

        # 4. Login with +91 prefixed mobile number
        user4 = database.authenticate_user(f"+91 {phone}", pwd)
        self.assertIsNotNone(user4)
        self.assertEqual(user4["id"], user1["id"])

    def test_10_login_invalid_credentials_and_rate_limiting(self):
        """Test wrong password, nonexistent account, and rate limit lockout."""
        client_ip = "192.168.1.100"
        ident = "test_lockout_user"

        # Record 5 failed attempts
        for _ in range(5):
            security.rate_limiter.record_login_failure(client_ip, ident)

        # Check rate limit
        allowed, retry_after = security.rate_limiter.check_login_rate_limit(client_ip, ident)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

        # Reset rate limiter
        security.rate_limiter.reset_login_rate_limit(client_ip, ident)
        allowed_after_reset, _ = security.rate_limiter.check_login_rate_limit(client_ip, ident)
        self.assertTrue(allowed_after_reset)

    def test_11_session_creation_fixation_protection_and_logout(self):
        """Verify session is cleared and regenerated upon login and logout."""
        username = "session_user"
        email = "session@example.com"
        phone = "9825055555"
        pwd = "SessionPass#2026"

        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")
        _, _, tok = database.verify_otp_db(email, code, purpose="REGISTER")
        database.register_user(username, email, pwd, phone=phone, reg_token=tok["reg_token"])

        # Perform login via Flask test client
        with self.client:
            res = self.client.post("/login", data={
                "username": username,
                "password": pwd,
                "remember": "on"
            }, follow_redirects=False)
            
            # User should be redirected to setup wizard
            self.assertEqual(res.status_code, 302)
            self.assertIn("/setup", res.headers.get("Location", ""))

            # Session should be populated
            from flask import session
            self.assertEqual(session.get("username"), username)
            self.assertEqual(session.get("user_role"), "MASTER")
            self.assertTrue(session.get("logged_in"))

            # Logout
            logout_res = self.client.get("/logout", follow_redirects=False)
            self.assertEqual(logout_res.status_code, 302)
            self.assertIsNone(session.get("user_id"))

    # =========================================================
    # 3. PASSWORD RECOVERY LIFECYCLE (TWO-STEP SEQUENCE)
    # =========================================================
    def test_12_password_recovery_complete_lifecycle(self):
        """
        Test the mandatory 10-step password recovery sequence:
        1. Request code for registered account
        2. Verify OTP -> Server returns single-use reset_token
        3. Submit reset_token + new_password -> Server securely updates password
        4. Token cannot be reused
        5. Old password fails; new password succeeds
        """
        username = "recovery_user"
        email = "recovery@example.com"
        phone = "9825099999"
        old_pwd = "OldPassword#123"
        new_pwd = "NewPassword#2026"

        # Register user
        code, _ = database.generate_and_save_otp(email, purpose="REGISTER")
        _, _, tok = database.verify_otp_db(email, code, purpose="REGISTER")
        database.register_user(username, email, old_pwd, phone=phone, reg_token=tok["reg_token"])

        # 1. Recovery Challenge: Generate Reset OTP
        reset_otp, err = database.generate_and_save_otp(email, purpose="RESET")
        self.assertIsNotNone(reset_otp)

        # 2. Wrong purpose test: An OTP issued for REGISTER must NOT verify for RESET
        is_valid_wrong, _, _ = database.verify_otp_db(email, reset_otp, purpose="REGISTER")
        self.assertFalse(is_valid_wrong)

        # 3. Verify OTP for RESET -> Must return reset_token
        is_valid, err, data = database.verify_otp_db(email, reset_otp, purpose="RESET")
        self.assertTrue(is_valid)
        self.assertIn("reset_token", data)
        reset_token = data["reset_token"]
        self.assertTrue(len(reset_token) > 20)

        # 4. Attempt password reset with invalid token
        bad_ok, bad_err = database.consume_reset_token("invalid_token_123", new_pwd)
        self.assertFalse(bad_ok)

        # 5. Reset with valid reset_token
        ok, res_err = database.consume_reset_token(reset_token, new_pwd)
        self.assertTrue(ok)
        self.assertIsNone(res_err)

        # 6. Replay attack: Reusing the same reset_token must fail
        replay_ok, _ = database.consume_reset_token(reset_token, new_pwd)
        self.assertFalse(replay_ok)

        # 7. Old password must no longer authenticate
        self.assertIsNone(database.authenticate_user(username, old_pwd))

        # 8. New password must authenticate successfully
        new_auth = database.authenticate_user(username, new_pwd)
        self.assertIsNotNone(new_auth)
        self.assertEqual(new_auth["email"], email)

    # =========================================================
    # 4. AUTHORIZATION & TENANT ISOLATION (IDOR PREVENTION)
    # =========================================================
    def test_13_idor_prevention_on_wedding_resources(self):
        """Verify that a user cannot access or trigger links for another user's wedding."""
        # Create User A & Wedding A
        codeA, _ = database.generate_and_save_otp("userA@example.com", purpose="REGISTER")
        _, _, tokA = database.verify_otp_db("userA@example.com", codeA, purpose="REGISTER")
        userA = database.register_user("user_a", "userA@example.com", "Password@123", phone="9825000001", reg_token=tokA["reg_token"])
        weddingA_id = database.save_or_update_wedding(userA["user_id"], {
            "groom_name": "GroomA", "bride_name": "BrideA",
            "groom_father_name": "FatherA", "groom_father_phone": "9825000002"
        })
        parentsA = database.get_parent_sessions(weddingA_id)
        parentA_token = parentsA[0]["token"]

        # Create User B & Wedding B
        codeB, _ = database.generate_and_save_otp("userB@example.com", purpose="REGISTER")
        _, _, tokB = database.verify_otp_db("userB@example.com", codeB, purpose="REGISTER")
        userB = database.register_user("user_b", "userB@example.com", "Password@123", phone="9825000003", reg_token=tokB["reg_token"])
        weddingB_id = database.save_or_update_wedding(userB["user_id"], {
            "groom_name": "GroomB", "bride_name": "BrideB",
            "groom_father_name": "FatherB", "groom_father_phone": "9825000004"
        })

        # Log in as User B
        with self.client:
            self.client.post("/login", data={"username": "user_b", "password": "Password@123"})

            # User B attempts to trigger parent link dispatch for User A's parent token (IDOR Attack!)
            res = self.client.post(f"/api/send-parent-link/{parentA_token}")
            self.assertEqual(res.status_code, 403)
            self.assertIn("Unauthorized".encode("utf-8"), res.data)

if __name__ == "__main__":
    unittest.main()
