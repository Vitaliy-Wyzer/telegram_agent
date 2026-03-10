import logging
import sqlite3
from argon2 import PasswordHasher
from config import TELEGRAM_BOT_TOKEN
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, CallbackContext, ConversationHandler
from image_processor import extract_text_from_image, process_image_and_answer

CREATE_GROUP_NAME = 1
CREATE_PASSWORD = 2
CONFIRM_PASSWORD = 3


class PersistentUserManager:
    def __init__(self, db_file="data/data.db"):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for subscribers."""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")
            
            # 1. Create subscribers table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    group_id INTEGER DEFAULT NULL
                )
            ''')
            
            # 2. Check if groups table exists and needs migration
            need_migration = False
            table_exists = False
            try:
                cursor.execute("SELECT chat_id FROM groups LIMIT 1")
                table_exists = True
            except sqlite3.OperationalError:
                # Column missing (or table missing)
                # Check if table exists
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='groups'")
                if cursor.fetchone():
                    need_migration = True
                    table_exists = True
            
            if not table_exists:
                cursor.execute('''
                    CREATE TABLE groups (
                        group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_name TEXT UNIQUE,
                        chat_id INTEGER,
                        hash_code TEXT,
                        FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id)
                    )
                ''')
            elif need_migration:
                print("Doing robust schema migration for 'groups' table...")
                try:
                    cursor.execute("PRAGMA foreign_keys=OFF")
                    cursor.execute("BEGIN TRANSACTION")
                    
                    # Rename old table
                    cursor.execute("ALTER TABLE groups RENAME TO groups_old")
                    
                    # Create new table with correct schema
                    cursor.execute('''
                        CREATE TABLE groups (
                            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            group_name TEXT UNIQUE,
                            chat_id INTEGER,
                            hash_code TEXT,
                            FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id)
                        )
                    ''')
                    
                    # Copy data (owner_id will be NULL for old records)
                    cursor.execute('''
                        INSERT INTO groups (group_id, group_name, hash_code)
                        SELECT group_id, group_name, hash_code FROM groups_old
                    ''')
                    
                    # Drop old table
                    cursor.execute("DROP TABLE groups_old")
                    
                    cursor.execute("COMMIT")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    print("Schema migration successful: Rebuilt 'groups' table structure")
                except Exception as e:
                    cursor.execute("ROLLBACK")
                    print(f"Migration failed: {e}")
                    
            conn.commit()
            print(f"Database initialized with {self.get_subscriber_count()} existing subscribers")
    
    def add_subscriber(self, chat_id, username=None, first_name=None, last_name=None):
        """Add a subscriber to the database."""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO subscribers (chat_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (chat_id, username, first_name, last_name))
            conn.commit()
        
    def create_group(self, group_name, password, chat_id):
        ph = PasswordHasher()
        res = ph.hash(password)
        
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")
            try:
                cursor.execute('''
                            INSERT INTO groups (group_name, hash_code, chat_id)
                            VALUES (?, ?, ?)
                            ''', (group_name, res, chat_id))
                conn.commit()
            except sqlite3.IntegrityError as e:
                print(f"Error creating group: {e}")
                raise e
            finally:
                conn.close()
        
        def remove_subscriber(self, chat_id):
            """Remove a subscriber from the database."""
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM subscribers WHERE chat_id = ?', (chat_id,))
            conn.commit()
    
    def get_all_subscribers(self):
        """Get all subscriber chat IDs."""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT chat_id FROM subscribers')
            subscribers = [row[0] for row in cursor.fetchall()]
        return subscribers
    
    def get_subscriber_count(self):
        """Get total number of subscribers."""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM subscribers')
            count = cursor.fetchone()[0]
        return count
    
    def is_subscribed(self, chat_id):
        """Check if a user is subscribed."""
        with sqlite3.connect(self.db_file) as conn:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM subscribers WHERE chat_id = ?', (chat_id,))
            result = cursor.fetchone() is not None
        return result
    
    
    

# Initialize persistent user manager
user_manager = PersistentUserManager()

async def broadcast_to_all_subscribers(context: ContextTypes.DEFAULT_TYPE, message: str, parse_mode='Markdown'):
    """Broadcast message to ALL subscribers in database."""
    subscribers = user_manager.get_all_subscribers()
    
    if not subscribers:
        logging.info("No subscribers to broadcast to")
        return 0
    
    successful_sends = 0
    total_subscribers = len(subscribers)
    
    logging.info(f"Broadcasting to {total_subscribers} subscribers...")
    
    for chat_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode
            )
            successful_sends += 1
        except Exception as e:
            logging.error(f"Failed to send to {chat_id}: {e}")
    
    logging.info(f"Broadcast completed: {successful_sends}/{total_subscribers} successful")
    return successful_sends

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages - answer exam questions."""
    user_text = update.message.text.strip()
    
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    user_manager.add_subscriber(
        chat_id=chat_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    if len(user_text) < 5:
        await update.message.reply_text("Please provide a question or text to process.")
        return
    
    processing_msg = await update.message.reply_text("🔄 Processing your question...")
    
    try:
        # Process text with AI
        await update.effective_chat.send_action("typing")
        answers = process_image_and_answer(user_text)
        
        if not answers or len(answers.strip()) < 5:
            await processing_msg.edit_text("No meaningful answers could be generated.")
            return
        
        # Send answer to user
        await update.message.reply_text(answers, parse_mode='Markdown')
        await processing_msg.delete()
        
        total_subscribers = user_manager.get_subscriber_count()
        if total_subscribers > 0:
            broadcast_text = f"**New answers processed by {user.first_name}:**\n\n{answers}"
            successful_sends = await broadcast_to_all_subscribers(context, broadcast_text)
            
            await update.message.reply_text(
                f"Answers broadcasted to {successful_sends} subscribers!"
            )
        
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle images - extract text, answer, and broadcast to ALL subscribers."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Auto-subscribe user when they send an image
    user_manager.add_subscriber(
        chat_id=chat_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    processing_msg = await update.message.reply_text("🔄 Handling image...")
    
    try:
        # Get image and extract text
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        await update.effective_chat.send_action("typing")
        raw_text = extract_text_from_image(bytes(image_bytes))
        
        if not raw_text or len(raw_text.strip()) < 10:
            await processing_msg.edit_text("Text not found.")
            return
        
        # Single AI call to process and answer
        await update.effective_chat.send_action("typing")
        answers = process_image_and_answer(raw_text)
        
        # Send to original user
        await update.message.reply_text(answers, parse_mode='Markdown')
        await processing_msg.delete()
        
        # Broadcast to ALL subscribers (including the sender)
        total_subscribers = user_manager.get_subscriber_count()
        if total_subscribers > 0:
            broadcast_text = f"📢 **New answers processed by {user.first_name}:**\n\n{answers}"
            successful_sends = await broadcast_to_all_subscribers(context, broadcast_text)
            with open("file.txt", "w") as f:
                f.write("1")
            
            # Notify sender about broadcast
            await update.message.reply_text(
                f"✅ Answers broadcasted to {successful_sends} subscribers!"
            )
        
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - subscribe user."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    user_manager.add_subscriber(
        chat_id=chat_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    total_subscribers = user_manager.get_subscriber_count()
    
    welcome_text = (
        "**Welcome to the Answer Bot!**\n\n"
        "You are now subscribed to receive answers from all users!\n\n"
        "**How it works:**\n"
        "• Send an image with questions\n"
        "• I'll extract text and find answers\n"
        "• Answers are broadcasted to ALL subscribers\n\n"
        f"Currently {total_subscribers} subscribers receiving broadcasts\n\n"
        "Use /unsubscribe to stop receiving broadcasts"
    )
    
async def create_group_command(update: Update, context: CallbackContext):
    """Handle /create_group command - subscribe user."""
    await update.message.reply_text("Please provide the group name:")
    return CREATE_GROUP_NAME

async def get_group_name(update: Update, context: CallbackContext):
    """Store group name and ask for password"""
    group_name = update.message.text.strip()
    
    if len(group_name) < 3:
        await update.message.reply_text("Group name must be at least 3 characters")
        return CREATE_GROUP_NAME
    
    context.user_data['group_name'] = group_name
    await update.message.reply_text("Now please provide a password (first time): ")
    return CREATE_PASSWORD

async def get_password(update: Update, context: CallbackContext):
    """Store password and ask for confirmation"""
    password = update.message.text.strip()
    context.user_data['password'] = password
    await update.message.reply_text("Please confirm the password")
    return CONFIRM_PASSWORD

async def confirm_password(update: Update, context: CallbackContext):
    """Verify password and create group"""
    password = context.user_data['password']
    confirmation = update.message.text.strip()
    
    if password == confirmation:
        user_manager.create_group(context.user_data['group_name'], password, update.effective_chat.id)
        
        await update.message.reply_text(
            f"Group created!\nName: {context.user_data['group_name']}\n"
            f"Password: ||{password}||"
        )
        
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await update.message.reply_text("Passwords don't match. Please try again.")
        return CONFIRM_PASSWORD
    
async def fallback_handler(update: Update, context: CallbackContext):
    return context.user_data.get('state', CREATE_GROUP_NAME)
    
    
async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unsubscribe command."""
    chat_id = update.effective_chat.id
    
    if user_manager.is_subscribed(chat_id):
        user_manager.remove_subscriber(chat_id)
        await update.message.reply_text(
            "You have been unsubscribed from broadcasts.\n"
            "Use /start to subscribe again."
        )
    else:
        await update.message.reply_text("You are not currently subscribed.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    total_subscribers = user_manager.get_subscriber_count()
    await update.message.reply_text(
        f"📊 **Bot Statistics:**\n\n"
        f"• Total subscribers: {total_subscribers}\n"
        f"• Your ID: {update.effective_user.id}\n"
        f"• Subscribed: {'✅ Yes' if user_manager.is_subscribed(update.effective_chat.id) else '❌ No'}"
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command for manual broadcasting (admin only)."""
    if context.args:
        message = " ".join(context.args)
        successful_sends = await broadcast_to_all_subscribers(context, message)
        await update.message.reply_text(f"✅ Manual broadcast sent to {successful_sends} subscribers!")
    else:
        await update.message.reply_text("Usage: /broadcast <message>")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    group_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("create_group", create_group_command)],
        states={
            CREATE_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_group_name)],
            CREATE_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
            CONFIRM_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_password)],
        },
        fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler)],
        per_user=True
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(group_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    
    print(f"Bot running with {user_manager.get_subscriber_count()} existing subscribers!")
    print("Send images to process and broadcast!")
    application.run_polling()