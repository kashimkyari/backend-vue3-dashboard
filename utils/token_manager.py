# utils/token_manager.py

import random
import time
from datetime import datetime, timedelta
import hashlib
import logging

logger = logging.getLogger(__name__)

class TokenManager:
    """Manages secure token generation and validation for password resets"""
    
    def __init__(self, db_connection=None, token_expiry_hours=1):
        """Initialize the token manager
        
        Args:
            db_connection: Database connection object
            token_expiry_hours: Number of hours until token expires (default: 1)
        """
        self.db = db_connection
        self.token_expiry_hours = token_expiry_hours
    
    def generate_reset_token(self, user_id, user_email):
        """Generate a secure 6-digit token for password reset
        
        Args:
            user_id: User's ID in the database
            user_email: User's email address
            
        Returns:
            str: The generated 6-digit token
        """
        # Generate a secure 6-digit token
        token = str(random.randint(100000, 999999))
        
        # Create a timestamp for expiration
        expiry_time = datetime.now() + timedelta(hours=self.token_expiry_hours)
        timestamp = int(expiry_time.timestamp())
        
        # Create token hash for storage (we'll store the hash, not the raw token)
        token_hash = self._hash_token(token)
        
        # Store token in database with user_id and expiration
        self._store_token(user_id, token_hash, timestamp)
        
        # Return the raw token to be included in the email
        return token
    
    def verify_token(self, token, user_email):
        """Verify if a token is valid for the given user
        
        Args:
            token: The token to verify
            user_email: User's email address
            
        Returns:
            bool: True if token is valid, False otherwise
        """
        # Hash the token
        token_hash = self._hash_token(token)
        
        # Get user ID from email
        user_id = self._get_user_id_from_email(user_email)
        if not user_id:
            logger.warning(f"No user found for email: {user_email}")
            return False
        
        # Check if token exists and is valid
        stored_token = self._get_token(user_id, token_hash)
        if not stored_token:
            logger.warning(f"No valid token found for user ID: {user_id}")
            return False
        
        # Check if token is expired
        current_time = int(datetime.now().timestamp())
        if current_time > stored_token['expires_at']:
            logger.warning(f"Token expired for user ID: {user_id}")
            self._invalidate_token(user_id, token_hash)
            return False
        
        return True
    
    def _hash_token(self, token):
        """Create a hash of the token for secure storage
        
        Args:
            token: Raw token string
            
        Returns:
            str: Hashed token
        """
        return hashlib.sha256(token.encode()).hexdigest()
    
    def _store_token(self, user_id, token_hash, expires_at):
        """Store token hash in database with expiration
        
        Args:
            user_id: User's ID
            token_hash: Hashed token
            expires_at: Expiration timestamp
        """
        # Implement database storage based on your DB system
        # This is a placeholder for your specific database implementation
        if self.db:
            # First invalidate any existing tokens for this user
            self._invalidate_all_user_tokens(user_id)
            
            # Store new token
            # Example SQL: INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)
            try:
                current_time = int(datetime.now().timestamp())
                self.db.execute(
                    "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, token_hash, expires_at, current_time)
                )
                self.db.commit()
                logger.info(f"Reset token stored for user ID: {user_id}")
            except Exception as e:
                logger.error(f"Error storing reset token: {str(e)}")
                raise
    
    def _get_token(self, user_id, token_hash):
        """Retrieve token information from database
        
        Args:
            user_id: User's ID
            token_hash: Hashed token
            
        Returns:
            dict: Token data or None if not found
        """
        # Implement database retrieval based on your DB system
        if self.db:
            try:
                result = self.db.execute(
                    "SELECT * FROM password_reset_tokens WHERE user_id = ? AND token_hash = ? AND is_used = 0",
                    (user_id, token_hash)
                ).fetchone()
                
                if result:
                    return {
                        'id': result[0],
                        'user_id': result[1],
                        'token_hash': result[2],
                        'expires_at': result[3],
                        'is_used': result[4]
                    }
            except Exception as e:
                logger.error(f"Error retrieving token: {str(e)}")
        
        return None
    
    def _invalidate_token(self, user_id, token_hash):
        """Mark a token as used/invalid
        
        Args:
            user_id: User's ID
            token_hash: Hashed token
        """
        if self.db:
            try:
                self.db.execute(
                    "UPDATE password_reset_tokens SET is_used = 1 WHERE user_id = ? AND token_hash = ?",
                    (user_id, token_hash)
                )
                self.db.commit()
                logger.info(f"Token invalidated for user ID: {user_id}")
            except Exception as e:
                logger.error(f"Error invalidating token: {str(e)}")
    
    def _invalidate_all_user_tokens(self, user_id):
        """Invalidate all existing tokens for a user
        
        Args:
            user_id: User's ID
        """
        if self.db:
            try:
                self.db.execute(
                    "UPDATE password_reset_tokens SET is_used = 1 WHERE user_id = ?",
                    (user_id,)
                )
                self.db.commit()
                logger.info(f"All tokens invalidated for user ID: {user_id}")
            except Exception as e:
                logger.error(f"Error invalidating user tokens: {str(e)}")
    
    def _get_user_id_from_email(self, email):
        """Get user ID from email address
        
        Args:
            email: User's email address
            
        Returns:
            int: User ID or None if not found
        """
        # Implement based on your user table structure
        if self.db:
            try:
                result = self.db.execute(
                    "SELECT id FROM users WHERE email = ?",
                    (email,)
                ).fetchone()
                
                if result:
                    return result[0]
            except Exception as e:
                logger.error(f"Error getting user ID: {str(e)}")
        
        return None