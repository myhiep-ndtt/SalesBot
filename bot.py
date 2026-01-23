import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID", "")
BANK_ACC = os.getenv("BANK_ACC", "")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {}

# --- 2. KẾT NỐI GOOGLE SHEETS ---
def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_SHEETS_JSON")
        if not creds_json: return None
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        return authorize(creds).open("BotSales")
    except Exception as e:
        print(f"❌ Lỗi kết nối Sheets: {e}")
        return None

# --- 3. XỬ LÝ GIAO HÀNG (Khớp Sheet 'acc' và 'Orders') ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        orders_sheet = db.worksheet("Orders")
        records = acc_sheet.get_all_records()
        
        for i, row in enumerate(records, 2):
            p_name = str(row.get('Tên Sản Phẩm', '')).strip()
            status = str(row.get('Trạng Thái', '')).strip()
            
            # Chấp nhận cả "Sẵn sàng" và "Hoạt Động" như trong ảnh acc của bạn
            if p_name == info['product'] and status in ["Sẵn sàng", "Hoạt Động"]:
                tk = str(row.get('Tài khoản', '')).strip()
                mk = str(row.get('Mật khẩu', '')).strip()
                acc_info = f"{tk} | {mk}" if mk and mk.lower() != "n/a" else tk
                
                # Cập nhật trạng thái
                acc_sheet.update_cell(i, 4, "Đã bán")
                
                # Ghi vào Orders (7 cột như ảnh)
                orders_sheet.append_row([
                    datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                    order_id, str(info['user_id']), info['product'], info['price'], "Thành công", acc_info
                ])
                
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                             params={"chat_id": info['user_id'], "text": f"✅ **Giao hàng thành công!**\n📦 {info['product']}\n🔑 Key: `{acc_info}`", "parse_mode": "Markdown"})
                return True
        return False
    except: return False

# --- 4. FLASK WEBHOOK ---
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
def home(): return "Bot Live", 200

# --- 5. TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ & Hướng dẫn"]]
    if update.effective_user.id == ADMIN_ID: btns.append(["📥 Nhập Kho"])
    await update.message.reply_text("🛒 **NDTT PREMIUM STORE**\nHệ thống bán acc tự động 24/7.", 
                                    reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db = get_db()
        data = db.worksheet("DataBot").get_all_records()
        msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
        keyboard = []
        
        for row in data:
            name = str(row.get('Tên Sản Phẩm', '')).strip()
            if not name: continue
            
            # Làm sạch giá tiền
            try:
                price = int(re.sub(r'[^\d]', '', str(row.get('Giá Tiền', 0))))
            except: price = 0
            
            icon = str(row.get('Icon', '🔹')).strip()
            status = str(row.get('Trạng Thái', '')).strip()
            
            cmd = "/buy" + re.sub(r'[^a-zA-Z0-9]', '', name).lower()
            msg += f"{icon} *{name}*: `{price:,}`đ\n└ Lệnh: {cmd}\n\n"
            
            # FIX LỖI: callback_data thay vì callback_query_data
            if "Sẵn sàng" in status:
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_data=f"buy_{name}")])
        
        await update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Lỗi lấy bảng giá: {e}")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("buy_"):
        product_name = query.data.replace("buy_", "")
        # Logic tạo đơn hàng và QR Code...
        db = get_db()
        data = db.worksheet("DataBot").get_all_records()
        selected = next((r for r in data if str(r.get('Tên Sản Phẩm', '')).strip() == product_name), None)
        
        if selected:
            price = int(re.sub(r'[^\d]', '', str(selected.get('Giá Tiền', 0))))
            order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
            
            qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
            await query.message.reply_photo(
                photo=qr_url, 
                caption=f"💳 **THANH TOÁN**\n📦 SP: {product_name}\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kiểm tra thanh toán", callback_data="check_manual")]])
            )

# --- 6. KHỞI CHẠY ---
def main():
    if not TELEGRAM_TOKEN: return
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    application.add_handler(CallbackQueryHandler(handle_interaction))

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    print("🚀 BOT LIVE!"); application.run_polling()

if __name__ == '__main__':
    main()
