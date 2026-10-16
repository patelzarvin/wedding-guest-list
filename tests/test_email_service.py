import unittest
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import email_service

class TestEmailService(unittest.TestCase):

    @patch("email_service.requests.post")
    def test_resend_http_api_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        with patch("email_service.get_http_email_config", return_value={"resend_key": "re_test_key_123", "brevo_key": "", "sendgrid_key": ""}):
            ok, msg = email_service.send_verification_email("guest@example.com", "654321", "REGISTER")
            self.assertTrue(ok)
            self.assertIn("Resend HTTPS API", msg)
            self.assertTrue(mock_post.called)

    @patch("email_service.requests.post")
    def test_brevo_http_api_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        with patch("email_service.get_http_email_config", return_value={"resend_key": "", "brevo_key": "xkeysib_test_123", "sendgrid_key": ""}):
            ok, msg = email_service.send_verification_email("guest@example.com", "654321", "REGISTER")
            self.assertTrue(ok)
            self.assertIn("Brevo HTTPS API", msg)
            self.assertTrue(mock_post.called)

    @patch("email_service.smtplib.SMTP")
    @patch("email_service.smtplib.SMTP_SSL")
    def test_cloud_smtp_timeout_helpful_error(self, mock_ssl, mock_smtp):
        mock_smtp.side_effect = TimeoutError("timed out")
        mock_ssl.side_effect = TimeoutError("timed out")

        with patch("email_service.get_http_email_config", return_value={"resend_key": "", "brevo_key": "", "sendgrid_key": ""}):
            with patch.dict(os.environ, {"RENDER": "true"}):
                ok, msg = email_service.send_verification_email("guest@example.com", "654321", "REGISTER")
                self.assertFalse(ok)
                self.assertIn("Render Free tier blocks outbound SMTP ports", msg)

if __name__ == "__main__":
    unittest.main()
