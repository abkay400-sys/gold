import os
import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Set
import telebot
from telebot import types
import aiohttp
import ddddocr
import cv2
import numpy as np
from io import BytesIO

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8237500612:AAEoiekB1GMvBl6-BgFrRTR5-qwaEhZamNo")
ADMIN_ID = 8565708186
CONCURRENCY = 10
PROGRESS_INTERVAL = 50

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)
ocr = ddddocr.DdddOcr()

# State management
user_sessions: Dict[int, Dict] = {}
bot_start_time = time.time()
total_scans = 0

# Myanmar language messages
MESSAGES = {
    "start": "🎉 ကြိုဆိုပါသည်! Gold Scan Bot သို့ လည်ပတ်ရန်အတွက် /help ကို ရိုက်ပါ။",
    "help": """
🤖 **Gold Scan Bot အကူအညီ**

📋 **အဓိကအ号령များ:**
🔑 /genkey - API key ထုတ်ပေးခြင်း
📊 /listkeys - သိမ်းဆည်းထားသော keys များကို ကြည့်ခြင်း
🗑️ /delkey - Key ဖျက်ခြင်း
🔍 /scan - Session URL ကို scan ပြုလုပ်ခြင်း
📈 /result - Scan ရလဒ်များကို ကြည့်ခြင်း
⚙️ /status - Bot အခြေအနေကို ကြည့်ခြင်း

💡 **အကြံပြုချက်:**
• Inline buttons ကို အသုံးပြုပြီး command များကို အလွယ်တကူ အသုံးပြုနိုင်ပါသည်။
• Progress updates များကို လက်ခြင်း ရယူနိုင်ပါသည်။
""",
    "scan_start": "🔍 Scan စတင်နေသည်... {count} codes များကို စစ်ဆေးမည်ဖြစ်သည်။",
    "scan_progress": "⏳ Progress: {current}/{total} ({percent}%) ✅ Success: {success}",
    "scan_complete": "✨ Scan အပြီးသတ်! ✅ Success: {success} | ❌ Failed: {failed}",
    "no_session": "❌ Session မရှိသေးပါ။ /scan ကို အသုံးပြုပြီး စတင်ပါ။",
    "invalid_url": "❌ Invalid URL ဖြစ်သည်။ ကျေးဇူးပြုပြီး မှန်ကန်သော URL ထည့်သွင်းပါ။",
    "admin_only": "🔐 Admin သာ အသုံးပြုနိုင်သည်။",
    "key_generated": "🔑 Key ထုတ်ပေးပြီး!\n\n📌 Key ID: {key_id}\n⏰ Expires: {expires}\n📋 Plan: {plan}",
    "no_keys": "📭 သိမ်းဆည်းထားသော keys မရှိပါ။",
    "key_deleted": "🗑️ Key ဖျက်ပြီး!",
}

# Inline keyboard helpers
def get_main_menu():
    """Create main menu inline keyboard"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔍 Scan", callback_data="scan"))
    markup.add(types.InlineKeyboardButton("📈 Results", callback_data="results"))
    markup.add(types.InlineKeyboardButton("⚙️ Status", callback_data="status"))
    if user_sessions.get(ADMIN_ID):
        markup.add(types.InlineKeyboardButton("🔑 Keys", callback_data="keys"))
    return markup

def get_scan_menu():
    """Create scan menu inline keyboard"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📝 Enter URL", callback_data="enter_url"))
    markup.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back"))
    return markup

# Command handlers
@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Send welcome message"""
    user_id = message.chat.id
    user_sessions[user_id] = {"state": "idle", "session_url": None, "results": []}
    
    bot.reply_to(message, MESSAGES["start"], parse_mode="Markdown")
    bot.send_message(user_id, "🎯 **Main Menu**", reply_markup=get_main_menu(), parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def send_help(message):
    """Send help message"""
    bot.reply_to(message, MESSAGES["help"], parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def send_status(message):
    """Send bot status"""
    uptime = int(time.time() - bot_start_time)
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    
    status_msg = f"""
🤖 **Bot Status**

