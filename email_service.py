import smtplib
import os
import json
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

def get_http_email_config():
    """Retrieve HTTP API key (Resend, Brevo, SendGrid) from environment, settings.json, or db"""
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    
    if not (resend_key or brevo_key or sendgrid_key):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    resend_key = data.get("resend_api_key", "").strip()
                    brevo_key = data.get("brevo_api_key", "").strip()
                    sendgrid_key = data.get("sendgrid_api_key", "").strip()
            except Exception:
                pass
                
    if not (resend_key or brevo_key or sendgrid_key):
        try:
            from database import get_app_setting
            resend_key = (get_app_setting("resend_api_key") or "").strip()
            brevo_key = (get_app_setting("brevo_api_key") or "").strip()
            sendgrid_key = (get_app_setting("sendgrid_api_key") or "").strip()
        except Exception:
            pass

    return {
        "resend_key": resend_key,
        "brevo_key": brevo_key,
        "sendgrid_key": sendgrid_key
    }

def send_via_http_api(recipient_email, subject, html_content, plain_text):
    """
    Sends email over HTTPS (Port 443) using Resend, Brevo, or SendGrid REST API.
    Unrestricted on cloud hosts like Render Free tier where raw SMTP ports are blocked.
    """
    import requests
    config = get_http_email_config()
    sender_email, _ = get_smtp_credentials()

    # 1. Resend API
    if config["resend_key"]:
        try:
            url = "https://api.resend.com/emails"
            headers = {
                "Authorization": f"Bearer {config['resend_key']}",
                "Content-Type": "application/json"
            }
            from_addr = "WedVite Luxury Suite <onboarding@resend.dev>" if "@" not in sender_email or "gmail.com" in sender_email else f"WedVite <{sender_email}>"
            payload = {
                "from": from_addr,
                "to": [recipient_email],
                "subject": subject,
                "html": html_content,
                "text": plain_text
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=6)
            if resp.status_code in (200, 201):
                return True, "Email sent successfully via Resend HTTPS API"
            return False, f"Resend API error ({resp.status_code}): {resp.text}"
        except Exception as e:
            return False, f"Resend API exception: {str(e)}"

    # 2. Brevo API (formerly Sendinblue)
    if config["brevo_key"]:
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            headers = {
                "api-key": config["brevo_key"],
                "Content-Type": "application/json"
            }
            payload = {
                "sender": {"name": "WedVite Luxury Suite", "email": sender_email or "zarvin.114@gmail.com"},
                "to": [{"email": recipient_email}],
                "subject": subject,
                "htmlContent": html_content,
                "textContent": plain_text
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=6)
            if resp.status_code in (200, 201):
                return True, "Email sent successfully via Brevo HTTPS API"
            return False, f"Brevo API error ({resp.status_code}): {resp.text}"
        except Exception as e:
            return False, f"Brevo API exception: {str(e)}"

    # 3. SendGrid API
    if config["sendgrid_key"]:
        try:
            url = "https://api.sendgrid.com/v3/mail/send"
            headers = {
                "Authorization": f"Bearer {config['sendgrid_key']}",
                "Content-Type": "application/json"
            }
            payload = {
                "personalizations": [{"to": [{"email": recipient_email}]}],
                "from": {"email": sender_email or "zarvin.114@gmail.com", "name": "WedVite Luxury Suite"},
                "subject": subject,
                "content": [
                    {"type": "text/plain", "value": plain_text},
                    {"type": "text/html", "value": html_content}
                ]
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=6)
            if resp.status_code in (200, 202):
                return True, "Email sent successfully via SendGrid HTTPS API"
            return False, f"SendGrid API error ({resp.status_code}): {resp.text}"
        except Exception as e:
            return False, f"SendGrid API exception: {str(e)}"

    return None, "No HTTP email API configured"

