# utils/email.py

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def send_email(to_email, subject, html_content, from_email=None):
    """
    Send an email with HTML content to a single recipient
    
    Args:
        to_email (str): Recipient email address
        subject (str): Email subject
        html_content (str): HTML content of the email
        from_email (str, optional): Sender email address. Defaults to env var or app config.
    
    Returns:
        bool: True if email sent successfully
    
    Raises:
        RuntimeError: If email sending fails
    """
    # Get SMTP settings from environment variables with app config fallback
    smtp_server = os.getenv('MAIL_SERVER') or current_app.config.get('MAIL_SERVER')
    smtp_port = int(os.getenv('MAIL_PORT') or current_app.config.get('MAIL_PORT', 587))
    smtp_username = os.getenv('MAIL_USERNAME') or current_app.config.get('MAIL_USERNAME')
    smtp_password = os.getenv('MAIL_PASSWORD') or current_app.config.get('MAIL_PASSWORD')
    use_tls = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', '1', 't') or current_app.config.get('MAIL_USE_TLS', True)
    use_ssl = os.getenv('MAIL_USE_SSL', 'False').lower() in ('true', '1', 't') or current_app.config.get('MAIL_USE_SSL', False)
    
    # Get sender from env vars, function param, or app config
    sender = from_email or os.getenv('MAIL_DEFAULT_SENDER') or current_app.config.get('MAIL_DEFAULT_SENDER')
    
    if not smtp_server or not smtp_port:
        raise ValueError("SMTP server configuration missing")
    
    if not sender:
        raise ValueError("Sender email not specified")
    
    # Create message
    message = MIMEMultipart('alternative')
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = to_email
    
    # Attach HTML content
    html_part = MIMEText(html_content, 'html')
    message.attach(html_part)
    
    try:
        # Choose appropriate SMTP class based on SSL setting
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        
        # Connect to SMTP server
        server = smtp_class(smtp_server, smtp_port)
        
        if use_tls and not use_ssl:
            server.starttls()
        
        # Login if credentials provided
        if smtp_username and smtp_password:
            server.login(smtp_username, smtp_password)
        
        # Send email
        server.sendmail(sender, to_email, message.as_string())
        server.quit()
        
        current_app.logger.info(f"Email sent to {to_email}: {subject}")
        return True
        
    except Exception as e:
        error_msg = f"Failed to send email: {str(e)}"
        current_app.logger.error(error_msg)
        raise RuntimeError(error_msg)


# Email templates for common scenarios
def send_welcome_email(user_email, username):
    """
    Send welcome email to newly registered user
    
    Args:
        user_email (str): User's email address
        username (str): User's username
        
    Returns:
        bool: True if email sent successfully
    """
    subject = "Welcome to Live Stream Monitoring"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4CAF50; color: white; padding: 10px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9f9f9; }}
            .footer {{ font-size: 12px; text-align: center; margin-top: 30px; color: #777; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to Live Stream Monitoring!</h1>
            </div>
            <div class="content">
                <h2>Hello {username}!</h2>
                <p>Thank you for creating an account with us. We're excited to have you onboard!</p>
                <p>You can now log in to your account and start monitoring your streams.</p>
                <p>If you have any questions or need assistance, feel free to contact our support team.</p>
            </div>
            <div class="footer">
                <p>This is an automated message. Please do not reply to this email.</p>
                <p>&copy; {2025} Live Stream Monitoring. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return send_email(user_email, subject, html_content)


def send_password_reset_email(user_email, token):
    """
    Send password reset email with reset link
    
    Args:
        user_email (str): User's email address
        token (str): Password reset token
        
    Returns:
        bool: True if email sent successfully
    """
    # Generate reset URL for the deployed application
    reset_url = f"http://live-stream-monitoring-vue3-flask.vercel.app/reset-password?token={token}"
    
    subject = "Password Reset Request"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2196F3; color: white; padding: 10px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9f9f9; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: #2196F3; 
                     color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
            .warning {{ color: #f44336; }}
            .footer {{ font-size: 12px; text-align: center; margin-top: 30px; color: #777; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Password Reset Request</h1>
            </div>
            <div class="content">
                <h2>Reset Your Password</h2>
                <p>We received a request to reset your password for your Live Stream Monitoring account. Click the button below to create a new password:</p>
                <p style="text-align: center;">
                    <a href="{reset_url}" class="button">Reset Password</a>
                </p>
                <p>Or copy and paste this link into your browser:</p>
                <p style="background-color: #eee; padding: 10px; word-break: break-all;">{reset_url}</p>
                <p>This link will expire in 1 hour for security reasons.</p>
                <p class="warning">If you didn't request a password reset, please ignore this email or contact our support team if you have concerns.</p>
            </div>
            <div class="footer">
                <p>This is an automated message. Please do not reply to this email.</p>
                <p>&copy; {2025} Live Stream Monitoring. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return send_email(user_email, subject, html_content)