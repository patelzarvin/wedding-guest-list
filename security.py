import re
import os
import time
import hmac
import hashlib
import secrets
import threading
from datetime import datetime, timedelta

# Pepper for keyed OTP hashing
OTP_PEPPER = os.environ.get("OTP_PEPPER", "wedvite_luxury_auth_pepper_2026_gj")

# -------------------------------------------------------------
# IDENTIFIER NORMALIZATION & VALIDATION
# -------------------------------------------------------------
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")

def normalize_email(email):
    """Trim whitespace and convert to lowercase."""
    if not email:
        return ""
    return str(email).strip().lower()

def validate_email(email):
    """
    Validate email address according to standard email format.
    Returns: (is_valid, error_msg, normalized_email)
    """
    norm = normalize_email(email)
    if not norm:
        return False, "ઇમેઇલ એડ્રેસ જરૂરી છે. (Email address is required).", ""
    if len(norm) > 100:
        return False, "ઇમેઇલ ખૂબ લાંબો છે. (Email is too long).", ""
    if not EMAIL_REGEX.match(norm):
        return False, "કૃપા કરીને માન્ય ઇમેઇલ એડ્રેસ દાખલ કરો. (Please enter a valid email address).", ""
    return True, None, norm

def clean_phone_10(phone):
    """
    Extract digits from input and normalize Indian phone number to 10 digits.
    Handles +91, 91, 0 prefixes cleanly.
    """
    if not phone:
        return ""
    digits = "".join(filter(str.isdigit, str(phone)))
    # If 12 digits starting with 91, strip country code
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    # If 11 digits starting with 0, strip leading 0
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    # If longer than 10 digits, extract last 10
    elif len(digits) > 10:
        digits = digits[-10:]
    return digits

def validate_indian_phone(phone):
    """
    Validate and normalize Indian mobile number (+91).
    Rules: Must be exactly 10 digits starting with 6, 7, 8, or 9.
    Returns: (is_valid, error_msg, clean_10, formatted_e164)
    """
    clean_10 = clean_phone_10(phone)
    if not clean_10:
        return False, "મોબાઇલ નંબર જરૂરી છે. (Mobile number is required).", "", ""
    if len(clean_10) != 10:
        return False, "કૃપા કરીને માન્ય 10-અંકનો મોબાઇલ નંબર દાખલ કરો. (Must be 10 digits).", "", ""
    if clean_10[0] not in ('6', '7', '8', '9'):
        return False, "માન્ય ભારતીય મોબાઇલ નંબર દાખલ કરો (6-9 થી શરૂ થતો). (Must start with 6-9).", "", ""
    formatted_e164 = f"+91{clean_10}"
    return True, None, clean_10, formatted_e164

def validate_username(username):
    """
    Validate username:
    - 3 to 30 characters
    - Alphanumeric, underscores, hyphens, dots
    Returns: (is_valid, error_msg, normalized_username)
    """
    if not username:
        return False, "યુઝરનેમ જરૂરી છે. (Username is required).", ""
    norm = str(username).strip()
    if len(norm) < 3:
        return False, "યુઝરનેમ ઓછામાં ઓછા 3 અક્ષરનું હોવું જરૂરી છે. (Username must be at least 3 characters).", ""
    if len(norm) > 30:
        return False, "યુઝરનેમ 30 અક્ષરથી વધુ ન હોવું જોઈએ. (Username must not exceed 30 characters).", ""
    if not USERNAME_REGEX.match(norm):
        return False, "યુઝરનેમમાં માત્ર અક્ષરો, અંકો, અને _, -, . માન્ય છે. (Allowed: letters, numbers, _, -, .)", ""
    return True, None, norm

def validate_password_strength(password):
    """
    Enforce password policy:
    - Minimum 8 characters
    - Must contain at least one letter and at least one digit or special character
    Returns: (is_valid, error_msg)
    """
    if not password:
        return False, "પાસવર્ડ જરૂરી છે. (Password is required)."
    if len(password) < 8:
        return False, "પાસવર્ડ ઓછામાં ઓછા 8 અક્ષરનો હોવો જોઈએ. (Password must be at least 8 characters)."
    if len(password) > 128:
        return False, "પાસવર્ડ ખૂબ લાંબો છે. (Password must not exceed 128 characters)."
    has_letter = any(c.isalpha() for c in password)
    has_digit_or_symbol = any(not c.isalpha() for c in password)
    if not (has_letter and has_digit_or_symbol):
        return False, "પાસવર્ડમાં અક્ષરો અને ઓછામાં ઓછો એક અંક અથવા ચિહ્ન હોવું જરૂરી છે. (Must contain letters and at least one number/symbol)."
    return True, None

