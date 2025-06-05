# email_debug.py
import os
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys

def check_smtp_configuration():
    """Print out SMTP configuration and test the connection"""
    # Load environment variables
    load_dotenv()
    
    # Get SMTP settings from environment variables
    smtp_server = os.getenv('MAIL_SERVER')
    smtp_port = os.getenv('MAIL_PORT')
    smtp_username = os.getenv('MAIL_USERNAME')
    smtp_password = os.getenv('MAIL_PASSWORD')
    use_tls = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
    use_ssl = os.getenv('MAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
    default_sender = os.getenv('MAIL_DEFAULT_SENDER')
    
    # Print configuration (hiding password)
    print("SMTP Configuration:")
    print(f"- Server: {smtp_server}")
    print(f"- Port: {smtp_port}")
    print(f"- Username: {smtp_username}")
    print(f"- Password: {'*' * 8 if smtp_password else 'Not set'}")
    print(f"- Use TLS: {use_tls}")
    print(f"- Use SSL: {use_ssl}")
    print(f"- Default Sender: {default_sender}")
    
    if not all([smtp_server, smtp_port, smtp_username, smtp_password, default_sender]):
        print("\n❌ ERROR: Missing SMTP configuration values!")
        return False
    
    # Test SMTP connection
    print("\nTesting SMTP connection...")
    try:
        # Choose appropriate SMTP class based on SSL setting
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        
        # Connect to SMTP server
        server = smtp_class(smtp_server, int(smtp_port))
        
        if use_tls and not use_ssl:
            server.starttls()
        
        # Login if credentials provided
        server.login(smtp_username, smtp_password)
        
        print("✅ SMTP connection successful!")
        server.quit()
        return True
        
    except Exception as e:
        print(f"❌ SMTP connection failed: {str(e)}")
        return False

def send_test_email(to_email):
    """Send a test email to verify the configuration"""
    load_dotenv()
    
    # Get SMTP settings
    smtp_server = os.getenv('MAIL_SERVER')
    smtp_port = int(os.getenv('MAIL_PORT', '587'))
    smtp_username = os.getenv('MAIL_USERNAME')
    smtp_password = os.getenv('MAIL_PASSWORD')
    use_tls = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
    use_ssl = os.getenv('MAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
    sender = os.getenv('MAIL_DEFAULT_SENDER')
    
    # Create message
    message = MIMEMultipart('alternative')
    message['Subject'] = "Test Email from Live Stream Monitoring"
    message['From'] = sender
    message['To'] = to_email
    
    # Create HTML content
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; }
            .container { padding: 20px; }
            .header { background-color: #4CAF50; color: white; padding: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Test Email</h1>
            </div>
            <p>This is a test email to verify SMTP configuration.</p>
            <p>If you received this, your email configuration is working correctly.</p>
        </div>
    </body>
    </html>
    """
    
    # Attach HTML content
    html_part = MIMEText(html_content, 'html')
    message.attach(html_part)
    
    try:
        # Choose appropriate SMTP class
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        
        # Connect to SMTP server
        server = smtp_class(smtp_server, smtp_port)
        
        if use_tls and not use_ssl:
            server.starttls()
        
        # Login if credentials provided
        server.login(smtp_username, smtp_password)
        
        # Send email
        server.sendmail(sender, to_email, message.as_string())
        server.quit()
        
        print(f"✅ Test email sent to {to_email} successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send test email: {str(e)}")
        return False

if __name__ == "__main__":
    if check_smtp_configuration():
        print("\nSMTP configuration looks good!")
        
        if len(sys.argv) > 1:
            test_email = sys.argv[1]
            print(f"\nSending test email to {test_email}...")
            send_test_email(test_email)
        else:
            print("\nTo send a test email, run: python email_debug.py your@email.com")
    else:
        print("\nPlease fix the SMTP configuration issues before testing email sending.")