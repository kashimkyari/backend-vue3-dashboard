#!/usr/bin/env python3
"""
nuclear_drop_tables.py - Nuclear option for dropping all PostgreSQL database objects

This script uses the most aggressive approach possible to clean a PostgreSQL database:
1. Terminates all active connections to the database
2. Drops and recreates the entire schema
3. Handles edge cases and locked objects

WARNING: This will destroy ALL data in the database!
"""

import logging
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv
from sqlalchemy import text
from flask import Flask
from extensions import db
from config import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('nuclear_drop.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def get_db_connection_params():
    """Extract database connection parameters from environment."""
    load_dotenv()
    
    # Try to get from DATABASE_URL first
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        # Parse DATABASE_URL (format: postgresql://user:pass@host:port/dbname)
        import urllib.parse
        result = urllib.parse.urlparse(database_url)
        return {
            'host': result.hostname,
            'port': result.port or 5432,
            'database': result.path[1:],  # Remove leading '/'
            'user': result.username,
            'password': result.password
        }
    
    # Fallback to individual environment variables
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'database': os.getenv('DB_NAME', 'postgres'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', '')
    }

def terminate_database_connections(db_params, target_db):
    """Terminate all connections to the target database."""
    logger.info(f"Terminating all connections to database: {target_db}")
    
    # Connect to postgres database (not the target database)
    postgres_params = db_params.copy()
    postgres_params['database'] = 'postgres'
    
    try:
        conn = psycopg2.connect(**postgres_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Terminate all connections to the target database
        cursor.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity 
            WHERE datname = %s AND pid <> pg_backend_pid();
        """, (target_db,))
        
        terminated = cursor.fetchall()
        logger.info(f"Terminated {len(terminated)} connections")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Error terminating connections: {e}")

def nuclear_drop_database():
    """Nuclear option: Drop and recreate the entire database."""
    db_params = get_db_connection_params()
    target_database = db_params['database']
    
    logger.info(f"NUCLEAR OPTION: Completely dropping database '{target_database}'")
    logger.warning("This will destroy ALL data in the database!")
    
    # Terminate all connections to the target database
    terminate_database_connections(db_params, target_database)
    
    # Connect to postgres database to drop the target database
    postgres_params = db_params.copy()
    postgres_params['database'] = 'postgres'
    
    try:
        conn = psycopg2.connect(**postgres_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Drop the database
        logger.info(f"Dropping database: {target_database}")
        cursor.execute(f'DROP DATABASE IF EXISTS "{target_database}";')
        
        # Recreate the database
        logger.info(f"Recreating database: {target_database}")
        cursor.execute(f'CREATE DATABASE "{target_database}";')
        
        cursor.close()
        conn.close()
        
        logger.info("SUCCESS! Database has been completely recreated")
        
    except Exception as e:
        logger.error(f"Error during nuclear drop: {e}")
        raise

def nuclear_drop_schema_only():
    """Drop and recreate only the public schema (preserves database)."""
    load_dotenv()
    app = create_app()
    
    logger.info("NUCLEAR OPTION: Dropping and recreating public schema")
    
    with app.app_context():
        conn = db.engine.connect()
        
        try:
            # Terminate any active transactions
            conn.execute(text("SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE state = 'active' AND pid <> pg_backend_pid();"))
            
            # Drop the public schema and everything in it
            logger.info("Dropping public schema CASCADE")
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
            conn.commit()
            
            # Recreate the public schema
            logger.info("Recreating public schema")
            conn.execute(text("CREATE SCHEMA public;"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO postgres;"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            conn.commit()
            
            logger.info("SUCCESS! Public schema has been recreated")
            
        except Exception as e:
            logger.error(f"Error during schema drop: {e}")
            raise
        finally:
            conn.close()

def force_drop_individual_objects():
    """Force drop database objects one by one with maximum aggression."""
    load_dotenv()
    app = create_app()
    
    with app.app_context():
        conn = db.engine.connect()
        
        try:
            # Set aggressive timeouts
            conn.execute(text("SET statement_timeout = '5s';"))
            conn.execute(text("SET lock_timeout = '3s';"))
            
            schema = 'public'
            
            # Get all object types and force drop them
            logger.info("Force dropping all database objects...")
            
            # Drop materialized views first
            mat_views_query = text("""
                SELECT schemaname, matviewname 
                FROM pg_matviews 
                WHERE schemaname = :schema;
            """)
            
            mat_views = conn.execute(mat_views_query, {'schema': schema}).fetchall()
            for schema_name, view_name in mat_views:
                try:
                    logger.info(f"Force dropping materialized view: {view_name}")
                    conn.execute(text(f'DROP MATERIALIZED VIEW IF EXISTS "{view_name}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping materialized view {view_name}: {e}")
            
            # Drop all views
            views_query = text("""
                SELECT table_name 
                FROM information_schema.views 
                WHERE table_schema = :schema;
            """)
            
            views = [row[0] for row in conn.execute(views_query, {'schema': schema}).fetchall()]
            for view in views:
                if view.startswith('pg_'):
                    continue
                try:
                    logger.info(f"Force dropping view: {view}")
                    conn.execute(text(f'DROP VIEW IF EXISTS "{view}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping view {view}: {e}")
            
            # Drop all tables with CASCADE
            tables_query = text("""
                SELECT tablename 
                FROM pg_tables 
                WHERE schemaname = :schema;
            """)
            
            tables = [row[0] for row in conn.execute(tables_query, {'schema': schema}).fetchall()]
            for table in tables:
                try:
                    logger.info(f"Force dropping table: {table}")
                    conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping table {table}: {e}")
            
            # Drop all sequences
            sequences_query = text("""
                SELECT sequence_name 
                FROM information_schema.sequences 
                WHERE sequence_schema = :schema;
            """)
            
            sequences = [row[0] for row in conn.execute(sequences_query, {'schema': schema}).fetchall()]
            for sequence in sequences:
                try:
                    logger.info(f"Force dropping sequence: {sequence}")
                    conn.execute(text(f'DROP SEQUENCE IF EXISTS "{sequence}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping sequence {sequence}: {e}")
            
            # Drop all functions and procedures
            functions_query = text("""
                SELECT routine_name, routine_type
                FROM information_schema.routines 
                WHERE routine_schema = :schema;
            """)
            
            functions = conn.execute(functions_query, {'schema': schema}).fetchall()
            for func_name, func_type in functions:
                try:
                    logger.info(f"Force dropping {func_type.lower()}: {func_name}")
                    conn.execute(text(f'DROP {func_type} IF EXISTS "{func_name}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping {func_type.lower()} {func_name}: {e}")
            
            # Drop all types
            types_query = text("""
                SELECT typname 
                FROM pg_type 
                WHERE typnamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)
                AND typtype = 'c';  -- composite types
            """)
            
            types = [row[0] for row in conn.execute(types_query, {'schema': schema}).fetchall()]
            for type_name in types:
                try:
                    logger.info(f"Force dropping type: {type_name}")
                    conn.execute(text(f'DROP TYPE IF EXISTS "{type_name}" CASCADE;'))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error dropping type {type_name}: {e}")
            
            logger.info("Force drop completed")
            
        except Exception as e:
            logger.error(f"Error during force drop: {e}")
            raise
        finally:
            conn.close()

if __name__ == "__main__":
    logger.info("Starting NUCLEAR database cleanup process")
    logger.warning("WARNING: This will destroy ALL data!")
    
    # Uncomment the method you want to use:
    
    # Method 1: Drop and recreate entire database (most aggressive)
    # nuclear_drop_database()
    
    # Method 2: Drop and recreate public schema only (preserves database structure)
    nuclear_drop_schema_only()
    
    # Method 3: Force drop individual objects (fallback method)
    # force_drop_individual_objects()
    
    logger.info("NUCLEAR database cleanup process completed")