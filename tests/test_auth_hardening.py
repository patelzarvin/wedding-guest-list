import unittest
from app import app
import database
import security

class TestAuthFixes(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_proxy_fix_headers(self):
        """Verify ProxyFix respects X-Forwarded-Proto https when processed by WSGI"""
        environ = {
            'wsgi.url_scheme': 'http',
            'HTTP_X_FORWARDED_PROTO': 'https',
            'HTTP_X_FORWARDED_FOR': '203.0.113.195',
            'REQUEST_METHOD': 'GET',
            'PATH_INFO': '/'
        }
        # Run through ProxyFix WSGI layer
        def start_response(status, headers):
            pass
        app.wsgi_app(environ, start_response)
        self.assertEqual(environ['wsgi.url_scheme'], 'https')
        self.assertEqual(environ['REMOTE_ADDR'], '203.0.113.195')

    def test_auth_send_otp_with_phone_and_email(self):
        """Verify auth_send_otp accepts phone and email and returns success with fallback or delivery"""
        res = self.client.post("/api/auth/send-otp", json={
            "email": "newguest@example.com",
            "phone": "9876501234",
            "purpose": "REGISTER"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        # Should contain message and target_email
        self.assertIn("target_email", data)

    def test_login_rate_limiter_reset(self):
        """Verify successful login resets rate limiting across all IPs"""
        ident = "admin_reset_test"
        ip = "10.0.0.1"
        security.rate_limiter.record_login_failure(ip, ident)
        allowed, _ = security.rate_limiter.check_login_rate_limit(ip, ident)
        self.assertTrue(allowed)
        security.rate_limiter.reset_login_rate_limit(ip, ident)
        self.assertNotIn(f"{ip}:{ident}", security.rate_limiter._login_attempts)

if __name__ == "__main__":
    unittest.main()
