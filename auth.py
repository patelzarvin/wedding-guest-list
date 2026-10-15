import functools
import os
from flask import session, redirect, url_for, request, jsonify
from database import authenticate_user as db_auth_user

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id") and not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized. Please log in."}), 401
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def check_side_authorization(target_side):
    """
    Side authorization check:
    - Master can access all sides
    - Groom can only access GROOM
    - Bride can only access BRIDE
    """
    user_role = session.get("user_role", "MASTER")
    if not user_role or user_role == "MASTER":
        return True
    return user_role == target_side.upper()

