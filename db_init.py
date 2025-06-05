# db_init.py - Database initialization script
# Run this script to create tables in the correct order

from extensions import db
from models import (User, Stream, ChaturbateStream, StripchatStream, 
                    Assignment, Log, ChatKeyword, FlaggedObject, 
                    TelegramRecipient, DetectionLog, ChatMessage, 
                    PasswordReset, PasswordResetToken)
from flask import Flask
from config import create_app
import os
from dotenv import load_dotenv
import logging

def init_db():
    load_dotenv()
    app = create_app()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.FileHandler('db_init.log'), logging.StreamHandler()]
    )
    
    with app.app_context():
        # Drop all tables if they exist
        logging.info("Dropping all existing tables...")
        db.drop_all()
        
        logging.info("Creating tables in the correct order...")
        
        # Create tables in a specific order to respect foreign key constraints
        # 1. First, create independent tables with no foreign keys
        db.create_all(tables=[User.__table__, Stream.__table__, 
                             ChatKeyword.__table__, FlaggedObject.__table__,
                             TelegramRecipient.__table__])
        logging.info("Created base tables (User, Stream, etc.)")
        
        # 2. Create tables that depend on the base tables
        db.create_all(tables=[ChaturbateStream.__table__, StripchatStream.__table__])
        logging.info("Created platform-specific stream tables")
        
        # 3. Create tables with foreign keys to the base tables
        db.create_all(tables=[Assignment.__table__, PasswordReset.__table__, 
                             PasswordResetToken.__table__])
        logging.info("Created relationship tables (Assignment, PasswordReset, etc.)")
        
        # 4. Create tables with foreign keys to relationship tables
        db.create_all(tables=[DetectionLog.__table__, ChatMessage.__table__, Log.__table__])
        logging.info("Created log tables")
        
        # Create default admin user
        admin_exists = User.query.filter_by(role='admin').first()
        if not admin_exists:
            admin_user = User(
                username=os.getenv('DEFAULT_ADMIN_USERNAME', 'admin'),
                password=os.getenv('DEFAULT_ADMIN_PASSWORD', 'Admin@123!'),
                role='admin',
                email=os.getenv('DEFAULT_ADMIN_EMAIL', 'admin@example.com'),
                receive_updates=True
            )
            db.session.add(admin_user)
            db.session.commit()
            logging.info("Default admin user created")
        else:
            logging.info("Admin user already exists")
        
        logging.info("Database initialization complete!")

if __name__ == "__main__":
    init_db()