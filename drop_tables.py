#!/usr/bin/env python3
"""
postgres_drop_tables.py - Improved script to drop all tables from a PostgreSQL database

This script uses a more efficient approach to safely drop all tables by:
1. Dropping foreign key constraints one by one to avoid timeouts
2. Using proper transaction management
3. Adding timeout handling and retry logic
4. Better error handling and recovery
"""

import logging
import os
import time
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DatabaseError
from flask import Flask
from extensions import db
from config import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('pg_drop.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def drop_all_postgres_tables():
    """Drop all tables from a PostgreSQL database using PostgreSQL specific methods."""
    load_dotenv()
    app = create_app()
    
    with app.app_context():
        # Use autocommit mode to avoid transaction issues
        conn = db.engine.connect()
        
        try:
            # Set statement timeout to 30 seconds
            conn.execute(text("SET statement_timeout = '30s';"))
            
            # Get the schema we're using (usually 'public')
            schema_query = text("SELECT current_schema();")
            schema = conn.execute(schema_query).scalar() or 'public'
            logger.info(f"Working with schema: {schema}")
            
            # Get all tables in the schema
            tables_query = text("""
                SELECT tablename 
                FROM pg_tables 
                WHERE schemaname = :schema
                ORDER BY tablename;
            """)
            
            tables = [row[0] for row in conn.execute(tables_query, {'schema': schema}).fetchall()]
            logger.info(f"Found {len(tables)} tables: {', '.join(tables)}")
            
            if not tables:
                logger.info("No tables found in database. Nothing to drop.")
                return
            
            # Drop foreign key constraints one by one
            logger.info("Dropping foreign key constraints one by one...")
            fk_query = text("""
                SELECT 
                    tc.constraint_name,
                    tc.table_name
                FROM information_schema.table_constraints tc
                WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = :schema
                ORDER BY tc.table_name, tc.constraint_name;
            """)
            
            foreign_keys = conn.execute(fk_query, {'schema': schema}).fetchall()
            logger.info(f"Found {len(foreign_keys)} foreign key constraints to drop")
            
            for fk_name, table_name in foreign_keys:
                try:
                    logger.info(f"Dropping FK constraint {fk_name} from table {table_name}")
                    drop_fk_sql = text(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{fk_name}";')
                    conn.execute(drop_fk_sql)
                    conn.commit()
                except OperationalError as e:
                    if "does not exist" in str(e):
                        logger.info(f"FK constraint {fk_name} already dropped or doesn't exist")
                    else:
                        logger.error(f"Error dropping FK constraint {fk_name}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error dropping FK constraint {fk_name}: {e}")
            
            logger.info("Finished dropping foreign key constraints")
            
            # Now drop all tables
            logger.info("Dropping tables...")
            for table in tables:
                try:
                    logger.info(f"Dropping table: {table}")
                    drop_query = text(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
                    conn.execute(drop_query)
                    conn.commit()
                    logger.info(f"Successfully dropped table: {table}")
                except Exception as e:
                    logger.error(f"Error dropping table {table}: {e}")
            
            # Drop sequences
            logger.info("Dropping sequences...")
            sequences_query = text("""
                SELECT sequence_name 
                FROM information_schema.sequences 
                WHERE sequence_schema = :schema;
            """)
            
            sequences = [row[0] for row in conn.execute(sequences_query, {'schema': schema}).fetchall()]
            logger.info(f"Found {len(sequences)} sequences to drop")
            
            for sequence in sequences:
                try:
                    logger.info(f"Dropping sequence: {sequence}")
                    drop_query = text(f'DROP SEQUENCE IF EXISTS "{sequence}" CASCADE;')
                    conn.execute(drop_query)
                    conn.commit()
                    logger.info(f"Successfully dropped sequence: {sequence}")
                except Exception as e:
                    logger.error(f"Error dropping sequence {sequence}: {e}")
            
            # Drop views
            logger.info("Dropping views...")
            views_query = text("""
                SELECT table_name 
                FROM information_schema.views 
                WHERE table_schema = :schema;
            """)
            
            views = [row[0] for row in conn.execute(views_query, {'schema': schema}).fetchall()]
            logger.info(f"Found {len(views)} views to drop")
            
            for view in views:
                if view.startswith('pg_') or view.startswith('information_schema'):
                    continue  # Skip system views
                try:
                    logger.info(f"Dropping view: {view}")
                    drop_query = text(f'DROP VIEW IF EXISTS "{view}" CASCADE;')
                    conn.execute(drop_query)
                    conn.commit()
                    logger.info(f"Successfully dropped view: {view}")
                except Exception as e:
                    logger.error(f"Error dropping view {view}: {e}")
            
            # Drop functions and procedures
            logger.info("Dropping functions and procedures...")
            functions_query = text("""
                SELECT routine_name, routine_type
                FROM information_schema.routines 
                WHERE routine_schema = :schema 
                AND routine_type IN ('FUNCTION', 'PROCEDURE');
            """)
            
            functions = conn.execute(functions_query, {'schema': schema}).fetchall()
            logger.info(f"Found {len(functions)} functions/procedures to drop")
            
            for func_name, func_type in functions:
                try:
                    logger.info(f"Dropping {func_type.lower()}: {func_name}")
                    drop_query = text(f'DROP {func_type} IF EXISTS "{func_name}" CASCADE;')
                    conn.execute(drop_query)
                    conn.commit()
                    logger.info(f"Successfully dropped {func_type.lower()}: {func_name}")
                except Exception as e:
                    logger.error(f"Error dropping {func_type.lower()} {func_name}: {e}")
            
            # Final verification
            tables_after = [row[0] for row in conn.execute(tables_query, {'schema': schema}).fetchall()]
            sequences_after = [row[0] for row in conn.execute(sequences_query, {'schema': schema}).fetchall()]
            views_after = [row[0] for row in conn.execute(views_query, {'schema': schema}).fetchall() 
                          if not row[0].startswith('pg_') and not row[0].startswith('information_schema')]
            
            if not tables_after and not sequences_after and not views_after:
                logger.info("SUCCESS! All database objects have been dropped.")
            else:
                if tables_after:
                    logger.warning(f"Some tables could not be dropped: {', '.join(tables_after)}")
                if sequences_after:
                    logger.warning(f"Some sequences could not be dropped: {', '.join(sequences_after)}")
                if views_after:
                    logger.warning(f"Some views could not be dropped: {', '.join(views_after)}")
            
        except Exception as e:
            logger.error(f"Unexpected error during database cleanup: {e}")
            logger.error("Rolling back any pending transaction...")
            try:
                conn.rollback()
            except:
                pass
        finally:
            conn.close()
            logger.info("Database connection closed")

def drop_with_retry(max_retries=3):
    """Drop tables with retry logic in case of temporary issues."""
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1} of {max_retries}")
            drop_all_postgres_tables()
            return  # Success, exit the retry loop
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5  # Progressive backoff
                logger.info(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                logger.error("All retry attempts failed")
                raise

if __name__ == "__main__":
    logger.info("Starting improved PostgreSQL database cleanup process")
    try:
        drop_with_retry()
        logger.info("PostgreSQL database cleanup process completed successfully")
    except Exception as e:
        logger.error(f"Database cleanup process failed: {e}")
        exit(1)