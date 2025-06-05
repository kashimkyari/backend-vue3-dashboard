import os
import asyncio
import logging
import signal
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler
from models import User  # Assuming User model is defined elsewhere

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# API Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'https://monitor-backend.jetcamstudio.com:5000')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')

# Database configuration
db_uri = os.getenv('DATABASE_URL', 'postgresql://postgres.wuldblvptkdjqxwjsych:password@aws-0-eu-central-1.pooler.supabase.com:6543/postgres')
root_cert_path = os.getenv('PG_ROOT_CERT_PATH', '/home/kvsh1m/LiveStream_Monitoring_Vue3_Flask/backend/root.crt')

if not db_uri:
    raise ValueError("DATABASE_URL environment variable not set")

connect_args = {}
if root_cert_path and os.path.exists(root_cert_path):
    connect_args = {
        'sslmode': 'verify-full',
        'sslrootcert': root_cert_path
    }
else:
    connect_args = {
        'sslmode': 'require'
    }
    logging.warning("Root certificate not found or not set. Using sslmode=require.")

engine = create_engine(db_uri, connect_args=connect_args)
Session = sessionmaker(bind=engine)
db_session = Session()

# Conversation states (fixed to 31 states)
(
    ADD_STREAM_URL, ADD_STREAM_PLATFORM, ADD_STREAM_AGENT, ADD_KEYWORD, ADD_OBJECT, AGENT_MANAGEMENT,
    ASSIGN_AGENT, UPDATE_AGENT, DELETE_AGENT, LIST_AGENTS, LIST_STREAMS, CREATE_STREAM, UPDATE_STREAM,
    DELETE_STREAM, REFRESH_STREAM, INTERACTIVE_STREAM, UPDATE_STREAM_STATUS, TRIGGER_DETECTION,
    GET_DETECTION_STATUS, GET_DASHBOARD, GET_AGENT_DASHBOARD, SEND_MESSAGE, GET_MESSAGES, GET_ONLINE_USERS,
    MARK_MESSAGES_READ, GET_AGENT_MESSAGES, UPLOAD_ATTACHMENT, GET_UNREAD_COUNT, MARK_MESSAGE_READ, HEALTH_CHECK,
    NOTIFICATION_FORWARD
) = range(31)

# User session data
user_sessions = {}

