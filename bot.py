import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH ---
# Lấy Token và kiểm tra xem có tồn tại không
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    print("❌ LỖI: Không tìm thấy TELEGRAM_TOKEN trong biến môi trường!")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID", "")
BANK_ACC = os.getenv("BANK_ACC", "")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {}

# --- 2. KẾT NỐI DATABASE ---
def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_SHEETS_JSON")
        if not creds_json:
            print("❌ LỖI: Thiếu GOOGLE_SHEETS_JSON")
            return None
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        return authorize(creds).open("BotSales")
    except Exception as e:
        print(f"❌ Lỗi Sheets: {e}")
        return None

def name_to_cmd(name):
    return re.sub(r'[^a-zA-Z0-9]', '', str(name)).lower()

def clean_price(price_val):
    try:
        if isinstance(price_val, (int, float)): return int(price_val)
        res = re.sub(r'[^\d]', '', str(price_val))
        return int(res) if res else 0
    except: return 0

# --- 3. LOGIC GIAO HÀNG ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        orders_sheet = db.worksheet("Orders")
        records = acc_sheet.get_all_records()
        
        for i, row in enumerate(records, 2):
            if str(row.get('Tên Sản Phẩm')).strip() == info['product'] and str(row.get('Trạng Thái')).strip() == "Sẵn sàng":
                tk, mk = str(row.get('Tài khoản', '')), str(row.get('Mật khẩu', ''))
                acc_info = f"{tk} | {mk}" if mk and mk.lower() != "n/a" else tk
                
                acc_sheet.update_cell(i, 4, "Đã bán")
                orders_sheet.append_row([
                    datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), 
                    order_id, str(info['user_id']), info['product'], info['price'], "Thành công", acc_info
                ])
                
                msg = (f"✅ **GIAO HÀNG THÀNH CÔNG**\n━━━━━━━━━━━━━━━\n"
                       f"📦 SP: **{info['product']}**\n💰 Giá: `{info['price']:,}`đ\n"
                       f"🎁 **Thông tin:**\n\n`{acc_info}`")
                
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                             params={"chat_id": info['user_id'], "text": msg, "parse_mode": "Markdown"})
                return True
        return False
    except Exception as e:
        print(f"❌ Lỗi giao hàng: {e}"); return False

# --- 4. WEBHOOK ---
@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}":
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    content = str(data.get("content", "")).upper()
    for order_id, info in list(pending_orders.items()):
        if order_id in content:
            threading.Thread(target=process_delivery, args=(order_id, info)).start()
            del pending_orders[order_id]
            break
    return jsonify({"status": "ok"}), 200

@app.route('/')
def home(): return "Bot is Online", 200

# --- 5. HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ & Hướng dẫn"]]
    if update.effective_user.id == ADMIN_ID: btns.append(["📥 Nhập Kho"])
    await update.message.reply_text("🛒 **NDTT PREMIUM STORE**\nChọn chức năng:", 
                                    reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_db().worksheet("DataBot").get_all_records()
    msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
    keyboard = []
    for row in data:
        name, price = str(row['Tên Sản Phẩm']), clean_price(row['Giá Tiền'])
        cmd = "/buy" + name_to_cmd(name)
        msg += f"🔹 *{name}*: `{price:,}`đ\n└ Lệnh: {cmd}\n\n"
        if "Sẵn sàng" in str(row.get('Trạng Thái', '')):
            keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_query_data=f"buy_{name}")])
    await update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE, product_name: str):
    data = get_db().worksheet("DataBot").get_all_records()
    selected = next((r for r in data if str(r['Tên Sản Phẩm']) == product_name), None)
    if not selected: return
    price = clean_price(selected['Giá Tiền'])
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
    context.user_data['last_id'] = order_id
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
    await update.effective_message.reply_photo(photo=qr_url, caption=f"💳 SP: {product_name}\n💰 Giá: {price:,}đ\n📝 Nội dung: {order_id}", 
                                             reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kiểm tra thanh toán", callback_query_data="check_manual")]]))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("buy_"):
        await query.answer(); await create_order(update, context, query.data.replace("buy_", ""))
    elif query.data == "check_manual":
        order_id = context.user_data.get('last_id')
        info = pending_orders.get(order_id)
        if not info: await query.answer("❌ Đơn hết hạn."); return
        await query.answer("🔄 Đang kiểm tra...")
        try:
            resp = requests.get(f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}", headers={"Authorization": f"Bearer {SEPAY_API_KEY}"}).json()
            for tr in resp.get('transactions', []):
                if order_id in str(tr.get('content', '')).upper() and int(float(tr.get('amount'))) >= info['price']:
                    if process_delivery(order_id, info): del pending_orders[order_id]; return
            await query.message.reply_text("❌ Chưa nhận được tiền.")
        except: pass

async def quick_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split('@')[0].lower()
    data = get_db().worksheet("DataBot").get_all_records()
    for row in data:
        if ("/buy" + name_to_cmd(row['Tên Sản Phẩm'])) == cmd:
            await create_order(update, context, row['Tên Sản Phẩm']); return

# --- 6. CHẠY BOT ---
def main():
    if not TELEGRAM_TOKEN: return
    # Khởi tạo application mà không dùng loop thủ công để tránh lỗi Render
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("buy", show_catalog))
    bot_app.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    bot_app.add_handler(MessageHandler(filters.Regex(r"^/buy"), quick_buy))
    bot_app.add_handler(CallbackQueryHandler(handle_interaction))

    # Chạy Webhook Flask
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    
    print("🚀 BOT ĐANG CHẠY...")
    bot_app.run_polling()

if __name__ == '__main__':
    main()
