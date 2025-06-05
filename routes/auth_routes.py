from flask import Blueprint, request, jsonify, session, make_response, current_app
from extensions import db, redis_service
from models import User, PasswordReset
from utils import login_required
from utils.enhanced_email import email_service, send_welcome_email, send_password_reset_email, generate_six_digit_token
import re
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import logging

auth_bp = Blueprint('auth', __name__)

# --------------------------------------------------------------------
# Authentication Endpoints
# --------------------------------------------------------------------
@auth_bp.route("/api/login", methods=["POST"])
def login():
    current_app.logger.debug(f"Login attempt from: {request.remote_addr}, User-Agent: {request.headers.get('User-Agent')}")
    
    try:
        data = request.get_json()
        if not data:
            current_app.logger.warning("No JSON data received in request")
            return jsonify({"message": "Invalid request format"}), 400
            
        username_or_email = data.get("username")
        password = data.get("password")
        
        current_app.logger.debug(f"Login attempt for: {username_or_email}")
        
        if not username_or_email or not password:
            return jsonify({"message": "Username/email and password are required"}), 400
        
        user = None
        if '@' in username_or_email:
            try:
                user = User.query.filter_by(email=username_or_email).first()
                if user:
                    current_app.logger.debug(f"User found by email: {username_or_email}")
            except Exception as e:
                current_app.logger.warning(f"Email query failed, trying username: {str(e)}")
        
        if not user:
            user = User.query.filter_by(username=username_or_email).first()
            if user:
                current_app.logger.debug(f"User found by username: {username_or_email}")
        
        if not user:
            current_app.logger.debug(f"No user found for: {username_or_email}")
            return jsonify({"message": "Invalid credentials"}), 401
            
        if user and check_password_hash(user.password, password):
            session.permanent = True
            session["user_id"] = user.id
            session["user_role"] = user.role
            
            user.last_active = datetime.utcnow()
            db.session.commit()
            
            # Cache user session data in Redis
            if current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
                session_data = {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                    "telegram_username": user.telegram_username,
                    "telegram_chat_id": user.telegram_chat_id
                }
                redis_service.set_user_session(
                    user.id,
                    session_data,
                    expire=current_app.config.get('SESSION_CACHE_TIMEOUT', 86400)
                )
            
            response = jsonify({
                "message": "Login successful",
                "role": user.role,
                "username": user.username,
                "telegram_username": user.telegram_username,
                "telegram_chat_id": user.telegram_chat_id
            })
            response.set_cookie(
                'user_role',
                user.role,
                max_age=30*24*60*60,
                httponly=False,
                secure=True,
                samesite='None'
            )
            
            is_production = current_app.config.get('ENV') == 'production'
            domain = None  # Let browser determine domain
            
            response.set_cookie(
                'session_active', 
                'true',
                max_age=30*24*60*60,
                httponly=True,
                secure=True,
                samesite='None',
                domain=domain
            )
            
            session_cookie_name = current_app.config.get('SESSION_COOKIE_NAME', 'session')
            response.set_cookie(
                session_cookie_name,
                request.cookies.get(session_cookie_name, ''),
                max_age=30*24*60*60,
                httponly=True,
                secure=True,
                samesite='None',
                domain=domain
            )
            
            current_app.logger.info(f"Login successful for: {username_or_email}")
            return response
        
        current_app.logger.debug(f"Invalid password for: {username_or_email}")
        return jsonify({"message": "Invalid credentials"}), 401
        
    except Exception as e:
        current_app.logger.error(f"Login error: {str(e)}")
        return jsonify({"message": "An error occurred during login", "error": str(e) if current_app.debug else None}), 500

@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    current_app.logger.info(f"Logout attempt from: {request.remote_addr}, Session: {dict(session)}")
    
    # Clear cached user session from Redis
    user_id = session.get("user_id")
    if user_id and current_app.config.get('REDIS_ENABLED') and redis_service.is_available():
        redis_service.clear_user_session(user_id)
    
    # Clear Flask session and cookies
    session.clear()
    response = jsonify({"message": "Logged out successfully"})
    session_cookie_name = current_app.config.get('SESSION_COOKIE_NAME', 'session')
    response.set_cookie(session_cookie_name, '', expires=0, httponly=True, secure=True, samesite='None')
    response.set_cookie('session_active', '', expires=0, httponly=True, secure=True, samesite='None')
    response.set_cookie('user_role', '', expires=0, httponly=False, secure=True, samesite='None')
    current_app.logger.info("Session cleared and cookies expired")
    return response

