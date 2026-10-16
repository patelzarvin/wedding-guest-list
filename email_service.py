import smtplib
import os
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

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
    
    import ssl
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=12) as server:
            server.login(sender_email, clean_pwd)
            server.sendmail(sender_email, [recipient_email], msg.as_string())
        return True, "Email sent successfully"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail Authentication failed. Please check your Google App Password."
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"
