import os, json, random, string, datetime, threading, requests
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- CẤU HÌNH HỆ THỐNG ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID")
BANK_ACC = os.getenv("BANK_ACC")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY")

app = Flask(__name__)

# Kết nối Google Sheets
def get_db():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.getenv("GOOGLE_SHEETS_JSON"))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return authorize(creds).open("BotSales")

# --- WEB SERVER (Cho Render & Webhook) ---
@app.route('/')
def home(): return "Bot is Online!"

@app.route('/sepay-webhook', methods=['POST'])
async def sepay_webhook():
    # Phần này xử lý khi SePay đẩy tin nhắn tiền về tự động (Webhook)
    return jsonify({"status": "ok"}), 200

# --- CHỨC NĂNG ADMIN: NHẬP KHO ---
async def admin_nhap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID: return

    text = update.message.text
    if text.startswith("NHAP"):
        lines = text.split('\n')
        product_name = lines[0].replace("NHAP", "").strip()
        
        try:
            acc_sheet = get_db().worksheet("acc")
            new_rows = []
            for line in lines[1:]:
                if "|" in line:
                    acc, pwd = line.split("|")
                    new_rows.append([product_name, acc.strip(), pwd.strip(), "Sẵn sàng", f"Nhập {datetime.datetime.now().strftime('%d/%m')}"])
            
            if new_rows:
                acc_sheet.append_rows(new_rows)
                await update.message.reply_text(f"✅ **ĐÃ NHẬP KHO**\n📦 SP: {product_name}\n➕ Số lượng: {len(new_rows)}")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {str(e)}")

# --- GIAO DIỆN KHÁCH HÀNG ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    buttons = [["📊 Xem Bảng Giá"], ["💳 Hướng dẫn", "☎️ Hỗ trợ"]]
    if user_id == ADMIN_ID:
        buttons.append(["📥 Nhập Kho Hàng Loạt"])
    
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await update.message.reply_text("👋 Chào mừng bạn đến với Shop Auto!\nSử dụng menu bên dưới để mua hàng.", reply_markup=reply_markup)

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_db().worksheet("DataBot")
        data = sheet.get_all_records()
        
        msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
        keyboard = []
        for row in data:
            name, status, price, icon = row['Tên Sản Phẩm'], row['Trạng Thái'], row['Giá Tiền'], row['Icon']
            msg += f"{icon} *{name}*\n└ {status} | Giá: `{price:,}`đ\n\n"
            if "Hết hàng" not in str(status):
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_query_data=f"buy_{name}")])
        
        await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text("❌ Lỗi kết nối dữ liệu Sheet.")

async def handle_buy_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_name = query.data.replace("buy_", "")
    await query.answer()

    # Lấy giá từ Sheet
    sheet = get_db().worksheet("DataBot")
    price = 0
    for row in sheet.get_all_records():
        if row['Tên Sản Phẩm'] == product_name:
            price = int(row['Giá Tiền'])
            break

    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    context.user_data['order'] = {"id": order_id, "name": product_name, "price": price}

    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
    
    caption = (f"💳 **THANH TOÁN ĐƠN HÀNG**\n\n"
               f"📦 Sản phẩm: {product_name}\n"
               f"💰 Số tiền: `{price:,}` VNĐ\n"
               f"📝 Nội dung: `{order_id}`\n\n"
               f"⚠️ *Lưu ý: Chuyển đúng số tiền và nội dung.*")
    
    kb = [[InlineKeyboardButton("✅ Xác nhận đã chuyển khoản", callback_query_data="verify")]]
    await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order = context.user_data.get('order')
    if not order: return

    await query.answer("⌛ Đang kiểm tra giao dịch...", show_alert=False)
    
    # Gọi API SePay kiểm tra tiền
    url = f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}"
    headers = {"Authorization": f"Bearer {SEPAY_API_KEY}"}
    
    try:
        resp = requests.get(url, headers=headers).json()
        found = False
        for tr in resp.get('transactions', []):
            if order['id'] in tr.get('content') and int(float(tr.get('amount'))) >= order['price']:
                found = True
                break
        
        if found:
            # Lấy key từ tab 'acc'
            db = get_db()
            acc_sheet = db.worksheet("acc")
            orders_sheet = db.worksheet("Orders")
            
            data = acc_sheet.get_all_records()
            for i, row in enumerate(data, 2):
                if row['Tên Sản Phẩm'] == order['name'] and row['Trạng Thái'] == "Sẵn sàng":
                    key_val = f"TK: `{row['Tài khoản']}`\nMK: `{row['Mật khẩu']}`"
                    # Cập nhật trạng thái
                    acc_sheet.update_cell(i, 4, "Đã bán")
                    # Ghi lịch sử đơn hàng
                    orders_sheet.append_row([datetime.datetime.now().strftime("%H:%M %d/%m"), order['id'], query.from_user.id, order['name'], order['price'], "Thành công", key_val])
                    
                    await query.message.reply_text(f"🎉 **THANH TOÁN THÀNH CÔNG!**\n\n🎁 Key của bạn:\n{key_val}", parse_mode='Markdown')
                    context.user_data['order'] = None
                    return
            await query.message.reply_text("❌ Hết hàng! Vui lòng liên hệ Admin hoàn tiền.")
        else:
            await query.message.reply_text("❌ Chưa tìm thấy giao dịch. Vui lòng đợi 30s và thử lại.")
    except Exception as e:
        await query.message.reply_text(f"❌ Lỗi hệ thống: {str(e)}")

# --- KHỞI CHẠY BOT ---
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    app_bot = Application.builder().token(token).build()
    
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("nhap", lambda u, c: u.message.reply_text("Gửi theo mẫu:\nNHAP Tên SP\nuser | pass")))
    app_bot.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    app_bot.add_handler(MessageHandler(filters.Text(["📥 Nhập Kho Hàng Loạt"]), lambda u, c: u.message.reply_text("Gửi mẫu: NHAP Tên SP\nuser | pass")))
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.Regex("^NHAP"), admin_nhap))
    app_bot.add_handler(CallbackQueryHandler(handle_buy_click, pattern="^buy_"))
    app_bot.add_handler(CallbackQueryHandler(verify_payment, pattern="verify"))
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))).start()
    app_bot.run_polling()

if __name__ == '__main__':
    main()
