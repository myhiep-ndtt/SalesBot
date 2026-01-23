import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH HỆ THỐNG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID", "")
BANK_ACC = os.getenv("BANK_ACC", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {}

def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_SHEETS_JSON")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        return authorize(creds).open("BotSales")
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheets: {e}")
        return None

# --- 2. LOGIC KIỂM TRA KHO ---
def get_stock_counts():
    try:
        db = get_db()
        records = db.worksheet("acc").get_all_records()
        counts = {}
        for row in records:
            p_name = str(row.get('Tên Sản Phẩm', '')).strip()
            status = str(row.get('Trạng Thái', '')).strip()
            if status in ["Sẵn sàng", "Hoạt Động"]:
                counts[p_name] = counts.get(p_name, 0) + 1
        return counts
    except: return {}

# --- 3. LỆNH ADMIN /nhap ---
async def nhap_kho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    raw_input = " ".join(context.args)
    if "|" not in raw_input:
        await update.message.reply_text("⚠️ Cú pháp: `/nhap Tên SP | Tài khoản | Mật khẩu`", parse_mode='Markdown')
        return
    try:
        parts = [p.strip() for p in raw_input.split("|")]
        p_name, tk = parts[0], parts[1]
        mk = parts[2] if len(parts) > 2 else "N/A"
        db = get_db()
        db.worksheet("acc").append_row([p_name, tk, mk, "Sẵn sàng", f"Bot nạp {datetime.datetime.now().strftime('%d/%m %H:%M')}"])
        await update.message.reply_text(f"✅ Đã nạp thành công **{p_name}** vào kho.", parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ Lỗi: {e}")

# --- 4. LỆNH /list (LIỆT KÊ SẢN PHẨM & TRẠNG THÁI) ---
async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db = get_db()
        data = db.worksheet("DataBot").get_all_records()
        stocks = get_stock_counts()
        msg = "📊 **DANH SÁCH SẢN PHẨM & TRẠNG THÁI**\n"
        msg += "━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for row in data:
            name = str(row.get('Tên Sản Phẩm', '')).strip()
            if not name: continue
            price = int(re.sub(r'[^\d]', '', str(row.get('Giá Tiền', 0))))
            icon = str(row.get('Icon', '🔹')).strip()
            count = stocks.get(name, 0)
            
            status_text = f"🟢 Còn hàng ({count})" if count > 0 else "🔴 Hết hàng"
            msg += f"{icon} **{name}**\n├ Giá: `{price:,}`đ\n└ Trạng thái: {status_text}\n\n"
            
            if count > 0:
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_data=f"buy_{name}")])
        
        msg += "👉 _Chọn nút bên dưới để lấy mã thanh toán._"
        await (update.callback_query.message.edit_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)) 
               if update.callback_query else update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)))
    except Exception as e: await update.effective_message.reply_text(f"❌ Lỗi tải bảng giá: {e}")

# --- 5. LỆNH /contact & /buy ---
async def contact_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("☎️ **LIÊN HỆ HỖ TRỢ**\n\nAdmin: @NgDanhThanhTrung\nPhục vụ tận tâm 24/7.", parse_mode='Markdown')

async def buy_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("💡 **HƯỚNG DẪN MUA HÀNG**\n\n1️⃣ Gõ /list xem sản phẩm.\n2️⃣ Chọn 'Mua', chuyển khoản đúng mã đơn.\n3️⃣ Nhận tài khoản ngay lập tức.", parse_mode='Markdown')

# --- 6. XỬ LÝ THANH TOÁN ---
async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("buy_"):
        product_name = query.data.replace("buy_", "")
        stocks = get_stock_counts()
        if stocks.get(product_name, 0) == 0:
            await query.message.reply_text("❌ Sản phẩm vừa hết hàng!")
            return

        db = get_db()
        data = db.worksheet("DataBot").get_all_records()
        price = next((int(re.sub(r'[^\d]', '', str(row.get('Giá Tiền', 0)))) for row in data if str(row.get('Tên Sản Phẩm', '')).strip() == product_name), 0)
        
        order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
        
        caption = f"💳 **THANH TOÁN ĐƠN HÀNG**\n📦 SP: **{product_name}**\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`\n\n⚠️ *Lưu ý: Chạm mã `{order_id}` để copy. Chuyển đúng nội dung để nhận hàng tự động!*"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
        await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode='Markdown')

# --- 7. GIAO HÀNG (WEBHOOK SEPAY) ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        records = acc_sheet.get_all_records()
        for i, row in enumerate(records, 2):
            if str(row.get('Tên Sản Phẩm', '')).strip() == info['product'] and str(row.get('Trạng Thái', '')).strip() in ["Sẵn sàng", "Hoạt Động"]:
                acc_info = f"{row['Tài khoản']} | {row['Mật khẩu']}" if str(row['Mật khẩu']).upper() != "N/A" else row['Tài khoản']
                acc_sheet.update_cell(i, 4, "Đã bán")
                db.worksheet("Orders").append_row([datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), order_id, str(info['user_id']), info['product'], info['price'], "Thành công", acc_info])
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", params={"chat_id": info['user_id'], "text": f"✅ **GIAO HÀNG THÀNH CÔNG**\n📦 SP: {info['product']}\n🔑 Thông tin: `{acc_info}`", "parse_mode": "Markdown"})
                return True
        return False
    except: return False

@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}": return jsonify({"error": "Unauthorized"}), 401
    content = str(request.json.get("content", "")).upper()
    for order_id, info in list(pending_orders.items()):
        if order_id in content:
            threading.Thread(target=process_delivery, args=(order_id, info)).start()
            del pending_orders[order_id]
            break
    return jsonify({"status": "ok"}), 200

# --- 8. KHỞI CHẠY & MENU ---
async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Khởi động bot"),
        BotCommand("list", "Xem bảng giá & trạng thái kho"),
        BotCommand("buy", "Hướng dẫn mua hàng"),
        BotCommand("contact", "Liên hệ Admin @NgDanhThanhTrung")
    ])

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🛒 **NDTT STORE - CHÀO MỪNG BẠN**", reply_markup=ReplyKeyboardMarkup([["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ"]], resize_keyboard=True))))
    application.add_handler(CommandHandler("nhap", nhap_kho))
    application.add_handler(CommandHandler("list", show_catalog))
    application.add_handler(CommandHandler("buy", buy_info))
    application.add_handler(CommandHandler("contact", contact_info))
    
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá", "☎️ Hỗ trợ"]), lambda u, c: show_catalog(u, c) if "Giá" in u.message.text else contact_info(u, c)))
    application.add_handler(CallbackQueryHandler(handle_interaction))
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    print("🚀 BOT ĐÃ SẴN SÀNG!"); application.run_polling()

if __name__ == '__main__': main()