@auth_bp.route('/api/session', methods=['GET'])
def check_session():
    try:
        current_app.logger.debug(f"Session check - Session contents: {dict(session)}")
        current_app.logger.debug(f"Session check - Cookies received: {request.cookies}")
        
        if "user_id" not in session:
            current_app.logger.debug("No user_id in session")
            return jsonify({"isLoggedIn": False})
        
        user_id = session.get("user_id")
        current_app.logger.debug(f"Found user_id in session: {user_id}")
        
        user = User.query.get(user_id)
        
        if user is None:
            current_app.logger.debug(f"User with ID {user_id} not found in database")
            session.clear()
            return jsonify({"isLoggedIn": False, "message": "User not found"})
            
        user.last_active = datetime.utcnow()
        db.session.commit()
        
        current_app.logger.debug(f"Session check successful for user: {user.username}, role: {user.role}")
        
        return jsonify({
            "isLoggedIn": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "telegram_username": user.telegram_username,
                "telegram_chat_id": user.telegram_chat_id
            }
        })
    except Exception as e:
        logging.error(f"Session check error: {str(e)}")
        db.session.rollback()
        return jsonify({"isLoggedIn": False, "message": "Server error"}), 500

# --------------------------------------------------------------------
# Registration and Account Management Endpoints
# --------------------------------------------------------------------
@auth_bp.route("/api/check-username", methods=["POST"])
def check_username():
    data = request.get_json()
    username = data.get("username")
    
    if not username:
        return jsonify({"available": False, "message": "Username is required"}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return jsonify({
            "available": False, 
            "message": "Username must be 3-20 characters and contain only letters, numbers, and underscores"
        }), 400
    
    exists = User.query.filter_by(username=username).first() is not None
    return jsonify({"available": not exists})

@auth_bp.route("/api/check-email", methods=["POST"])
def check_email():
    data = request.get_json()
    email = data.get("email")
    
    if not email:
        return jsonify({"available": False, "message": "Email is required"}), 400
    
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({"available": False, "message": "Invalid email format"}), 400
    
    exists = User.query.filter_by(email=email).first() is not None
    return jsonify({"available": not exists})

@auth_bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    receive_updates = data.get("receiveUpdates", False)
    telegram_username = data.get("telegram_username")
    telegram_chat_id = data.get("telegram_chat_id")
    
    if not username or not email or not password:
        return jsonify({"message": "Username, email, and password are required"}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return jsonify({
            "message": "Username must be 3-20 characters and contain only letters, numbers, and underscores"
        }), 400
    
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({"message": "Invalid email format"}), 400
    
    if len(password) < 8:
        return jsonify({"message": "Password must be at least 8 characters"}), 400
    
    if not re.search(r'[A-Z]', password):
        return jsonify({"message": "Password must contain at least one uppercase letter"}), 400
    
    if not re.search(r'[0-9]', password):
        return jsonify({"message": "Password must contain at least one number"}), 400
    
    if not re.search(r'[^A-Za-z0-9]', password):
        return jsonify({"message": "Password must contain at least one special character"}), 400
    
    if User.query.filter_by(username=username).first():
        return jsonify({"message": "Username already taken"}), 400
    
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "Email already registered"}), 400
    
    if telegram_username and not telegram_username.startswith('@'):
        return jsonify({"message": "Telegram username must start with @"}), 400
    
    if telegram_username and User.query.filter_by(telegram_username=telegram_username).first():
        return jsonify({"message": "Telegram username already taken"}), 400
    
    hashed_password = generate_password_hash(password)
    new_user = User(
        username=username,
        email=email,
        password=hashed_password,
        role="agent",
        receive_updates=receive_updates,
        created_at=datetime.utcnow(),
        last_active=datetime.utcnow(),
        telegram_username=telegram_username,
        telegram_chat_id=telegram_chat_id
    )
    
    try:
        db.session.add(new_user)
        db.session.commit()
        
        try:
            send_welcome_email(email, username)
            current_app.logger.info(f"Welcome email sent to {email}")
        except Exception as e:
            current_app.logger.error(f"Failed to send welcome email: {str(e)}")
        
        return jsonify({
            "message": "Account created successfully",
            "user": new_user.serialize()
        }), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Registration error: {str(e)}")
        return jsonify({"message": f"Error creating account: {str(e)}"}), 500

