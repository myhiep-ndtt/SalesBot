import os, json, random, string, datetime, threading, requests
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
pending_orders = {} # Bộ nhớ tạm để khớp mã đơn

def get_db():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.getenv("GOOGLE_SHEETS_JSON")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
    return authorize(creds).open("BotSales")

# --- HÀM GIAO HÀNG ĐỊNH DẠNG ĐẸP ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        orders_sheet = db.worksheet("Orders")
        records = acc_sheet.get_all_records()
        
        for i, row in enumerate(records, 2):
            if row['Tên Sản Phẩm'] == info['product'] and row['Trạng Thái'] == "Sẵn sàng":
                tk, mk = row['Tài khoản'], row['Mật khẩu']
                
                # Cập nhật trạng thái 'Đã bán'
                acc_sheet.update_cell(i, 4, "Đã bán")
                
                # Ghi lịch sử đơn hàng
                orders_sheet.append_row([
                    datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), 
                    order_id, info['user_id'], info['product'], info['price'], "Thành công", f"{tk}|{mk}"
                ])
                
                # Tin nhắn trả về định dạng cũ chuyên nghiệp
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

# --- WEBHOOK (TỰ ĐỘNG GIAO) ---
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

# --- CHECK THỦ CÔNG (DÙNG SEPAY_API_KEY) ---
async def verify_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = context.user_data.get('last_id')
    info = pending_orders.get(order_id)
    
    if not info:
        await query.answer("❌ Đơn hàng đã xử lý hoặc không tồn tại.", show_alert=True)
        return

    await query.answer("⌛ Đang kiểm tra giao dịch từ ngân hàng...")
    headers = {"Authorization": f"Bearer {SEPAY_API_KEY}"}
    try:
        resp = requests.get(f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}", headers=headers).json()
        for tr in resp.get('transactions', []):
            if order_id in tr.get('content', '') and int(float(tr.get('amount'))) >= info['price']:
                if process_delivery(order_id, info):
                    del pending_orders[order_id]
                    return
        await query.message.reply_text("❌ Hệ thống chưa thấy tiền vào. Vui lòng thử lại sau 30 giây.")
    except:
        await query.message.reply_text("❌ Lỗi kết nối SePay. Vui lòng liên hệ Admin.")

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [["📊 Xem Bảng Giá"], ["💳 Hướng dẫn", "☎️ Hỗ trợ"]]
    if update.effective_user.id == ADMIN_ID: btns.append(["📥 Nhập Kho Hàng Loạt"])
    await update.message.reply_text("🛒 Chào mừng bạn đến với Shop Auto!", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

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
    kb = [[InlineKeyboardButton("✅ Tôi đã chuyển khoản", callback_query_data="check_manual")]]
    await query.message.reply_photo(photo=qr_url, caption=f"💳 **THANH TOÁN**\n📦 SP: {product}\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`", reply_markup=InlineKeyboardMarkup(kb))

async def admin_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if "NHAP" in update.message.text:
        lines = update.message.text.split('\n')
        product = lines[0].replace("NHAP", "").strip()
        new_rows = [[product, l.split("|")[0].strip(), l.split("|")[1].strip(), "Sẵn sàng", "Admin"] for l in lines[1:] if "|" in l]
        if new_rows:
            get_db().worksheet("acc").append_rows(new_rows)
            await update.message.reply_text(f"✅ Đã nhập {len(new_rows)} tài khoản.")

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^NHAP"), admin_import))
    application.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(verify_manual, pattern="check_manual"))
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    application.run_polling()

if __name__ == '__main__':
    main()