# -------------------------------------------------------------
# CRYPTOGRAPHIC UTILITIES
# -------------------------------------------------------------
def hash_otp(identifier, otp_code, purpose):
    """Keyed SHA-256 hash of OTP code bound to identifier and purpose."""
    norm_ident = str(identifier).strip().lower()
    norm_purp = str(purpose).strip().upper()
    code_str = str(otp_code).strip()
    payload = f"{OTP_PEPPER}:{norm_ident}:{norm_purp}:{code_str}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def constant_time_compare(val1, val2):
    """Safe constant-time string comparison to prevent timing attacks."""
    if val1 is None or val2 is None:
        return False
    return hmac.compare_digest(str(val1), str(val2))

def generate_secure_token(length=32):
    """Generate cryptographically unpredictable token (e.g. for reset_token or reg_token)."""
    return secrets.token_urlsafe(length)

# -------------------------------------------------------------
# THREAD-SAFE RATE LIMITER
# -------------------------------------------------------------
class AuthRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        # key -> list of timestamps
        self._login_attempts = {}
        self._otp_requests = {}
        self._otp_last_requested = {} # key -> timestamp of last request for 60s cooldown

    def check_login_rate_limit(self, ip, identifier, max_attempts=5, window_seconds=900):
        """
        Max 5 failed attempts per 15 minutes per IP + identifier.
        Returns: (allowed, retry_after_seconds)
        """
        now = time.time()
        key = f"{ip}:{str(identifier).strip().lower()}"
        with self._lock:
            attempts = [t for t in self._login_attempts.get(key, []) if now - t < window_seconds]
            self._login_attempts[key] = attempts
            if len(attempts) >= max_attempts:
                oldest = attempts[0]
                retry_after = int(window_seconds - (now - oldest))
                return False, max(1, retry_after)
            return True, 0

    def record_login_failure(self, ip, identifier):
        now = time.time()
        key = f"{ip}:{str(identifier).strip().lower()}"
        with self._lock:
            attempts = self._login_attempts.get(key, [])
            attempts.append(now)
            self._login_attempts[key] = attempts

    def reset_login_rate_limit(self, ip, identifier):
        key = f"{ip}:{str(identifier).strip().lower()}"
        with self._lock:
            self._login_attempts.pop(key, None)

    def check_otp_rate_limit(self, ip, identifier, max_requests=3, window_seconds=600, cooldown_seconds=60):
        """
        Enforce max 3 OTP requests per 10 minutes, plus 60 seconds resend cooldown.
        Returns: (allowed, error_msg, retry_after_seconds)
        """
        now = time.time()
        norm_ident = str(identifier).strip().lower()
        key = f"{norm_ident}"
        ip_key = f"ip:{ip}"

        with self._lock:
            # 1. Cooldown check
            last_req = self._otp_last_requested.get(key, 0)
            if now - last_req < cooldown_seconds:
                rem = int(cooldown_seconds - (now - last_req))
                return False, f"કૃપા કરીને ફરી કોડ મોકલવા માટે {rem} સેકન્ડ રાહ જુઓ. (Please wait {rem}s before requesting a new code).", rem

            # 2. Window check by identifier
            reqs = [t for t in self._otp_requests.get(key, []) if now - t < window_seconds]
            self._otp_requests[key] = reqs
            if len(reqs) >= max_requests:
                oldest = reqs[0]
                retry_after = int(window_seconds - (now - oldest))
                return False, f"બહુ બધા વિનંતીઓ થઈ ગઈ છે. કૃપા કરીને {retry_after // 60 + 1} મિનિટ પછી ફરી પ્રયાસ કરો. (Rate limit exceeded. Try again in {retry_after}s).", retry_after

            # 3. Window check by IP
            ip_reqs = [t for t in self._otp_requests.get(ip_key, []) if now - t < window_seconds]
            self._otp_requests[ip_key] = ip_reqs
            if len(ip_reqs) >= (max_requests * 3):
                return False, "તમારા નેટવર્ક પરથી ઘણી વિનંતીઓ આવી છે. થોડી વાર પછી પ્રયાસ કરો. (Too many requests from this IP).", 300

            return True, None, 0

    def record_otp_request(self, ip, identifier):
        now = time.time()
        norm_ident = str(identifier).strip().lower()
        key = f"{norm_ident}"
        ip_key = f"ip:{ip}"
        with self._lock:
            self._otp_last_requested[key] = now
            reqs = self._otp_requests.get(key, [])
            reqs.append(now)
            self._otp_requests[key] = reqs
            ip_reqs = self._otp_requests.get(ip_key, [])
            ip_reqs.append(now)
            self._otp_requests[ip_key] = ip_reqs

# Global singleton rate limiter
rate_limiter = AuthRateLimiter()