@auth_bp.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    email = data.get("email")
    
    if not email or not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({"message": "Valid email is required"}), 400
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"message": "If your email is registered, you will receive a password reset code"}), 200
    
    token = generate_six_digit_token()
    
    expiration = datetime.utcnow() + timedelta(hours=1)
    
    password_reset = PasswordReset(
        user_id=user.id,
        token=token,
        expires_at=expiration
    )
    
    try:
        PasswordReset.query.filter_by(user_id=user.id).delete()
        
        db.session.add(password_reset)
        db.session.commit()
        
        try:
            send_password_reset_email(email, token)
            current_app.logger.info(f"Password reset email sent to {email}")
        except Exception as e:
            current_app.logger.error(f"Failed to send password reset email: {str(e)}")
            db.session.rollback()
            return jsonify({"message": "Unable to send password reset email. Please try again later."}), 500
        
        return jsonify({
            "message": "If your email is registered, you will receive a password reset code"
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Password reset error: {str(e)}")
        return jsonify({"message": "An error occurred processing your request"}), 500

@auth_bp.route("/api/verify-reset-token", methods=["POST"])
def verify_reset_token():
    data = request.get_json()
    token = data.get("token")
    
    if not token:
        return jsonify({"valid": False, "message": "Token is required"}), 400
    
    if not token.isdigit() or len(token) != 6:
        return jsonify({"valid": False, "message": "Token must be a 6-digit number"}), 400
    
    reset_entry = PasswordReset.query.filter_by(token=token).first()
    
    if not reset_entry:
        return jsonify({"valid": False, "message": "Invalid or expired token"}), 400
    
    if reset_entry.expires_at < datetime.utcnow():
        return jsonify({"valid": False, "message": "Token has expired"}), 400
    
    return jsonify({"valid": True})

@auth_bp.route("/api/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    token = data.get("token")
    new_password = data.get("password")
    
    if not token or not new_password:
        return jsonify({"message": "Token and new password are required"}), 400
    
    if not token.isdigit() or len(token) != 6:
        return jsonify({"message": "Token must be a 6-digit number"}), 400
    
    if len(new_password) < 8:
        return jsonify({"message": "Password must be at least 8 characters"}), 400
    
    if not re.search(r'[A-Z]', new_password):
        return jsonify({"message": "Password must contain at least one uppercase letter"}), 400
    
    if not re.search(r'[0-9]', new_password):
        return jsonify({"message": "Password must contain at least one number"}), 400
    
    if not re.search(r'[^A-Za-z0-9]', new_password):
        return jsonify({"message": "Password must contain at least one special character"}), 400
    
    reset_entry = PasswordReset.query.filter_by(token=token).first()
    
    if not reset_entry:
        return jsonify({"message": "Invalid or expired token"}), 400
    
    if reset_entry.expires_at < datetime.utcnow():
        return jsonify({"message": "Token has expired"}), 400
    
    try:
        user = db.session.get(User, reset_entry.user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        user.password = generate_password_hash(new_password)
        PasswordReset.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        
        try:
            email_subject = "Your Password Has Been Reset"
            current_year = datetime.now().year
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta name="x-apple-disable-message-reformatting">
                <meta name="color-scheme" content="light dark">
                <meta name="supported-color-schemes" content="light dark">
                <title>Password Reset Confirmation</title>
                <style type="text/css">
                    body {{ width: 100% !important; margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
                    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
                    td {{ border-collapse: collapse; }}
                    img {{ border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }}
                    a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: none !important; font-size: inherit !important; font-family: inherit !important; font-weight: inherit !important; line-height: inherit !important; }}
                    .button:hover {{ background-color: #388e3c !important; }}
                    @media only screen and (max-width: 600px) {{
                        .container {{ width: 100% !important; padding: 10px !important; }}
                        .button {{ width: 100% !important; display: block !important; }}
                        .header img {{ max-width: 120px !important; }}
                        .content {{ padding: 20px !important; }}
                        .footer {{ padding: 10px !important; font-size: 11px !important; }}
                    }}
                </style>
            </head>
            <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f5f5f5; color: #333333;">
                <table role="presentation" width="100%" style="background-color: #f5f5f5;" cellpadding="0" cellspacing="0">
                    <tr>
                        <td align="center">
                            <table role="presentation" class="container" width="600" style="margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden;" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td class="header" style="background-color: #4caf50; padding: 20px; text-align: center;">
                                        <img src="https://jetcamstudio.com/wp-content/uploads/2023/04/Untitled-9-1-2.png" alt="JetCam Studio Logo" style="max-width: 150px; height: auto; border: 0;">
                                    </td>
                                </tr>
                                <tr>
                                    <td class="content" style="padding: 30px;">
                                        <h1 style="margin: 0 0 15px; font-size: 24px; font-weight: 600; color: #202124;">Password Reset Successful</h1>
                                        <p style="margin: 0 0 20px; font-size: 16px; line-height: 24px; color: #444444;">Your JetCam Studio account password has been successfully reset.</p>
                                        <p style="margin: 0 0 20px; font-size: 16px; line-height: 24px; color: #444444;">You can now log in with your new password.</p>
                                        <p style="margin: 0 0 20px; font-size: 14px; color: #666666;">If you did not initiate this change, please contact <a href="mailto:support@jetcamstudio.com" style="color: #4caf50; text-decoration: none;">support@jetcamstudio.com</a> immediately.</p>
                                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin: 20px 0;">
                                            <tr>
                                                <td align="center">
                                                    <a href="https://monitor-backend.jetcamstudio.com:5000" class="button" style="display: inline-block; padding: 12px 24px; background-color: #4caf50; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 16px; font-weight: 500; min-width: 180px; text-align: center; transition: background-color 0.3s ease;">Log In Now</a>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                                <tr>
                                    <td class="footer" style="padding: 15px; background-color: #f5f5f5; text-align: center; font-size: 12px; color: #666666;">
                                        <p style="margin: 0;">Â© {current_year} JetCam Studio. All rights reserved.</p>
                                        <p style="margin: 5px 0 0;">This is an automated message. Please do not reply.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </body>
            </html>
            """
            email_service.send_email(user.email, email_subject, html_content)
            current_app.logger.info(f"Password reset confirmation email sent to {user.email}")
        except Exception as e:
            current_app.logger.error(f"Failed to send password reset confirmation email: {str(e)}")
        
        return jsonify({
            "message": "Password has been reset successfully. You can now log in with your new password."
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Password reset error: {str(e)}")
        return jsonify({"message": "An error occurred processing your request"}), 500

@auth_bp.route("/api/change-password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json()
    current_password = data.get("currentPassword")
    new_password = data.get("newPassword")
    
    if not current_password or not new_password:
        return jsonify({"message": "Current and new passwords are required"}), 400
    
    user = db.session.get(User, session["user_id"])
    if not user:
        return jsonify({"message": "User not found"}), 404
    
    if not check_password_hash(user.password, current_password):
        return jsonify({"message": "Current password is incorrect"}), 400
    
    if len(new_password) < 8:
        return jsonify({"message": "Password must be at least 8 characters"}), 400
    
    if not re.search(r'[A-Z]', new_password):
        return jsonify({"message": "Password must contain at least one uppercase letter"}), 400
    
    if not re.search(r'[0-9]', new_password):
        return jsonify({"message": "Password must contain at least one number"}), 400
    
    if not re.search(r'[^A-Za-z0-9]', new_password):
        return jsonify({"message": "Password must contain at least one special character"}), 400
    
    try:
        user.password = generate_password_hash(new_password)
        db.session.commit()
        
        try:
            email_subject = "Your Password Has Been Changed"
            current_year = datetime.now().year
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta name="x-apple-disable-message-reformatting">
                <meta name="color-scheme" content="light dark">
                <meta name="supported-color-schemes" content="light dark">
                <title>Password Change Confirmation</title>
                <style type="text/css">
                    body {{ width: 100% !important; margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
                    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
                    td {{ border-collapse: collapse; }}
                    img {{ border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }}
                    a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: none !important; font-size: inherit !important; font-family: inherit !important; font-weight: inherit !important; line-height: inherit !important; }}
                    .button:hover {{ background-color: #388e3c !important; }}
                    @media only screen and (max-width: 600px) {{
                        .container {{ width: 100% !important; padding: 10px !important; }}
                        .button {{ width: 100% !important; display: block !important; }}
                        .header img {{ max-width: 120px !important; }}
                        .content {{ padding: 20px !important; }}
                        .footer {{ padding: 10px !important; font-size: 11px !important; }}
                    }}
                </style>
            </head>
            <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f5f5f5; color: #333333;">
                <table role="presentation" width="100%" style="background-color: #f5f5f5;" cellpadding="0" cellspacing="0">
                    <tr>
                        <td align="center">
                            <table role="presentation" class="container" width="600" style="margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden;" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td class="header" style="background-color: #4caf50; padding: 20px; text-align: center;">
                                        <img src="https://jetcamstudio.com/wp-content/uploads/2023/04/Untitled-9-1-2.png" alt="JetCam Studio Logo" style="max-width: 150px; height: auto; border: 0;">
                                    </td>
                                </tr>
                                <tr>
                                    <td class="content" style="padding: 30px;">
                                        <h1 style="margin: 0 0 15px; font-size: 24px; font-weight: 600; color: #202124;">Password Changed Successfully</h1>
                                        <p style="margin: 0 0 20px; font-size: 16px; line-height: 24px; color: #444444;">Your JetCam Studio account password has been successfully changed.</p>
                                        <p style="margin: 0 0 20px; font-size: 14px; color: #666666;">If you did not initiate this change, please contact <a href="mailto:support@jetcamstudio.com" style="color: #4caf50; text-decoration: none;">support@jetcamstudio.com</a> immediately.</p>
                                    </td>
                                </tr>
                                <tr>
                                    <td class="footer" style="padding: 15px; background-color: #f5f5f5; text-align: center; font-size: 12px; color: #666666;">
                                        <p style="margin: 0;">Â© {current_year} JetCam Studio. All rights reserved.</p>
                                        <p style="margin: 5px 0 0;">This is an automated message. Please do not reply.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </body>
            </html>
            """
            email_service.send_email(user.email, email_subject, html_content)
            current_app.logger.info(f"Password change confirmation email sent to {user.email}")
        except Exception as e:
            current_app.logger.error(f"Failed to send password change confirmation email: {str(e)}")
        
        return jsonify({"message": "Password changed successfully"}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Password change error: {str(e)}")
        return jsonify({"message": "An error occurred processing your request"}), 500

@auth_bp.route("/api/update-profile", methods=["POST"])
@login_required
def update_profile():
    data = request.get_json()
    
    user = db.session.get(User, session["user_id"])
    if not user:
        return jsonify({"message": "User not found"}), 404
    
    if "name" in data:
        user.name = data["name"]
    
    if "bio" in data:
        user.bio = data["bio"]
    
    if "receive_updates" in data:
        user.receive_updates = data["receive_updates"]
    
    if "telegram_username" in data:
        telegram_username = data["telegram_username"].strip() if data["telegram_username"] else None
        if telegram_username and not telegram_username.startswith('@'):
            return jsonify({"message": "Telegram username must start with @"}), 400
        if telegram_username and User.query.filter(User.telegram_username == telegram_username, User.id != user.id).first():
            return jsonify({"message": "Telegram username already taken"}), 400
        user.telegram_username = telegram_username
    
    if "telegram_chat_id" in data:
        user.telegram_chat_id = data["telegram_chat_id"].strip() if data["telegram_chat_id"] else None
    
    try:
        db.session.commit()
        return jsonify({
            "message": "Profile updated successfully",
            "user": user.serialize()
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Profile update error: {str(e)}")
        return jsonify({"message": "An error occurred updating your profile"}), 500

@auth_bp.route('/api/user/telegram', methods=['GET'])
def get_telegram_details():
    user = db.session.get(User, session["user_id"])
    if not user:
        return jsonify({"message": "User not found"}), 404
    return jsonify({
        "telegram_username": user.telegram_username or '',
        "chat_id": user.telegram_chat_id or '',
        "receive_updates": user.receive_updates or False
    }), 200

@auth_bp.route('/api/user/telegram', methods=['POST'])
def update_telegram_details():
    data = request.get_json()
    telegram_username = data.get('telegram_username', '').strip() if data.get('telegram_username') else None
    chat_id = data.get('chat_id', '').strip() if data.get('chat_id') else None
    receive_updates = data.get('receive_updates', False)

    user = db.session.get(User, session["user_id"])
    if not user:
        return jsonify({"message": "User not found"}), 404

    if telegram_username:
        if not telegram_username.startswith('@'):
            return jsonify({"message": "Telegram username must start with @"}), 400
        if User.query.filter(User.telegram_username == telegram_username, User.id != user.id).first():
            return jsonify({"message": "Telegram username already taken"}), 400
        user.telegram_username = telegram_username
    else:
        user.telegram_username = None

    user.telegram_chat_id = chat_id
    user.receive_updates = receive_updates

    try:
        db.session.commit()
        return jsonify({
            "message": "Telegram details updated successfully",
            "telegram_username": user.telegram_username or '',
            "chat_id": user.telegram_chat_id or '',
            "receive_updates": user.receive_updates
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Telegram details update error: {str(e)}")
        return jsonify({"message": "An error occurred updating Telegram details"}), 500