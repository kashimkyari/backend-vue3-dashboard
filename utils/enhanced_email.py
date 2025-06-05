import os
import smtplib
import time
import logging
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate
from datetime import datetime
from flask import current_app
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseEmailTemplate:
    """Base class for email templates with shared styles and structure"""
    
    @staticmethod
    def get_base_styles():
        return """
            /* Reset styles */
            * { margin: 0; padding: 0; box-sizing: border-box; }
            
            /* Gmail-specific fixes */
            body, table, td, div, p, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
            table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; border-collapse: collapse !important; }
            img { -ms-interpolation-mode: bicubic; border: 0; display: block; height: auto; outline: none; text-decoration: none; }
            
            /* Base styles */
            body {
                margin: 0 !important;
                padding: 0 !important;
                width: 100% !important;
                min-width: 100% !important;
                background-color: #f5f5f5;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            }
            
            /* Container styles */
            .email-container {
                max-width: 600px !important;
                margin: 0 auto !important;
            }
            
            /* Typography */
            h1 { font-size: 28px; line-height: 36px; margin-bottom: 16px; }
            h2 { font-size: 24px; line-height: 32px; margin-bottom: 12px; }
            p { font-size: 16px; line-height: 24px; margin-bottom: 16px; }
            
            /* Buttons */
            .button {
                display: inline-block;
                padding: 12px 24px;
                background-color: #1a73e8;
                color: #ffffff !important;
                text-decoration: none;
                border-radius: 4px;
                font-weight: 500;
                text-align: center;
                transition: background-color 0.3s ease;
            }
            .button:hover { background-color: #1557b0; }
            
            /* Dark mode support */
            @media (prefers-color-scheme: dark) {
                .darkmode-bg { background-color: #1f1f1f !important; }
                .darkmode-text { color: #ffffff !important; }
                .darkmode-content { background-color: #2d2d2d !important; }
                .darkmode-button {
                    background-color: #4285f4 !important;
                    border-color: #4285f4 !important;
                }
            }
            
            /* Mobile responsiveness */
            @media screen and (max-width: 600px) {
                .fluid { width: 100% !important; max-width: 100% !important; }
                .stack { display: block !important; width: 100% !important; }
                .pt { padding-top: 20px !important; }
                .pb { padding-bottom: 20px !important; }
                .px { padding-left: 20px !important; padding-right: 20px !important; }
            }
            
            /* Custom brand colors */
            .primary-bg { background-color: #1a73e8; }
            .warning-bg { background-color: #e65100; }
            .success-bg { background-color: #0b8043; }
            .danger-bg { background-color: #d32f2f; }
            
            /* Additional utilities */
            .rounded { border-radius: 8px; }
            .shadow { box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .mt-0 { margin-top: 0; }
            .mb-0 { margin-bottom: 0; }
        """
    
    @staticmethod
    def get_base_template():
        return """
        <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
        <html xmlns="http://www.w3.org/1999/xhtml" lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta name="x-apple-disable-message-reformatting">
            <meta name="format-detection" content="telephone=no,address=no,email=no,date=no">
            <meta name="color-scheme" content="light dark">
            <meta name="supported-color-schemes" content="light dark">
            <title>{title}</title>
            <style type="text/css">
                {styles}
            </style>
            <!--[if mso]>
            <style type="text/css">
                table, td, div, p, a, h1, h2, h3 {{ font-family: Arial, sans-serif !important; }}
            </style>
            <![endif]-->
        </head>
        <body class="darkmode-bg">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f5f5f5;">
                <tr>
                    <td>
                        <table role="presentation" class="email-container" align="center" cellspacing="0" cellpadding="0" border="0" width="600" style="margin: auto;">
                            <!-- Header -->
                            <tr>
                                <td class="primary-bg" style="padding: 20px; text-align: center;">
                                    <img src="https://jetcamstudio.com/wp-content/uploads/2023/04/Untitled-9-1-2.png" 
                                         alt="JetCam Studio" 
                                         width="150" 
                                         style="max-width: 150px; height: auto;">
                                </td>
                            </tr>
                            
                            <!-- Content -->
                            <tr>
                                <td class="darkmode-content" style="background-color: #ffffff; padding: 40px 30px; border-radius: 0 0 8px 8px;">
                                    {content}
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td style="padding: 20px; text-align: center; color: #666666; font-size: 12px;">
                                    <p style="margin: 0;">© {year} JetCam Studio. All rights reserved.</p>
                                    <p style="margin: 5px 0 0;">This is an automated message, please do not reply.</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

class EmailService:
    """Enhanced email service with retry capability and better error handling"""
    
    def __init__(self, app=None):
        """Initialize the email service with Flask app or environment variables"""
        self.app = app
        
        # Default SMTP settings
        self.smtp_server = None
        self.smtp_port = None
        self.username = None
        self.password = None
        self.use_tls = True
        self.use_ssl = False
        self.default_sender = None
        self.sender_name = None
        
        # Max retries for sending emails
        self.max_retries = 3
        self.retry_delay = 2  # seconds
        
        if app:
            self.init_app(app)
        else:
            self._load_config_from_env()
    
    def init_app(self, app):
        """Initialize with Flask application"""
        self.app = app
        self._load_config_from_app()
    
    def _load_config_from_env(self):
        """Load configuration from environment variables"""
        self.smtp_server = os.getenv('MAIL_SERVER')
        self.smtp_port = int(os.getenv('MAIL_PORT', '587'))
        self.username = os.getenv('MAIL_USERNAME')
        self.password = os.getenv('MAIL_PASSWORD')
        self.use_tls = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
        self.use_ssl = os.getenv('MAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
        self.default_sender = os.getenv('MAIL_DEFAULT_SENDER')
        self.sender_name = os.getenv('MAIL_SENDER_NAME', 'JetCam Studio')
        
        # Load retry configuration
        self.max_retries = int(os.getenv('MAIL_MAX_RETRIES', '3'))
        self.retry_delay = int(os.getenv('MAIL_RETRY_DELAY', '2'))
    
    def _load_config_from_app(self):
        """Load configuration from Flask application"""
        if not self.app:
            return
            
        self.smtp_server = self.app.config.get('MAIL_SERVER')
        self.smtp_port = int(self.app.config.get('MAIL_PORT', 587))
        self.username = self.app.config.get('MAIL_USERNAME')
        self.password = self.app.config.get('MAIL_PASSWORD')
        self.use_tls = self.app.config.get('MAIL_USE_TLS', True)
        self.use_ssl = self.app.config.get('MAIL_USE_SSL', False)
        self.default_sender = self.app.config.get('MAIL_DEFAULT_SENDER')
        self.sender_name = self.app.config.get('MAIL_SENDER_NAME', 'JetCam Studio')
        
        # Load retry configuration
        self.max_retries = int(self.app.config.get('MAIL_MAX_RETRIES', 3))
        self.retry_delay = int(self.app.config.get('MAIL_RETRY_DELAY', 2))
    
    def validate_config(self):
        """Validate that required configuration is present"""
        missing = []
        
        if not self.smtp_server:
            missing.append('MAIL_SERVER')
        if not self.smtp_port:
            missing.append('MAIL_PORT')
        if not self.username:
            missing.append('MAIL_USERNAME')
        if not self.password:
            missing.append('MAIL_PASSWORD')
        if not self.default_sender:
            missing.append('MAIL_DEFAULT_SENDER')
        
        if missing:
            raise ValueError(f"Missing required email configuration: {', '.join(missing)}")
        
        return True
    
    def send_email(self, to_email, subject, html_content, from_email=None, sender_name=None, retry=True):
        """
        Send an email with HTML content to a recipient
        
        Args:
            to_email (str): Recipient email address
            subject (str): Email subject
            html_content (str): HTML content of the email
            from_email (str, optional): Sender email address
            sender_name (str, optional): Name to display as sender
            retry (bool): Whether to retry sending on failure
            
        Returns:
            bool: True if email sent successfully
            
        Raises:
            ValueError: If configuration is missing
            RuntimeError: If email sending fails after retries
        """
        # Validate configuration
        self.validate_config()
        
        # Get sender email and name
        sender_email = from_email or self.default_sender
        display_name = sender_name or self.sender_name
        
        # Format sender with name if provided
        if display_name:
            formatted_sender = formataddr((display_name, sender_email))
        else:
            formatted_sender = sender_email
        
        # Create message
        message = MIMEMultipart('alternative')
        message['Subject'] = subject
        message['From'] = formatted_sender
        message['To'] = to_email
        message['Date'] = formatdate(localtime=True)
        message['Message-ID'] = f"<{int(time.time())}@{sender_email.split('@')[1]}>"
        
        # Add Reply-To header if different from sender
        if from_email and from_email != self.default_sender:
            message['Reply-To'] = from_email
        
        # Attach HTML content
        html_part = MIMEText(html_content, 'html')
        message.attach(html_part)
        
        # Add plain text alternative (basic version of the HTML)
        plain_text = self._html_to_plain_text(html_content)
        text_part = MIMEText(plain_text, 'plain')
        message.attach(text_part)
        
        # Add some headers to reduce spam classification
        message['X-Mailer'] = 'JetCam Studio App'
        
        # Attempt to send email with retries if enabled
        attempts = 0
        max_attempts = self.max_retries if retry else 1
        
        while attempts < max_attempts:
            attempts += 1
            try:
                return self._send(message, sender_email, to_email)
            except Exception as e:
                logger.error(f"Email sending attempt {attempts} failed: {str(e)}")
                
                if attempts < max_attempts:
                    logger.info(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    error_msg = f"Failed to send email after {max_attempts} attempts: {str(e)}"
                    logger.error(error_msg)
                    if self.app:
                        current_app.logger.error(error_msg)
                    raise RuntimeError(error_msg)
    
    def _send(self, message, sender_email, to_email):
        """Internal method to send the email via SMTP"""
        # Choose appropriate SMTP class based on SSL setting
        smtp_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        
        # Connect to SMTP server
        server = smtp_class(self.smtp_server, self.smtp_port)
        
        try:
            # Start TLS if needed
            if self.use_tls and not self.use_ssl:
                server.starttls()
            
            # Login
            server.login(self.username, self.password)
            
            # Send email
            server.sendmail(sender_email, to_email, message.as_string())
            
            log_msg = f"Email sent to {to_email}: {message['Subject']}"
            logger.info(log_msg)
            if self.app:
                current_app.logger.info(log_msg)
                
            return True
        finally:
            # Always close the connection
            server.quit()
    
    def _html_to_plain_text(self, html):
        """Convert HTML to plain text (basic implementation)"""
        text = html.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        text = text.replace('<p>', '\n').replace('</p>', '\n')
        text = text.replace('<div>', '\n').replace('</div>', '\n')
        text = text.replace('<h1>', '\n\n').replace('</h1>', '\n')
        text = text.replace('<h2>', '\n\n').replace('</h2>', '\n')
        text = text.replace('<h3>', '\n\n').replace('</h3>', '\n')
        text = text.replace('<li>', '\n- ').replace('</li>', '')
        
        # Remove all other HTML tags
        import re
        text = re.sub(r'<[^>]*>', '', text)
        
        # Replace multiple newlines with just two
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()

def send_welcome_email(user_email, username):
    """Send welcome email to newly registered user"""
    email_service = EmailService()
    current_year = datetime.now().year
    
    welcome_content = f"""
        <h1 style="color: #202124;" class="darkmode-text">Welcome to JetCam Studio, {username}! 👋</h1>
        
        <p style="color: #444444;" class="darkmode-text">
            We're thrilled to have you on board. Your account is ready, and you can now start monitoring 
            your streams by accessing your dashboard.
        </p>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 30px 0;">
            <tr>
                <td align="center">
                    <a href="https://monitor.jetcamstudio.com" 
                       class="button darkmode-button"
                       style="min-width: 200px;">
                        Go to Dashboard
                    </a>
                </td>
            </tr>
        </table>

        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" 
               style="margin: 30px 0; background-color: #f8f9fa; border-radius: 8px;" class="darkmode-content">
            <tr>
                <td style="padding: 20px;">
                    <h2 style="color: #202124; margin-top: 0;" class="darkmode-text">Key Features</h2>
                    
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                        <tr>
                            <td style="padding: 10px 0;">
                                <p style="margin: 0; color: #444444;" class="darkmode-text">
                                    <strong style="color: #1a73e8;">🔍 Stream Monitoring</strong><br>
                                    Detect policy violations to keep your channel safe
                                </p>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 0;">
                                <p style="margin: 0; color: #444444;" class="darkmode-text">
                                    <strong style="color: #1a73e8;">💬 Chat Analysis</strong><br>
                                    Filter inappropriate content in real-time
                                </p>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 0;">
                                <p style="margin: 0; color: #444444;" class="darkmode-text">
                                    <strong style="color: #1a73e8;">🚨 Instant Alerts</strong><br>
                                    Stay informed with immediate notifications
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>

        <p style="color: #666666; font-size: 14px;" class="darkmode-text">
            Need help? Contact us at 
            <a href="mailto:support@jetcamstudio.com" 
               style="color: #1a73e8; text-decoration: none;">
                support@jetcamstudio.com
            </a>
        </p>
    """
    
    template = BaseEmailTemplate.get_base_template()
    html_content = template.format(
        title="Welcome to JetCam Studio",
        styles=BaseEmailTemplate.get_base_styles(),
        content=welcome_content,
        year=current_year
    )
    
    return email_service.send_email(
        user_email,
        "Welcome to JetCam Studio! 🎉",
        html_content
    )

def generate_six_digit_token():
    """Generate a secure 6-digit token"""
    return str(random.randint(100000, 999999))

def send_password_reset_email(user_email, token):
    """Send password reset email with 6-digit token"""
    email_service = EmailService()
    current_year = datetime.now().year
    
    reset_content = f"""
        <h1 style="color: #202124;" class="darkmode-text">Reset Your Password</h1>
        
        <p style="color: #444444;" class="darkmode-text">
            We received a request to reset your password for your JetCam Studio account. 
            Use the code below to complete the password reset process.
        </p>
        
        <div style="background-color: #f8f9fa; border-radius: 8px; padding: 20px; margin: 30px 0; text-align: center;"
             class="darkmode-content">
            <p style="font-family: 'Courier New', monospace; font-size: 32px; letter-spacing: 5px; 
                      margin: 0; color: #202124; font-weight: bold;" class="darkmode-text">
                {token}
            </p>
        </div>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 30px 0;">
            <tr>
                <td align="center">
                    <a href="https://monitor.jetcamstudio.com/reset-password" 
                       class="button darkmode-button"
                       style="min-width: 200px;">
                        Reset Password
                    </a>
                </td>
            </tr>
        </table>
        
        <p style="color: #d32f2f; font-size: 14px; margin-top: 30px;" class="darkmode-text">
            If you didn't request this password reset, please ignore this email or contact 
            <a href="mailto:support@jetcamstudio.com" style="color: #d32f2f; text-decoration: none;">
                support@jetcamstudio.com
            </a>
        </p>
        
        <p style="color: #666666; font-size: 14px; margin-top: 20px;" class="darkmode-text">
            This code will expire in 1 hour for security reasons.
        </p>
    """
    
    template = BaseEmailTemplate.get_base_template()
    html_content = template.format(
        title="Reset Your Password",
        styles=BaseEmailTemplate.get_base_styles(),
        content=reset_content,
        year=current_year
    )
    
    return email_service.send_email(
        user_email,
        "Password Reset Code",
        html_content
    )

def send_notification_email(user_email, alert_type, stream_title, details, timestamp):
    """Send notification email for stream alerts"""
    email_service = EmailService()
    current_year = datetime.now().year
    
    alert_configs = {
        'audio': {
            'title': 'Audio Alert Detected',
            'icon': '🔊',
            'color': '#e65100'  # Orange
        },
        'visual': {
            'title': 'Visual Alert Detected',
            'icon': '👁️',
            'color': '#2962ff'  # Blue
        },
        'chat': {
            'title': 'Chat Alert Detected',
            'icon': '💬',
            'color': '#2e7d32'  # Green
        }
    }
    
    config = alert_configs.get(alert_type.lower(), {
        'title': 'Stream Alert',
        'icon': '⚠️',
        'color': '#d32f2f'  # Red
    })
    
    notification_content = f"""
        <div style="background-color: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 30px;"
             class="darkmode-content">
            <h1 style="color: {config['color']}; margin-top: 0;" class="darkmode-text">
                {config['icon']} {config['title']}
            </h1>
            <p style="color: #202124; margin: 0;" class="darkmode-text">
                <strong>Stream:</strong> {stream_title}
            </p>
            <p style="color: #202124; margin: 10px 0 0;" class="darkmode-text">
                <strong>Time:</strong> {timestamp}
            </p>
        </div>
        
        <div style="margin-bottom: 30px;">
            <h2 style="color: #202124; margin-top: 0;" class="darkmode-text">Alert Details</h2>
            <p style="color: #444444; white-space: pre-line;" class="darkmode-text">
                {details}
            </p>
        </div>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
            <tr>
                <td align="center">
                    <a href="https://monitor.jetcamstudio.com/dashboard" 
                       class="button darkmode-button"
                       style="min-width: 200px; background-color: {config['color']};">
                        View in Dashboard
                    </a>
                </td>
            </tr>
        </table>
    """
    
    template = BaseEmailTemplate.get_base_template()
    html_content = template.format(
        title=config['title'],
        styles=BaseEmailTemplate.get_base_styles(),
        content=notification_content,
        year=current_year
    )
    
    subject = f"{config['icon']} {config['title']} - {stream_title}"
    
    return email_service.send_email(
        user_email,
        subject,
        html_content
    )

# Create an instance for direct import
email_service = EmailService()