import logging
import sqlite3
from config import TELEGRAM_BOT_TOKEN
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler
from image_processor import extract_text_from_image, process_image_and_answer


class PersistentUserManager:
    def __init__(self, db_file="data/subscribers.db"):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for subscribers."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        print(f"✅ Database initialized with {self.get_subscriber_count()} existing subscribers")
    
    def add_subscriber(self, chat_id, username=None, first_name=None, last_name=None):
        """Add a subscriber to the database."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO subscribers (chat_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, username, first_name, last_name))
        conn.commit()
        conn.close()
    
    def remove_subscriber(self, chat_id):
        """Remove a subscriber from the database."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM subscribers WHERE chat_id = ?', (chat_id,))
        conn.commit()
        conn.close()
    
    def get_all_subscribers(self):
        """Get all subscriber chat IDs."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM subscribers')
        subscribers = [row[0] for row in cursor.fetchall()]
        conn.close()
        return subscribers
    
    def get_subscriber_count(self):
        """Get total number of subscribers."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM subscribers')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def is_subscribed(self, chat_id):
        """Check if a user is subscribed."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM subscribers WHERE chat_id = ?', (chat_id,))
        result = cursor.fetchone() is not None
        conn.close()
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
    
    logging.info(f"📤 Broadcasting to {total_subscribers} subscribers...")
    
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
            # Optionally remove inactive subscribers
            # user_manager.remove_subscriber(chat_id)
    
    logging.info(f"✅ Broadcast completed: {successful_sends}/{total_subscribers} successful")
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
        await update.message.reply_text("❌ Please provide a question or text to process.")
        return
    
    processing_msg = await update.message.reply_text("🔄 Processing your question...")
    
    try:
        # Process text with AI
        await update.effective_chat.send_action("typing")
        answers = process_image_and_answer(user_text)
        
        if not answers or len(answers.strip()) < 5:
            await processing_msg.edit_text("❌ No meaningful answers could be generated.")
            return
        
        # Send answer to user
        await update.message.reply_text(answers, parse_mode='Markdown')
        await processing_msg.delete()
        
        total_subscribers = user_manager.get_subscriber_count()
        if total_subscribers > 0:
            broadcast_text = f"📢 **New answers processed by {user.first_name}:**\n\n{answers}"
            successful_sends = await broadcast_to_all_subscribers(context, broadcast_text)
            
            await update.message.reply_text(
                f"✅ Answers broadcasted to {successful_sends} subscribers!"
            )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

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
    
    processing_msg = await update.message.reply_text("🔄 Przetwarzam obrazek...")
    
    try:
        # Get image and extract text
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        await update.effective_chat.send_action("typing")
        raw_text = extract_text_from_image(bytes(image_bytes))
        
        if not raw_text or len(raw_text.strip()) < 10:
            await processing_msg.edit_text("❌ Nie znaleziono tekstu.")
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
            
            # Notify sender about broadcast
            await update.message.reply_text(
                f"✅ Answers broadcasted to {successful_sends} subscribers!"
            )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Błąd: {str(e)}")

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
        "🤖 **Welcome to the Answer Bot!**\n\n"
        "✅ You are now subscribed to receive answers from all users!\n\n"
        "**How it works:**\n"
        "• Send an image with questions\n"
        "• I'll extract text and find answers\n"
        "• Answers are broadcasted to ALL subscribers\n\n"
        f"📊 Currently {total_subscribers} subscribers receiving broadcasts\n\n"
        "Use /unsubscribe to stop receiving broadcasts"
    )
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unsubscribe command."""
    chat_id = update.effective_chat.id
    
    if user_manager.is_subscribed(chat_id):
        user_manager.remove_subscriber(chat_id)
        await update.message.reply_text(
            "❌ You have been unsubscribed from broadcasts.\n"
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
    # Add admin check here if needed
    if context.args:
        message = " ".join(context.args)
        successful_sends = await broadcast_to_all_subscribers(context, message)
        await update.message.reply_text(f"✅ Manual broadcast sent to {successful_sends} subscribers!")
    else:
        await update.message.reply_text("Usage: /broadcast <message>")

# ... (keep your existing parse_questions_from_text and format_answer functions)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    
    print(f"🚀 Bot running with {user_manager.get_subscriber_count()} existing subscribers!")
    print("📧 Send images to process and broadcast!")
    application.run_polling()