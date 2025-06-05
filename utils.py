import logging
from functools import wraps
from typing import Any, Callable, List, Optional, Union
from flask import jsonify, session
import requests

logger = logging.getLogger(__name__)

def login_required(roles: Optional[Union[str, List[str]]] = None) -> Callable:
    """
    Decorator to enforce authentication and optional role-based authorization.
    
    Args:
        roles: A single role name or list of role names permitted to access the endpoint.
               If None or empty, any authenticated user is allowed.
    """
    # Normalize roles to a list for membership checks
    if isinstance(roles, str):
        allowed_roles: List[str] = [roles]
    elif isinstance(roles, list):
        allowed_roles = roles
    else:
        allowed_roles = []

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user_id = session.get("user_id")
            user_role = session.get("user_role")

            # Authentication check
            if not user_id:
                logger.warning("Unauthorized access attempt to %s", f.__name__)
                resp = jsonify({
                    "error": "Unauthorized",
                    "message": "Authentication required."
                })
                resp.status_code = 401
                return resp

            # Authorization check (if roles were specified)
            if allowed_roles and user_role not in allowed_roles:
                logger.warning(
                    "Permission denied for user %s with role %s (requires one of: %s) for %s",
                    user_id, user_role, ", ".join(allowed_roles), f.__name__
                )
                resp = jsonify({
                    "error": "Forbidden",
                    "message": "You do not have permission to perform this action."
                })
                resp.status_code = 403
                return resp

            # Passed all checks — proceed to the view function
            return f(*args, **kwargs)

        return wrapped
    return decorator

def get_stripchat_viewers(streamer_username):
    """
    Fetch the number of viewers for a Stripchat livestream.
    Args:
        streamer_username (str): The username of the streamer.
    Returns:
        int: Number of viewers (guests) if successful, None otherwise.
    """
    url = f'https://stripchat.com/api/front/v2/models/username/{streamer_username}/members'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Origin': 'http://localhost:8080',
        'Alt-Used': 'stripchat.com',
        'Connection': 'keep-alive',
        'Referer': 'http://localhost:8080/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        'TE': 'trailers'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('guests', 0)
        else:
            print(f"Failed to fetch viewers for {streamer_username}. Status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching viewers for {streamer_username}: {str(e)}")
        return None