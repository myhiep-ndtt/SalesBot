import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- CẤU HÌNH BIẾN MÔI TRƯỜNG ---
ADMIN_ID = int(os.getenv("ADMIN_ID"))
BANK_ID = os.getenv("BANK_ID")
BANK_ACC = os.getenv("BANK_ACC")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {} 

def get_db():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.getenv("GOOGLE_SHEETS_JSON")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
    return authorize(creds).open("BotSales")

# --- HÀM LƯU NGƯỜI DÙNG (CHỈ ID VÀ USERNAME) ---
def save_user(user_id, username):
    try:
        db = get_db()
        sheet = db.worksheet("Users")
        existing_ids = sheet.col_values(1)
        if str(user_id) not in existing_ids:
            sheet.append_row([str(user_id), username if username else "N/A"])
    except: pass

# --- HÀM GIAO HÀNG (GIỮ NGUYÊN ĐỊNH DẠNG CỦA BẠN) ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        orders_sheet = db.worksheet("Orders")
        records = acc_sheet.get_all_records()
        
        for i, row in enumerate(records, 2):
            if row['Tên Sản Phẩm'] == info['product'] and row['Trạng Thái'] == "Sẵn sàng":
                tk, mk = row['Tài khoản'], row['Mật khẩu']
                acc_sheet.update_cell(i, 4, "Đã bán")
                
                orders_sheet.append_row([
                    datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), 
                    order_id, info['user_id'], info['product'], info['price'], "Thành công", f"{tk}|{mk}"
                ])
                
                # GIỮ NGUYÊN ĐỊNH DẠNG TIN NHẮN BAN ĐẦU CỦA BẠN
                full_message = (
                    f"✅ **THANH TOÁN THÀNH CÔNG**\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"📦 **Sản phẩm:** {info['product']}\n"
                    f"🆔 **Mã đơn:** `{order_id}`\n"
                    f"💰 **Số tiền:** `{info['price']:,}`đ\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🎁 **Thông tin tài khoản:**\n\n"
                    f"👤 Tài khoản: `{tk}`\n"
                    f"🔑 Mật khẩu: `{mk}`\n\n"
                    f"⚠️ *Lưu ý: Vui lòng đổi mật khẩu sau khi đăng nhập.*"
                )
                
                token = os.getenv("TELEGRAM_TOKEN")
                requests.get(f"https://api.telegram.org/bot{token}/sendMessage", params={
                    "chat_id": info['user_id'], "text": full_message, "parse_mode": "Markdown"
                })
                return True
        return False
    except Exception as e:
        print(f"Lỗi Giao Hàng: {e}")
        return False

# --- WEBHOOK SEPAY ---
@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}":
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    content = data.get("content", "").upper()
    for order_id, info in list(pending_orders.items()):
        if order_id in content:
            threading.Thread(target=process_delivery, args=(order_id, info)).start()
            del pending_orders[order_id]
            break
    return jsonify({"status": "ok"}), 200

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    threading.Thread(target=save_user, args=(user.id, user.username)).start()
    btns = [["📊 Xem Bảng Giá"], ["💳 Hướng dẫn", "☎️ Hỗ trợ"]]
    if user.id == ADMIN_ID: btns.append(["📥 Nhập Kho Hàng Loạt"])
    await update.message.reply_text("🛒 Shop Acc Premium NDTT Auto 24/7 kính chào quý khách!", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_db().worksheet("DataBot").get_all_records()
    msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
    keyboard = []
    for row in data:
        msg += f"{row['Icon']} *{row['Tên Sản Phẩm']}*: `{row['Giá Tiền']:,}`đ\n"
        if "Còn hàng" in str(row['Trạng Thái']):
            keyboard.append([InlineKeyboardButton(f"Mua {row['Tên Sản Phẩm']}", callback_query_data=f"buy_{row['Tên Sản Phẩm']}")])
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product = query.data.replace("buy_", "")
    price = 0
    for row in get_db().worksheet("DataBot").get_all_records():
        if row['Tên Sản Phẩm'] == product:
            price = int(row['Giá Tiền'])
            break
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    pending_orders[order_id] = {"user_id": query.from_user.id, "product": product, "price": price}
    context.user_data['last_id'] = order_id
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
    await query.message.reply_photo(photo=qr_url, caption=f"💳 **THANH TOÁN**\n📦 SP: {product}\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`", 
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kiểm tra thanh toán", callback_query_data="check_manual")]]))

async def verify_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = context.user_data.get('last_id')
    info = pending_orders.get(order_id)
    if not info:
        await query.answer("❌ Đơn hàng không tồn tại hoặc đã xử lý.", show_alert=True)
        return
    try:
        resp = requests.get(f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}", 
                            headers={"Authorization": f"Bearer {SEPAY_API_KEY}"}).json()
        for tr in resp.get('transactions', []):
            if order_id in tr.get('content', '') and int(float(tr.get('amount'))) >= info['price']:
                if process_delivery(order_id, info):
                    del pending_orders[order_id]
                    return
        await query.message.reply_text("❌ Chưa tìm thấy tiền vào. Thử lại sau 30 giây.")
    except: pass

# --- ADMIN COMMANDS ---
async def admin_import_capcut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', update.message.text)
    if emails:
        new_rows = [["CapCut Pro", e, "hung@1234", "Sẵn sàng", f"Auto {datetime.datetime.now().strftime('%d/%m')}"] for e in emails]
        get_db().worksheet("acc").append_rows(new_rows)
        await update.message.reply_text(f"✅ Đã nạp `{len(emails)}` acc CapCut Pro thành công!")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg: return
    users = get_db().worksheet("Users").col_values(1)[1:]
    success = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=f"🔔 **THÔNG BÁO TỪ HỆ THỐNG**\n\n{msg}", parse_mode='Markdown')
            success += 1
            await asyncio.sleep(0.05)
        except: continue
    await update.message.reply_text(f"✅ Đã gửi tới {success}/{len(users)} người dùng.")

async def admin_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = update.message.text
    if "NHAP" in text:
        try:
            lines = text.split('\n')
            product = lines[0].replace("NHAP", "").strip()
            new_rows = []
            for line in lines[1:]:
                if "|" in line:
                    parts = line.split("|")
                    email = parts[0].replace("Email:", "").strip()
                    password = parts[1].replace("Pass:", "").strip()
                    new_rows.append([product, email, password, "Sẵn sàng", f"Nhập {datetime.datetime.now().strftime('%d/%m')}"])
            get_db().worksheet("acc").append_rows(new_rows)
            await update.message.reply_text(f"✅ Đã nhập thành công {len(new_rows)} acc cho `{product}`")
        except: pass

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("nhapcapcut", admin_import_capcut))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^NHAP"), admin_import))
    application.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(verify_manual, pattern="check_manual"))
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    application.run_polling()

if __name__ == '__main__':
    main()
