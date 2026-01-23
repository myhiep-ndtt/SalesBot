import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH ---
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
        print(f"❌ Lỗi Sheets: {e}")
        return None

# --- 2. LOGIC KHO HÀNG ---
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

# --- 3. LỆNH ADMIN /NHAP (BỘ LỌC CHUỖI DÍNH LIỀN) ---
async def nhap_kho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    raw_input = " ".join(context.args)
    
    if "|" not in raw_input:
        await update.message.reply_text("⚠️ Cú pháp: `/nhap Tên SP | chuỗi_email|pass_dính_liền`")
        return

    try:
        parts = raw_input.split("|", 1)
        product_name = parts[0].strip()
        data_string = parts[1].strip()

        # Regex nhận diện Email và bóc tách Password cho đến khi gặp Email tiếp theo
        pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\|(.*?)(?=[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|$)'
        accounts = re.findall(pattern, data_string)

        if not accounts:
            await update.message.reply_text("❌ Không tìm thấy định dạng email|pass trong chuỗi.")
            return

        db = get_db()
        acc_sheet = db.worksheet("acc")
        rows_to_add = []
        now = datetime.datetime.now().strftime('%d/%m %H:%M')

        for email, password in accounts:
            rows_to_add.append([product_name, email.strip(), password.strip(), "Sẵn sàng", f"Nạp {now}"])

        acc_sheet.append_rows(rows_to_add)
        await update.message.reply_text(f"✅ **NẠP KHO THÀNH CÔNG**\n📦 SP: `{product_name}`\n👤 SL: `{len(rows_to_add)}` tài khoản.")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {e}")

# --- 4. HIỂN THỊ BẢNG GIÁ (/LIST) ---
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
            
            status_text = f"🟢 Còn {count}" if count > 0 else "🔴 Hết hàng"
            msg += f"{icon} **{name}**\n├ Giá: `{price:,}`đ\n└ Trạng thái: {status_text}\n\n"
            if count > 0:
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_data=f"buy_{name}")])
        
        msg += "👉 _Chọn sản phẩm bên dưới để lấy mã thanh toán._"
        if update.callback_query:
            await update.callback_query.message.edit_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.effective_message.reply_text(f"❌ Lỗi: {e}")

# --- 5. XỬ LÝ MUA HÀNG & GIAO HÀNG ---
async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("buy_"):
        product_name = query.data.replace("buy_", "")
        db = get_db()
        data = db.worksheet("DataBot").get_all_records()
        price = next((int(re.sub(r'[^\d]', '', str(r['Giá Tiền']))) for r in data if r['Tên Sản Phẩm'] == product_name), 0)
        
        order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
        
        caption = f"💳 **THANH TOÁN**\n📦 SP: **{product_name}**\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`\n\n*(Chạm mã để copy. Chuyển đúng nội dung để nhận hàng tự động!)*"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
        await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode='Markdown')

def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        records = acc_sheet.get_all_records()
        for i, row in enumerate(records, 2):
            if str(row['Tên Sản Phẩm']) == info['product'] and str(row['Trạng Thái']) in ["Sẵn sàng", "Hoạt Động"]:
                acc_info = f"{row['Tài khoản']} | {row['Mật khẩu']}" if str(row['Mật khẩu']).upper() != "N/A" else row['Tài khoản']
                acc_sheet.update_cell(i, 4, "Đã bán")
                db.worksheet("Orders").append_row([datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), order_id, str(info['user_id']), info['product'], info['price'], "Thành công", acc_info])
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", params={"chat_id": info['user_id'], "text": f"✅ **GIAO HÀNG THÀNH CÔNG**\n📦 {info['product']}\n🔑 Key: `{acc_info}`", "parse_mode": "Markdown"})
                return True
        return False
    except: return False

@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}": return jsonify({"status": "fail"}), 401
    content = str(request.json.get("content", "")).upper()
    for oid, info in list(pending_orders.items()):
        if oid in content:
            threading.Thread(target=process_delivery, args=(oid, info)).start()
            del pending_orders[oid]; break
    return jsonify({"status": "ok"}), 200

# --- 6. KHỞI CHẠY & MENU ---
async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Khởi động bot"),
        BotCommand("list", "Xem bảng giá & kho hàng"),
        BotCommand("buy", "Hướng dẫn mua hàng"),
        BotCommand("contact", "Liên hệ Admin @NgDanhThanhTrung")
    ])

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🛒 **NDTT STORE**", reply_markup=ReplyKeyboardMarkup([["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ"]], resize_keyboard=True))))
    application.add_handler(CommandHandler("nhap", nhap_kho))
    application.add_handler(CommandHandler("list", show_catalog))
    application.add_handler(CommandHandler("buy", lambda u, c: u.message.reply_text("💡 **Hướng dẫn:** Chọn sản phẩm từ /list, chuyển khoản đúng mã đơn để nhận hàng ngay.")))
    application.add_handler(CommandHandler("contact", lambda u, c: u.message.reply_text("☎️ Admin: @NgDanhThanhTrung")))
    
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá", "☎️ Hỗ trợ"]), lambda u, c: show_catalog(u, c) if "Giá" in u.message.text else u.message.reply_text("☎️ Admin: @NgDanhThanhTrung")))
    application.add_handler(CallbackQueryHandler(handle_interaction))
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    application.run_polling()

if __name__ == '__main__': main()
