# utils/auth.py

from functools import wraps
from flask import session, jsonify, request

def login_required(f=None, role=None):
    """
    Decorator to restrict access to authenticated users with optional role check
    Can be used as @login_required or @login_required(role="admin") or @login_required(role=["admin", "agent"])
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                return jsonify({"message": "Authentication required"}), 401
            
            if role is not None:
                user_role = session.get("user_role")
                
                # Handle both single role (string) and multiple roles (list)
                if isinstance(role, list):
                    if user_role not in role:
                        allowed_roles = ", ".join(r.capitalize() for r in role)
                        return jsonify({"message": f"{allowed_roles} privileges required"}), 403
                else:
                    if user_role != role:
                        return jsonify({"message": f"{role.capitalize()} privileges required"}), 403
                
            return f(*args, **kwargs)
        return decorated_function
    
    # Handle both @login_required and @login_required(role="admin") usage
    if f is None:
        return decorator
    else:
        return decorator(f)

def admin_required(f):
    """
    Decorator to restrict access to admin users only
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"message": "Authentication required"}), 401
        
        if session.get("user_role") != "admin":
            return jsonify({"message": "Admin privileges required"}), 403
        
        return f(*args, **kwargs)
    return decorated_function

def api_key_required(f):
    """
    Decorator to authenticate API requests using API key
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        
        if not api_key:
            return jsonify({"message": "API key required"}), 401
        
        # Here you would validate the API key against your database
        # This is a simplified example
        valid_key = validate_api_key(api_key)
        
        if not valid_key:
            return jsonify({"message": "Invalid API key"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def validate_api_key(api_key):
    """
    Validate an API key against the database
    
    Args:
        api_key (str): The API key to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    # In a real implementation, you would check the key against your database
    # For example:
    # from models import ApiKey
    # return ApiKey.query.filter_by(key=api_key, is_active=True).first() is not None
    
    # This is just a placeholder
    return False