# Define keyboard layouts
def get_main_keyboard(role='agent'):
    keyboard = [
        ["📺 Streams", "🔍 Detection Status"],
        ["🔔 Notifications", "💬 Messages"],
        ["🧰 Tools", "ℹ️ Help"]
    ]
    if role == "admin":
        keyboard.insert(0, ["👥 Agents", "📊 Dashboard"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_streams_keyboard(role='agent'):
    keyboard = [
        ["🟢 My Streams", "➕ Add Stream"],
        ["⚙️ Manage Streams", "🔙 Back to Main Menu"]
    ]
    if role == "admin":
        keyboard.insert(1, ["🔄 Refresh Streams", "🎮 Interactive Stream"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_tools_keyboard(role='agent'):
    keyboard = [
        ["📝 Keywords", "🎯 Objects"],
        ["🔙 Back to Main Menu"]
    ]
    if role == "admin":
        keyboard.insert(0, ["➕ Add Keyword", "➕ Add Object"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        ["👤 Create Agent", "✏️ Update Agent"],
        ["🗑️ Delete Agent", "🔙 Back to Main Menu"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    keyboard = [["🔙 Back to Main Menu"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_platform_keyboard():
    keyboard = [
        ["Chaturbate", "Stripchat"],
        ["🔙 Cancel"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# API Helper Function
async def api_request(method, endpoint, data=None, token=None, params=None):
    url = f"{API_BASE_URL}/{endpoint}"
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    
    try:
        if method.lower() == 'get':
            response = requests.get(url, headers=headers, params=params, timeout=10)
        elif method.lower() == 'post':
            response = requests.post(url, headers=headers, json=data, timeout=10)
        elif method.lower() == 'put':
            response = requests.put(url, headers=headers, json=data, timeout=10)
        elif method.lower() == 'delete':
            response = requests.delete(url, headers=headers, timeout=10)
        else:
            return {'error': 'Invalid HTTP method'}
        
        if response.status_code >= 200 and response.status_code < 300:
            try:
                return response.json()
            except:
                return {'message': response.text}
        else:
            return {'error': f"API Error ({response.status_code}): {response.text}"}
    except Exception as e:
        logger.error(f"API request error: {str(e)}")
        return {'error': f"Connection error: {str(e)}"}

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    try:
        user = db_session.query(User).filter_by(telegram_chat_id=str(chat_id)).first()
        if user:
            # Escape special characters for Markdown V2
            username = user.username.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
            role = user.role.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
            user_sessions[chat_id] = {
                'logged_in': True,
                'username': user.username,
                'role': user.role,
                'token': user.id  # Using user.id as a token placeholder
            }
            await update.message.reply_text(
                f"Welcome back, {username}\! You are logged in as {role}\.",
                reply_markup=get_main_keyboard(user.role),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_text(
                "Your Telegram account is not linked to any user\. Please contact an administrator to link your account\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Database error: {e}")
        await update.message.reply_text(
            "An error occurred while checking your account\. Please try again later\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"Your chat ID is: `{chat_id}`\n\nUse this ID to set up notifications\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    
    if not session.get('logged_in', False):
        await update.message.reply_text(
            "Your Telegram account is not linked\. Please contact an administrator to link your account\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    role = session.get('role', 'agent')
    help_text = (
        "🔹 *LiveStream Monitoring Bot Help* 🔹\n\n"
        "*Main Commands:*\n"
        "• /start - Start the bot\n"
        "• /help - Show this help message\n"
        "• /getid - Get your chat ID\n"
        "• /logout - Log out\n"
        "• /health - Check API health\n"
        "• /link_telegram - Link your Telegram account\n\n"
        "*Features:*\n"
        "• 📺 *Streams* - Manage and monitor streams\n"
        "• 🔍 *Detection Status* - Check detection status\n"
        "• 🔔 *Notifications* - View alerts\n"
        "• 💬 *Messages* - Send and receive messages\n"
        "• 🧰 *Tools* - Access keywords and objects\n"
    )
    if role == "admin":
        help_text += (
            "• 👥 *Agents* - Manage agents\n"
            "• 📊 *Dashboard* - View analytics\n"
        )
    # Escape special characters in help_text
    help_text = help_text.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`').replace('[', r'\[')
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=get_main_keyboard(role)
    )

async def link_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_sessions and user_sessions[chat_id].get('logged_in'):
        try:
            user = db_session.query(User).filter_by(username=user_sessions[chat_id]['username']).first()
            if user:
                user.telegram_chat_id = str(chat_id)
                db_session.commit()
                await update.message.reply_text(
                    "Your Telegram account has been linked successfully!"
                )
            else:
                await update.message.reply_text(
                    "User not found. Please log in again."
                )
        except Exception as e:
            logger.error(f"Database error: {e}")
            await update.message.reply_text(
                "An error occurred while linking your account."
            )
    else:
        await update.message.reply_text(
            "Please log in first to link your Telegram account."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    
    if not session.get('logged_in', False):
        await update.message.reply_text(
            "Your Telegram account is not linked. Please contact an administrator to link your account."
        )
        return
    
    role = session.get('role', 'agent')
    help_text = (
        "🔹 *LiveStream Monitoring Bot Help* 🔹\n\n"
        "*Main Commands:*\n"
        "• /start - Start the bot\n"
        "• /help - Show this help message\n"
        "• /getid - Get your chat ID\n"
        "• /logout - Log out\n"
        "• /health - Check API health\n"
        "• /link_telegram - Link your Telegram account\n\n"
        "*Features:*\n"
        "• 📺 *Streams* - Manage and monitor streams\n"
        "• 🔍 *Detection Status* - Check detection status\n"
        "• 🔔 *Notifications* - View alerts\n"
        "• 💬 *Messages* - Send and receive messages\n"
        "• 🧰 *Tools* - Access keywords and objects\n"
    )
    if role == "admin":
        help_text += (
            "• 👥 *Agents* - Manage agents\n"
            "• 📊 *Dashboard* - View analytics\n"
        )
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(role)
    )

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"Your chat ID is: `{chat_id}`\n\nUse this ID to set up notifications.",
        parse_mode=ParseMode.MARKDOWN
    )

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_sessions:
        session = user_sessions[chat_id]
        if session.get('token'):
            await api_request('post', 'logout', token=session.get('token'))
        del user_sessions[chat_id]
        await update.message.reply_text(
            "You have been logged out."
        )
    else:
        await update.message.reply_text(
            "You were not logged in."
        )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await api_request('get', 'health')
    if 'error' in response:
        await update.message.reply_text(
            f"API Health Check Failed: {response['error']}"
        )
    else:
        await update.message.reply_text(
            "API Health: OK"
        )

# Message Handler for Menu Navigation
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    text = update.message.text
    session = user_sessions.get(chat_id, {})
    
    if not session.get('logged_in', False):
        await update.message.reply_text(
            "Your Telegram account is not linked. Please contact an administrator to link your account."
        )
        return
    
    role = session.get('role', 'agent')
    
    if text == "📺 Streams":
        await update.message.reply_text(
            "Select a streams option:",
            reply_markup=get_streams_keyboard(role)
        )
    elif text == "🔍 Detection Status":
        await show_detection_status(update, chat_id, session.get('token'))
    elif text == "🔔 Notifications":
        await show_notifications(update, chat_id, session.get('token'), role)
    elif text == "💬 Messages":
        await show_messages(update, chat_id, session.get('token'), role)
    elif text == "🧰 Tools":
        await update.message.reply_text(
            "Select a tools option:",
            reply_markup=get_tools_keyboard(role)
        )
    elif text == "ℹ️ Help":
        await help_command(update, context)
    elif text == "👥 Agents" and role == "admin":
        await update.message.reply_text(
            "Select an agent management option:",
            reply_markup=get_admin_keyboard()
        )
    elif text == "📊 Dashboard" and role == "admin":
        await show_dashboard(update, chat_id, session.get('token'))
    elif text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard(role)
        )
    # Streams submenu
    elif text == "🟢 My Streams":
        await show_my_streams(update, chat_id, session.get('token'), role)
    elif text == "⚙️ Manage Streams":
        await manage_streams(update, chat_id, session.get('token'), role)
    elif text == "🔄 Refresh Streams" and role == "admin":
        await refresh_streams(update, chat_id, session.get('token'))
    elif text == "🎮 Interactive Stream" and role == "admin":
        await update.message.reply_text(
            "Enter the room URL for interactive stream creation:",
            reply_markup=get_back_keyboard()
        )
        return INTERACTIVE_STREAM
    elif text == "➕ Add Stream":
        await update.message.reply_text(
            "Enter the stream URL:",
            reply_markup=get_back_keyboard()
        )
        return ADD_STREAM_URL
    elif text == "📝 Keywords":
        await show_keywords(update, chat_id, session.get('token'), role)
    elif text == "🎯 Objects":
        await show_objects(update, chat_id, session.get('token'), role)
    elif text == "➕ Add Keyword" and role == "admin":
        await update.message.reply_text(
            "Enter the keyword to add:",
            reply_markup=get_back_keyboard()
        )
        return ADD_KEYWORD
    elif text == "➕ Add Object" and role == "admin":
        await update.message.reply_text(
            "Enter the object name to add:",
            reply_markup=get_back_keyboard()
        )
        return ADD_OBJECT

# Conversation Handlers
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    session = user_sessions.get(chat_id, {})
    
    if not session.get('logged_in', False):
        await query.edit_message_text(
            "Your Telegram account is not linked. Please contact an administrator."
        )
        return ConversationHandler.END
    
    if query.data.startswith("stream_"):
        stream_id = query.data.split("_")[1]
        token = session.get('token')
        await show_stream_details(query, chat_id, stream_id, token, session.get('role', 'agent'))
    elif query.data.startswith("start_") or query.data.startswith("stop_"):
        action = "stop" if query.data.startswith("stop_") else "start"
        stream_id = query.data.split("_")[1]
        token = session.get('token')
        await trigger_detection(query, chat_id, stream_id, action, token)
    elif query.data == "streams_list":
        await show_streams_list(query, chat_id)
    elif query.data.startswith("notification_"):
        notification_id = query.data.split("_")[1]
        token = session.get('token')
        await show_notification_details(query, chat_id, notification_id, token, session.get('role', 'agent'))
    elif query.data == "read_all_notifications":
        token = session.get('token')
        await mark_all_notifications_read(query, chat_id, token, session.get('role', 'agent'))
    elif query.data.startswith("forward_") and session.get('role') == "admin":
        notification_id = query.data.split("_")[1]
        await query.edit_message_text(
            "Enter the agent ID to forward this notification to:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_forward")]])
        )
        context.user_data['notification_id'] = notification_id
        return NOTIFICATION_FORWARD
    elif query.data == "cancel_forward":
        await query.edit_message_text(
            "Forwarding cancelled.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Notifications", callback_data="notifications_list")]])
        )
        context.user_data.pop('notification_id', None)
        return ConversationHandler.END
    return ConversationHandler.END

async def add_stream_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if not session.get('logged_in', False):
        await update.message.reply_text(
            "Your Telegram account is not linked. Please contact an administrator."
        )
        return ConversationHandler.END
    
    text = update.message.text
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    user_sessions[chat_id]['stream_url'] = text
    await update.message.reply_text(
        "Select the platform:",
        reply_markup=get_platform_keyboard()
    )
    return ADD_STREAM_PLATFORM

async def add_stream_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    text = update.message.text
    
    if text == "🔙 Cancel":
        await update.message.reply_text(
            "Stream creation cancelled.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    if text not in ["Chaturbate", "Stripchat"]:
        await update.message.reply_text(
            "Please select a valid platform.",
            reply_markup=get_platform_keyboard()
        )
        return ADD_STREAM_PLATFORM
    
    user_sessions[chat_id]['platform'] = text.lower()
    if session.get('role') != "admin":
        user_sessions[chat_id]['agent_id'] = None
        return await create_stream(update, context)
    
    response = await api_request('get', 'api/agents', token=session.get('token'))
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch agents: {response['error']}",
            reply_markup=get_main_keyboard(role)
        )
        return ConversationHandler.END
    
    agents = response
    if not agents:
        await update.message.reply_text(
            "No agents available. Creating stream without agent.",
            reply_markup=get_back_keyboard()
        )
        user_sessions[chat_id]['agent_id'] = None
        return await create_stream(update, context)
    
    agent_list = "\n".join([f"{agent['id']}: {agent['username']}" for agent in agents])
    await update.message.reply_text(
        f"Enter the agent ID to assign (or 'none'):\n{agent_list}",
        reply_markup=get_back_keyboard()
    )
    return ADD_STREAM_AGENT

async def add_stream_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    text = update.message.text
    
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard('admin')
        )
        return ConversationHandler.END
    
    if text.lower() == 'none':
        user_sessions[chat_id]['agent_id'] = None
        return await create_stream(update, context)
    
    try:
        agent_id = int(text)
        response = await api_request('get', f'api/agents/{agent_id}', token=session.get('token'))
        if 'error' in response:
            await update.message.reply_text(
                "Invalid agent ID. Enter a valid ID or 'none'.",
                reply_markup=get_back_keyboard()
            )
            return ADD_STREAM_AGENT
        user_sessions[chat_id]['agent_id'] = agent_id
        return await create_stream(update, context)
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid agent ID or 'none'.",
            reply_markup=get_back_keyboard()
        )
        return ADD_STREAM_AGENT

async def create_stream(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions[chat_id]
    data = {
        'room_url': session['stream_url'],
        'platform': session['platform'],
        'agent_id': session.get('agent_id')
    }
    token = session.get('token')
    response = await api_request('post', 'api/streams', data, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to create stream: {response['error']}",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
    else:
        stream = response.get('stream', {})
        await update.message.reply_text(
            f"✅ Stream created!\nID: {stream.get('id')}\nURL: {stream.get('room_url')}\nPlatform: {stream.get('type')}",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
    
    user_sessions[chat_id].pop('stream_url', None)
    user_sessions[chat_id].pop('platform', None)
    user_sessions[chat_id].pop('agent_id', None)
    return ConversationHandler.END

async def add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    text = update.message.text
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard('admin')
        )
        return ConversationHandler.END
    
    data = {'keyword': text}
    token = session.get('token')
    response = await api_request('post', 'api/keywords', data, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to add keyword: {response['error']}",
            reply_markup=get_tools_keyboard('admin')
        )
    else:
        await update.message.reply_text(
            f"✅ Keyword '{text}' added.",
            reply_markup=get_tools_keyboard('admin')
        )
    return ConversationHandler.END

async def add_object(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    text = update.message.text
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard('admin')
        )
        return ConversationHandler.END
    
    data = {'object_name': text}
    token = session.get('token')
    response = await api_request('post', 'api/objects', data, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to add object: {response['error']}",
            reply_markup=get_tools_keyboard('admin')
        )
    else:
        await update.message.reply_text(
            f"✅ Object '{text}' added.",
            reply_markup=get_tools_keyboard('admin')
        )
    return ConversationHandler.END

async def agent_management_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    text = update.message.text
    action = session.get('action')
    
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard('admin')
        )
        user_sessions[chat_id].pop('action', None)
        return ConversationHandler.END
    
    token = session.get('token')
    if action == "create_agent":
        try:
            username, password, email = text.split(':')
            data = {
                'username': username.strip(),
                'password': password.strip(),
                'email': email.strip(),
                'receive_updates': True
            }
            response = await api_request('post', 'api/agents', data, token=token)
            if 'error' in response:
                await update.message.reply_text(
                    f"Failed to create agent: {response['error']}",
                    reply_markup=get_admin_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"✅ Agent created: {username}",
                    reply_markup=get_admin_keyboard()
                )
            user_sessions[chat_id].pop('action', None)
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text(
                "Invalid format. Use: username:password:email",
                reply_markup=get_back_keyboard()
            )
            return AGENT_MANAGEMENT
    elif action == "update_agent":
        try:
            agent_id, username, password, email, receive_updates = text.split(':')
            data = {
                'username': username.strip(),
                'password': password.strip(),
                'email': email.strip(),
                'receive_updates': receive_updates.lower() == 'true'
            }
            response = await api_request('put', f'api/agents/{agent_id}', data, token=token)
            if 'error' in response:
                await update.message.reply_text(
                    f"Failed to update agent: {response['error']}",
                    reply_markup=get_admin_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"✅ Agent #{agent_id} updated",
                    reply_markup=get_admin_keyboard()
                )
            user_sessions[chat_id].pop('action', None)
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text(
                "Invalid format. Use: id:username:password:email:receive_updates",
                reply_markup=get_back_keyboard()
            )
            return AGENT_MANAGEMENT
    elif action == "delete_agent":
        try:
            agent_id = int(text)
            response = await api_request('delete', f'api/agents/{agent_id}', token=token)
            if 'error' in response:
                await update.message.reply_text(
                    f"Failed to delete agent: {response['error']}",
                    reply_markup=get_admin_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"✅ Agent #{agent_id} deleted",
                    reply_markup=get_admin_keyboard()
                )
            user_sessions[chat_id].pop('action', None)
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text(
                "Please enter a valid agent ID.",
                reply_markup=get_back_keyboard()
            )
            return AGENT_MANAGEMENT
    return ConversationHandler.END

async def interactive_stream(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    text = update.message.text
    if text == "🔙 Back to Main Menu":
        await update.message.reply_text(
            "Returned to main menu.",
            reply_markup=get_main_keyboard('admin')
        )
        return ConversationHandler.END
    
    data = {'room_url': text, 'platform': 'chaturbate'}  # Default to Chaturbate, adjust as needed
    token = session.get('token')
    response = await api_request('post', 'api/streams/interactive', data, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to start interactive stream creation: {response['error']}",
            reply_markup=get_main_keyboard('admin')
        )
    else:
        await update.message.reply_text(
            f"✅ Interactive stream creation started!\nJob ID: {response.get('job_id')}",
            reply_markup=get_main_keyboard('admin')
        )
    return ConversationHandler.END

async def forward_notification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    
    text = update.message.text
    notification_id = context.user_data.get('notification_id')
    token = session.get('token')
    
    try:
        agent_id = int(text)
        data = {'agent_id': agent_id}
        response = await api_request('post', f'api/notifications/{notification_id}/forward', data, token=token)
        if 'error' in response:
            await update.message.reply_text(
                f"Failed to forward notification: {response['error']}",
                reply_markup=get_main_keyboard('admin')
            )
        else:
            await update.message.reply_text(
                f"✅ Notification #{notification_id} forwarded to agent #{agent_id}",
                reply_markup=get_main_keyboard('admin')
            )
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid agent ID.",
            reply_markup=get_main_keyboard('admin')
        )
    
    context.user_data.pop('notification_id', None)
    return ConversationHandler.END

# Utility Functions
async def show_my_streams(update: Update, chat_id: int, token: str, role: str) -> None:
    endpoint = 'api/streams' if role == "admin" else 'api/agent/dashboard'
    response = await api_request('get', endpoint, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch streams: {response['error']}",
            reply_markup=get_streams_keyboard(role)
        )
        return
    
    streams = response if role == "admin" else response.get('assignments', [])
    if not streams:
        await update.message.reply_text(
            "No streams available.",
            reply_markup=get_streams_keyboard(role)
        )
        return
    
    streams_text = f"*{'All' if role == 'admin' else 'Your Assigned'} Streams* ({len(streams)})\n\n"
    stream_buttons = []
    for stream in streams:
        status = "🟢" if stream.get('status') == 'online' else "⚫"
        stream_id = stream.get('id')
        streams_text += f"{status} Stream #{stream_id}: {stream.get('streamer_username', 'Unnamed')}\n"
        stream_buttons.append([InlineKeyboardButton(f"{status} Stream #{stream_id}", callback_data=f"stream_{stream_id}")])
    
    await update.message.reply_text(
        streams_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(stream_buttons)
    )

async def show_stream_details(query, chat_id: int, stream_id: str, token: str, role: str) -> None:
    response = await api_request('get', f'api/detection-status/{stream_id}', token=token)
    
    if 'error' in response:
        await query.edit_message_text(
            f"Error getting stream details: {response['error']}"
        )
        return
    
    stream_status = "🟢 Active" if response.get('active', False) else "⚫ Inactive"
    stream_url = response.get('stream_url', 'N/A')
    
    control_buttons = []
    if response.get('active', False):
        control_buttons.append(InlineKeyboardButton("⏹️ Stop Monitoring", callback_data=f"stop_{stream_id}"))
    else:
        control_buttons.append(InlineKeyboardButton("▶️ Start Monitoring", callback_data=f"start_{stream_id}"))
    
    status_message = (
        f"*Stream #{stream_id} Details*\n\n"
        f"• Status: {stream_status}\n"
        f"• URL: `{stream_url}`\n"
        f"• Type: {response.get('stream_type', 'Unknown')}\n"
        f"• Last updated: {datetime.now().strftime('%H:%M:%S')}"
    )
    
    await query.edit_message_text(
        status_message,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            control_buttons,
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"stream_{stream_id}")],
            [InlineKeyboardButton("🔙 Back to Streams", callback_data="streams_list")]
        ])
    )

async def trigger_detection(query, chat_id: int, stream_id: str, action: str, token: str) -> None:
    data = {'stream_id': int(stream_id), 'stop': action == "stop"}
    response = await api_request('post', 'api/trigger-detection', data, token=token)
    
    if 'error' in response:
        await query.edit_message_text(
            f"Error controlling monitoring: {response['error']}"
        )
        return
    
    status = "stopped" if action == "stop" else "started"
    await query.edit_message_text(
        f"✅ Monitoring {status} for Stream #{stream_id}.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Stream", callback_data=f"stream_{stream_id}")]])
    )

async def show_detection_status(update: Update, chat_id: int, token: str) -> None:
    response = await api_request('get', 'api/agent/dashboard', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch detection status: {response['error']}",
            reply_markup=get_main_keyboard(user_sessions.get(chat_id, {}).get('role', 'agent'))
        )
        return
    
    streams = response.get('assignments', [])
    if not streams:
        await update.message.reply_text(
            "No assigned streams to monitor.",
            reply_markup=get_main_keyboard(user_sessions.get(chat_id, {}).get('role', 'agent'))
        )
        return
    
    status_text = "*Detection Status*\n\n"
    for stream in streams:
        stream_id = stream.get('id')
        status_response = await api_request('get', f'api/detection-status/{stream_id}', token=token)
        if 'error' in status_response:
            status_text += f"Stream #{stream_id}: Error - {status_response['error']}\n"
        else:
            status = "🟢 Active" if status_response.get('active', False) else "⚫ Inactive"
            status_text += f"Stream #{stream_id}: {status} ({stream.get('streamer_username', 'Unnamed')})\n"
    
    await update.message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(user_sessions.get(chat_id, {}).get('role', 'agent'))
    )

async def show_notifications(update: Update, chat_id: int, token: str, role: str) -> None:
    endpoint = 'api/notifications' if role == "admin" else 'api/agent/notifications'
    response = await api_request('get', endpoint, token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch notifications: {response['error']}",
            reply_markup=get_main_keyboard(role)
        )
        return
    
    notifications = response if role == "admin" else response
    if not notifications:
        await update.message.reply_text(
            "No notifications.",
            reply_markup=get_main_keyboard(role)
        )
        return
    
    unread_count = sum(1 for n in notifications if not n.get('read', False))
    notifications_text = f"*Your Notifications* ({unread_count} unread)\n\n"
    notification_buttons = []
    for notification in notifications[:5]:
        notification_id = notification.get('id')
        read_status = "✓" if notification.get('read', False) else "🔴"
        timestamp = notification.get('timestamp', '')
        notifications_text += f"{read_status} #{notification_id}: {notification.get('event_type', 'No message')} ({timestamp[-8:-3]})\n"
        if not notification.get('read', False):
            notification_buttons.append([InlineKeyboardButton(f"Mark #{notification_id} as Read", callback_data=f"notification_{notification_id}")])
        if role == "admin":
            notification_buttons.append([InlineKeyboardButton(f"Forward #{notification_id}", callback_data=f"forward_{notification_id}")])
    
    if unread_count > 0:
        notification_buttons.append([InlineKeyboardButton("📖 Mark All as Read", callback_data="read_all_notifications")])
    
    await update.message.reply_text(
        notifications_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(notification_buttons)
    )

async def show_notification_details(query, chat_id: int, notification_id: str, token: str, role: str) -> None:
    endpoint = 'api/notifications' if role == "admin" else 'api/agent/notifications'
    response = await api_request('put', f'{endpoint}/{notification_id}/read', token=token)
    
    if 'error' in response:
        await query.edit_message_text(
            f"Error marking notification as read: {response['error']}"
        )
        return
    
    await query.edit_message_text(
        f"✅ Notification #{notification_id} marked as read.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Notifications", callback_data="notifications_list")]])
    )

async def mark_all_notifications_read(query, chat_id: int, token: str, role: str) -> None:
    endpoint = 'api/notifications/read-all' if role == "admin" else 'api/agent/notifications/read-all'
    response = await api_request('put', endpoint, token=token)
    
    if 'error' in response:
        await query.edit_message_text(
            f"Error marking notifications as read: {response['error']}"
        )
    else:
        await query.edit_message_text(
            f"✅ All notifications marked as read.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Notifications", callback_data="notifications_list")]])
        )

async def show_keywords(update: Update, chat_id: int, token: str, role: str) -> None:
    response = await api_request('get', 'api/keywords', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch keywords: {response['error']}",
            reply_markup=get_tools_keyboard(role)
        )
        return
    
    keywords = response
    if not keywords:
        await update.message.reply_text(
            "No keywords found.",
            reply_markup=get_tools_keyboard(role)
        )
        return
    
    keywords_text = f"*Monitored Keywords* ({len(keywords)})\n\n"
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i+5]
        keywords_text += "• " + ", ".join(item.get('keyword', 'Unknown') for item in batch) + "\n"
    
    await update.message.reply_text(
        keywords_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_tools_keyboard(role)
    )

async def show_objects(update: Update, chat_id: int, token: str, role: str) -> None:
    response = await api_request('get', 'api/objects', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch objects: {response['error']}",
            reply_markup=get_tools_keyboard(role)
        )
        return
    
    objects = response
    if not objects:
        await update.message.reply_text(
            "No objects found.",
            reply_markup=get_tools_keyboard(role)
        )
        return
    
    objects_text = f"*Monitored Objects* ({len(objects)})\n\n"
    for i in range(0, len(objects), 5):
        batch = objects[i:i+5]
        objects_text += "• " + ", ".join(item.get('object_name', 'Unknown') for item in batch) + "\n"
    
    await update.message.reply_text(
        objects_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_tools_keyboard(role)
    )

async def show_streams_list(query, chat_id):
    session = user_sessions.get(chat_id, {})
    token = session.get('token')
    
    if not token:
        await query.edit_message_text(
            "Your Telegram account is not linked. Please contact an administrator."
        )
        return
    
    response = await api_request('get', 'api/agent/dashboard', token=token)
    
    if 'error' in response:
        await query.edit_message_text(
            f"Error fetching streams: {response['error']}"
        )
        return
    
    ongoing = response.get('ongoing_streams', 0)
    assignments = response.get('assignments', [])
    
    if not assignments:
        await query.edit_message_text(
            "No streams assigned to you yet.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="streams_list")]])
        )
        return
    
    streams_text = f"*Your Assigned Streams* ({ongoing} ongoing)\n\n"
    stream_buttons = []
    for assignment in assignments:
        stream = assignment
        stream_id = stream.get('id')
        status = "🟢" if stream.get('status', 'offline') == 'online' else "⚫"
        streams_text += f"{status} Stream #{stream_id}: {stream.get('streamer_username', 'Unnamed')}\n"
        stream_buttons.append([InlineKeyboardButton(f"{status} Stream #{stream_id}", callback_data=f"stream_{stream_id}")])
    
    stream_buttons.append([InlineKeyboardButton("🔄 Refresh List", callback_data="streams_list")])
    
    await query.edit_message_text(
        streams_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(stream_buttons)
    )

async def manage_streams(update: Update, chat_id: int, token: str, role: str) -> None:
    response = await api_request('get', 'api/streams', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch streams: {response['error']}",
            reply_markup=get_streams_keyboard(role)
        )
        return
    
    streams = response
    if not streams:
        await update.message.reply_text(
            "No streams available.",
            reply_markup=get_streams_keyboard(role)
        )
        return
    
    streams_text = "*Manage Streams*\n\n"
    stream_buttons = []
    for stream in streams:
        stream_id = stream.get('id')
        status = "🟢" if stream.get('status') == 'online' else "⚫"
        streams_text += f"{status} Stream #{stream_id}: {stream.get('streamer_username', 'Unnamed')}\n"
        stream_buttons.append([InlineKeyboardButton(f"{status} Stream #{stream_id}", callback_data=f"stream_{stream_id}")])
    
    await update.message.reply_text(
        streams_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(stream_buttons)
    )

async def refresh_streams(update: Update, chat_id: int, token: str) -> None:
    await update.message.reply_text(
        "Refreshing Chaturbate streams..."
    )
    chaturbate_response = await api_request('post', 'api/streams/refresh/chaturbate', {'room_slug': 'example'}, token=token)
    await update.message.reply_text(
        "Refreshing Stripchat streams..."
    )
    stripchat_response = await api_request('post', 'api/streams/refresh/stripchat', {'room_url': 'https://stripchat.com/example'}, token=token)
    
    if 'error' in chaturbate_response or 'error' in stripchat_response:
        await update.message.reply_text(
            f"Failed to refresh streams: {chaturbate_response.get('error', '')} {stripchat_response.get('error', '')}",
            reply_markup=get_streams_keyboard('admin')
        )
    else:
        await update.message.reply_text(
            "✅ Streams refreshed successfully.",
            reply_markup=get_streams_keyboard('admin')
        )

async def show_dashboard(update: Update, chat_id: int, token: str) -> None:
    response = await api_request('get', 'api/dashboard', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch dashboard: {response['error']}",
            reply_markup=get_main_keyboard('admin')
        )
        return
    
    ongoing = response.get('ongoing_streams', 0)
    streams = response.get('streams', [])
    dashboard_text = f"*Dashboard*\n\nOngoing Streams: {ongoing}\nTotal Streams: {len(streams)}"
    await update.message.reply_text(
        dashboard_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard('admin')
    )

async def show_messages(update: Update, chat_id: int, token: str, role: str) -> None:
    response = await api_request('get', f'api/messages/{chat_id}', token=token)
    
    if 'error' in response:
        await update.message.reply_text(
            f"Failed to fetch messages: {response['error']}",
            reply_markup=get_main_keyboard(role)
        )
        return
    
    messages = response
    if not messages:
        await update.message.reply_text(
            "No messages.",
            reply_markup=get_main_keyboard(role)
        )
        return
    
    messages_text = "*Your Messages*\n\n"
    for msg in messages[:5]:
        messages_text += f"#{msg.get('id')}: {msg.get('message')} ({msg.get('timestamp')[-8:-3]})\n"
    
    await update.message.reply_text(
        messages_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(role)
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    await update.message.reply_text(
        "Operation cancelled.",
        reply_markup=get_main_keyboard(session.get('role', 'agent'))
    )
    user_sessions[chat_id].pop('action', None)
    return ConversationHandler.END

async def start_agent_management(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> int:
    chat_id = update.message.chat_id
    session = user_sessions.get(chat_id, {})
    if session.get('role') != "admin":
        await update.message.reply_text(
            "Unauthorized. Admins only.",
            reply_markup=get_main_keyboard(session.get('role', 'agent'))
        )
        return ConversationHandler.END
    user_sessions[chat_id]['action'] = action
    if action == "create_agent":
        await update.message.reply_text(
            "Enter new agent's username:password:email:",
            reply_markup=get_back_keyboard()
        )
    elif action == "update_agent":
        response = await api_request('get', 'api/agents', token=session.get('token'))
        if 'error' in response:
            await update.message.reply_text(
                f"Failed to fetch agents: {response['error']}"
            )
            return ConversationHandler.END
        agents = response
        agent_list = "\n".join([f"ID {agent['id']}: {agent['username']}" for agent in agents])
        await update.message.reply_text(
            f"Enter agent ID and details (id:username:password:email:receive_updates):\n{agent_list}",
            reply_markup=get_back_keyboard()
        )
    elif action == "delete_agent":
        response = await api_request('get', 'api/agents', token=session.get('token'))
        if 'error' in response:
            await update.message.reply_text(
                f"Failed to fetch agents: {response['error']}"
            )
            return ConversationHandler.END
        agents = response
        agent_list = "\n".join([f"ID {agent['id']}: {agent['username']}" for agent in agents])
        await update.message.reply_text(
            f"Enter agent ID to delete:\n{agent_list}",
            reply_markup=get_back_keyboard()
        )
    return AGENT_MANAGEMENT

async def main():
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN not set")
        return
    
    application = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Add Stream$"), add_stream_url),
            MessageHandler(filters.Regex("^➕ Add Keyword$"), add_keyword),
            MessageHandler(filters.Regex("^➕ Add Object$"), add_object),
            MessageHandler(filters.Regex("^👤 Create Agent$"), lambda u, c: start_agent_management(u, c, "create_agent")),
            MessageHandler(filters.Regex("^✏️ Update Agent$"), lambda u, c: start_agent_management(u, c, "update_agent")),
            MessageHandler(filters.Regex("^🗑️ Delete Agent$"), lambda u, c: start_agent_management(u, c, "delete_agent")),
            MessageHandler(filters.Regex("^🎮 Interactive Stream$"), interactive_stream),
            CallbackQueryHandler(button_callback, pattern="^forward_")
        ],
        states={
            ADD_STREAM_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_stream_url)],
            ADD_STREAM_PLATFORM: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_stream_platform)],
            ADD_STREAM_AGENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_stream_agent)],
            ADD_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_keyword)],
            ADD_OBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_object)],
            AGENT_MANAGEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, agent_management_handler)],
            INTERACTIVE_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, interactive_stream)],
            NOTIFICATION_FORWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_notification)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("getid", getid))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(CommandHandler("link_telegram", link_telegram))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Telegram bot starting")
    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, lambda: asyncio.create_task(shutdown(application)))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Telegram bot started successfully")
    
    stop_signal = asyncio.Future()
    await stop_signal

async def shutdown(application):
    logger.info("Shutting down...")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()

def run_bot():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Telegram bot stopped by user")
    except asyncio.CancelledError:
        logger.info("Telegram bot tasks cancelled")
    except Exception as e:
        logger.error(f"Telegram bot error in event loop: {str(e)}")

if __name__ == "__main__":
    run_bot()