✅ Status: Online
⏱️ Uptime: {hours}h {minutes}m
📊 Total Scans: {total_scans}
👥 Active Users: {len(user_sessions)}
"""
    bot.reply_to(message, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=['scan'])
def start_scan(message):
    """Start scan process"""
    user_id = message.chat.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"state": "idle", "session_url": None, "results": []}
    
    msg = bot.send_message(user_id, "🔍 **Enter Session URL:**\n\n📌 Example: https://example.com/session", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_session_url)

def process_session_url(message):
    """Process session URL"""
    user_id = message.chat.id
    session_url = message.text.strip()
    
    if not session_url.startswith(('http://', 'https://')):
        bot.send_message(user_id, MESSAGES["invalid_url"])
        return
    
    user_sessions[user_id]["session_url"] = session_url
    bot.send_message(user_id, f"✅ Session URL သိမ်းဆည်းပြီး!\n\n🔗 URL: {session_url}\n\n📋 Codes ကို ထည့်သွင်းပါ (တစ်လိုင်းစီ):")
    bot.register_next_step_handler(message, process_codes)

def process_codes(message):
    """Process codes and start scanning"""
    user_id = message.chat.id
    codes = [code.strip() for code in message.text.split('\n') if code.strip()]
    
    if not codes:
        bot.send_message(user_id, "❌ Codes မရှိပါ။")
        return
    
    # Send progress message
    progress_msg = bot.send_message(user_id, MESSAGES["scan_start"].format(count=len(codes)), parse_mode="Markdown")
    
    # Simulate scanning with progress updates
    asyncio.run(scan_codes(user_id, codes, progress_msg.message_id))

async def scan_codes(user_id, codes, progress_msg_id):
    """Scan codes with progress updates"""
    global total_scans
    
    success_codes = []
    failed_codes = []
    
    for idx, code in enumerate(codes):
        # Simulate OCR processing
        await asyncio.sleep(0.1)
        
        # Random success/failure for demo
        if hash(code) % 3 != 0:
            success_codes.append(code)
        else:
            failed_codes.append(code)
        
        # Send progress update every PROGRESS_INTERVAL codes
        if (idx + 1) % PROGRESS_INTERVAL == 0 or idx == len(codes) - 1:
            progress_text = MESSAGES["scan_progress"].format(
                current=idx + 1,
                total=len(codes),
                percent=int((idx + 1) / len(codes) * 100),
                success=len(success_codes)
            )
            try:
                bot.edit_message_text(progress_text, user_id, progress_msg_id, parse_mode="Markdown")
            except:
                pass
    
    # Send final result
    total_scans += 1
    result_text = MESSAGES["scan_complete"].format(
        success=len(success_codes),
        failed=len(failed_codes)
    )
    bot.send_message(user_id, result_text, parse_mode="Markdown")
    
    # Save results
    user_sessions[user_id]["results"].append({
        "timestamp": datetime.now().isoformat(),
        "success": success_codes,
        "failed": failed_codes
    })

@bot.message_handler(commands=['result'])
def show_results(message):
    """Show scan results"""
    user_id = message.chat.id
    
    if user_id not in user_sessions or not user_sessions[user_id]["results"]:
        bot.send_message(user_id, MESSAGES["no_session"])
        return
    
    results = user_sessions[user_id]["results"][-1]  # Last result
    result_text = f"""
📊 **Scan Results**

✅ Success: {len(results['success'])}
❌ Failed: {len(results['failed'])}
⏰ Time: {results['timestamp']}

📋 Success Codes:
{', '.join(results['success'][:10])}
{'...' if len(results['success']) > 10 else ''}
"""
    bot.send_message(user_id, result_text, parse_mode="Markdown")

@bot.message_handler(commands=['genkey'])
def gen_key(message):
    """Generate API key (Admin only)"""
    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, MESSAGES["admin_only"])
        return
    
    key_id = int(time.time() * 1000)
    expires = datetime.now().isoformat()
    
    key_msg = MESSAGES["key_generated"].format(
        key_id=key_id,
        expires=expires,
        plan="1 Day"
    )
    bot.send_message(message.chat.id, key_msg, parse_mode="Markdown")

@bot.message_handler(commands=['listkeys'])
def list_keys(message):
    """List API keys (Admin only)"""
    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, MESSAGES["admin_only"])
        return
    
    bot.send_message(message.chat.id, "🔑 **API Keys**\n\n(Dashboard မှာ ကြည့်ပါ)", parse_mode="Markdown")

@bot.message_handler(commands=['delkey'])
def del_key(message):
    """Delete API key (Admin only)"""
    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, MESSAGES["admin_only"])
        return
    
    bot.send_message(message.chat.id, MESSAGES["key_deleted"])

@bot.callback_query_handler(func=lambda call: call.data in ['scan', 'results', 'status', 'keys', 'back'])
def handle_buttons(call):
    """Handle inline button callbacks"""
    user_id = call.from_user.id
    
    if call.data == 'scan':
        start_scan(call.message)
    elif call.data == 'results':
        show_results(call.message)
    elif call.data == 'status':
        send_status(call.message)
    elif call.data == 'keys':
        list_keys(call.message)
    elif call.data == 'back':
        bot.send_message(user_id, "🎯 **Main Menu**", reply_markup=get_main_menu(), parse_mode="Markdown")

# Start polling
if __name__ == "__main__":
    print("🤖 Bot is running...")
    bot.infinity_polling()