def get_smtp_credentials():
    """Retrieve SMTP sender email and app password from env, database, settings.json, or verified fallback"""
    # 1. Environment variables (Cloud / Render)
    smtp_email = os.environ.get("SMTP_EMAIL", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    
    # 2. Database persistent settings (wedding.db)
    if not smtp_email or not smtp_password:
        try:
            from database import get_app_setting
            if not smtp_email:
                db_email = get_app_setting("smtp_email")
                if db_email and db_email.strip():
                    smtp_email = db_email.strip()
            if not smtp_password:
                db_pwd = get_app_setting("smtp_password")
                if db_pwd and db_pwd.strip():
                    smtp_password = db_pwd.strip()
        except Exception:
            pass

    # 3. Local settings.json file
    if not smtp_email or not smtp_password:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:                          
                    data = json.load(f)
                    if not smtp_email:
                        smtp_email = data.get("smtp_email", "").strip()
                    if not smtp_password:
                        smtp_password = data.get("smtp_password", "").strip()
            except Exception:
                pass

    # 4. Out-of-the-box verified production fallback
    if not smtp_email:
        smtp_email = "zarvin.114@gmail.com"
    if not smtp_password:
        smtp_password = "uwfw vbvp qeqv jvpi"
        
    return smtp_email, smtp_password

def send_verification_email(recipient_email, otp_code, purpose="REGISTER"):
    """
    Sends an elegant luxury-themed HTML verification code to recipient's email.
    The OTP is NEVER leaked to frontend API responses.
    """
    sender_email, app_password = get_smtp_credentials()
    
    if not app_password:
        return False, "SMTP App Password is not configured yet in settings.json"
        
    clean_pwd = app_password.replace(" ", "")
    
    subject_map = {
        "REGISTER": "🔐 WedVite Luxury Suite - Verify Your Email",
        "RESET": "🔑 WedVite Luxury Suite - Password Reset Code"
    }
    action_map = {
        "REGISTER": "Welcome to WedVite Luxury Suite. Please verify your email to activate your wedding management portal.",
        "RESET": "We received a request to reset your WedVite account password. Use the verification code below to proceed."
    }
    
    subject = subject_map.get(purpose, "🔐 WedVite Verification Code")
    action_text = action_map.get(purpose, "Your verification code is ready.")
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #020704;
                color: #FFFFFF;
                margin: 0;
                padding: 40px 20px;
            }}
            .card {{
                max-width: 520px;
                margin: 0 auto;
                background: linear-gradient(145deg, #06140D 0%, #0D2C1E 100%);
                border: 1px solid rgba(212, 175, 55, 0.4);
                border-radius: 20px;
                padding: 40px 30px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.8);
                text-align: center;
            }}
            .logo {{
                font-family: Georgia, serif;
                font-size: 28px;
                color: #F3E5AB;
                letter-spacing: 1px;
                margin-bottom: 5px;
            }}
            .logo span {{
                color: #D4AF37;
                font-style: italic;
            }}
            .tagline {{
                font-size: 13px;
                color: #A3B899;
                margin-bottom: 30px;
            }}
            .msg {{
                font-size: 15px;
                line-height: 1.6;
                color: #E2E8F0;
                margin-bottom: 25px;
            }}
            .otp-box {{
                display: inline-block;
                background: #040F09;
                border: 2px solid #D4AF37;
                border-radius: 12px;
                padding: 18px 36px;
                font-size: 36px;
                font-weight: 800;
                letter-spacing: 8px;
                color: #F3E5AB;
                box-shadow: 0 0 25px rgba(212, 175, 55, 0.25);
                margin: 15px 0 25px 0;
            }}
            .expiry {{
                font-size: 13px;
                color: #FBBF24;
                margin-bottom: 25px;
            }}
            .footer {{
                font-size: 11px;
                color: #64748B;
                border-top: 1px solid rgba(212, 175, 55, 0.2);
                padding-top: 20px;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="logo">WedVite <span>Luxury Suite</span></div>
            <div class="tagline">Intelligent Wedding Guest Management Software</div>
            <p class="msg">{action_text}</p>
            <div>
                <div class="otp-box">{otp_code}</div>
            </div>
            <p class="expiry">⏳ આ કોડ ફક્ત ૧૦ મિનિટ માટે માન્ય છે. (Valid for 10 minutes)</p>
            <p style="font-size: 12px; color: #94A3B8;">જો તમે આ કોડ માટે વિનંતી ન કરી હોય, તો આ ઇમેઇલને અવગણો.</p>
            <div class="footer">
                &copy; 2026 WedVite Luxury Suite &bull; 256-Bit SSL Encrypted Verification
            </div>
        </div>
    </body>
    </html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"WedVite Luxury Suite <{sender_email}>"
    msg["To"] = recipient_email
    
    plain_text = f"WedVite Luxury Suite Verification Code:\n\n{otp_code}\n\nValid for 10 minutes.\nDo not share this code with anyone."
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))
    
    # 1. Prefer HTTP Email API (HTTPS 443 - zero cloud port blocking)
    http_ok, http_msg = send_via_http_api(recipient_email, subject, html_content, plain_text)
    if http_ok is True:
        return True, http_msg
    elif http_ok is False and "API error" in http_msg:
        return False, http_msg

    # 2. Direct SMTP transport with IPv4 resolution
    import ssl
    import socket

    orig_create_conn = socket.create_connection

    # Cloud / Render environment fix: Force IPv4 connection to prevent [Errno 101] Network is unreachable
    def create_ipv4_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        err = None
        for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            sock = None
            try:
                sock = socket.socket(af, socktype, proto)
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sa)
                return sock
            except Exception as e:
                err = e
                if sock is not None:
                    sock.close()
        if err is not None:
            raise err
        raise socket.error("Could not resolve IPv4 address for SMTP host")

    context = ssl.create_default_context()
    socket.create_connection = create_ipv4_connection

    try:
        # Method 1: Port 587 with STARTTLS (fast 4s timeout)
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=4) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(sender_email, clean_pwd)
                server.sendmail(sender_email, [recipient_email], msg.as_string())
            return True, "Email sent successfully via Port 587"
        except Exception as err587:
            # Method 2: Port 465 with SSL (fast 4s timeout)
            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=4) as server:
                    server.login(sender_email, clean_pwd)
                    server.sendmail(sender_email, [recipient_email], msg.as_string())
                return True, "Email sent successfully via Port 465"
            except smtplib.SMTPAuthenticationError:
                return False, "Gmail Authentication failed. Please check your Google App Password."
            except Exception as err465:
                is_timeout = "timed out" in str(err587).lower() or "timed out" in str(err465).lower()
                is_cloud = bool(os.environ.get("RENDER") or os.environ.get("PORT"))
                if is_timeout and is_cloud:
                    return False, (
                        "Cloud network restriction: Render Free tier blocks outbound SMTP ports 587 and 465. "
                        "Please set RESEND_API_KEY or BREVO_API_KEY in Render Environment Variables for HTTPS email delivery."
                    )
                return False, f"Failed to send email: {str(err587)} | Fallback error: {str(err465)}"
    finally:
        socket.create_connection = orig_create_conn
