import os, json, random, string, datetime, threading, requests
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- CẤU HÌNH BIẾN MÔI TRƯỜNG ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID")
BANK_ACC = os.getenv("BANK_ACC")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)

# Kết nối Google Sheets
def get_db():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.getenv("GOOGLE_SHEETS_JSON"))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return authorize(creds).open("BotSales")

# --- WEB SERVICE (Giữ service sống & nhận Webhook) ---
@app.route('/')
def health_check():
    return "Bot Web Service is Online!", 200

@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    # Logic nhận dữ liệu tự động từ SePay (nếu dùng Webhook nâng cao)
    return jsonify({"status": "received"}), 200

# --- CHỨC NĂNG ADMIN: NHẬP KHO HÀNG LOẠT ---
async def admin_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                    u, p = line.split("|")
                    new_rows.append([product_name, u.strip(), p.strip(), "Sẵn sàng", f"Admin nhập {datetime.datetime.now().strftime('%d/%m')}"])
            
            if new_rows:
                acc_sheet.append_rows(new_rows)
                await update.message.reply_text(f"🚀 **THÀNH CÔNG**\nĐã thêm {len(new_rows)} tài khoản cho món: {product_name}")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {str(e)}")

# --- GIAO DIỆN KHÁCH HÀNG ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    buttons = [["📊 Xem Bảng Giá"], ["💳 Hướng dẫn", "☎️ Hỗ trợ"]]
    if user_id == ADMIN_ID:
        buttons.append(["📥 Nhập Kho Hàng Loạt"])
    
    await update.message.reply_text(
        "👋 Chào mừng bạn đến với hệ thống bán hàng tự động 24/7!", 
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_db().worksheet("DataBot")
        data = sheet.get_all_records()
        msg = "📊 **BẢNG GIÁ VÀ TỒN KHO**\n\n"
        keyboard = []
        for row in data:
            name, status, price, icon = row['Tên Sản Phẩm'], row['Trạng Thái'], row['Giá Tiền'], row['Icon']
            msg += f"{icon} *{name}*\n└ {status} | Giá: `{price:,}`đ\n\n"
            if "Hết hàng" not in str(status):
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_query_data=f"buy_{name}")])
        
        await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text("❌ Lỗi tải dữ liệu từ Google Sheets.")

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_name = query.data.replace("buy_", "")
    await query.answer()

    # Lấy giá từ DataBot
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
               f"💰 Giá tiền: `{price:,}` VNĐ\n"
               f"📝 Nội dung CK: `{order_id}`\n\n"
               f"⚠️ *Hệ thống sẽ tự động gửi hàng sau khi nhận được thanh toán.*")
    
    kb = [[InlineKeyboardButton("✅ Tôi đã chuyển khoản", callback_query_data="verify")]]
    await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order = context.user_data.get('order')
    if not order:
        await query.answer("❌ Đơn hàng hết hạn, vui lòng chọn mua lại.", show_alert=True)
        return

    await query.answer("⌛ Đang kiểm tra giao dịch...", show_alert=False)
    
    # Kiểm tra SePay API
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
            db = get_db()
            acc_sheet = db.worksheet("acc")
            orders_sheet = db.worksheet("Orders")
            
            data = acc_sheet.get_all_records()
            for i, row in enumerate(data, 2):
                if row['Tên Sản Phẩm'] == order['name'] and row['Trạng Thái'] == "Sẵn sàng":
                    account_info = f"Tài khoản: `{row['Tài khoản']}`\nMật khẩu: `{row['Mật khẩu']}`"
                    
                    # Đánh dấu đã bán & Ghi lịch sử
                    acc_sheet.update_cell(i, 4, "Đã bán")
                    orders_sheet.append_row([
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 
                        order['id'], query.from_user.id, order['name'], 
                        order['price'], "Thành công", account_info
                    ])
                    
                    await query.message.reply_text(f"🎉 **THANH TOÁN THÀNH CÔNG!**\n\n🎁 Thông tin sản phẩm của bạn:\n{account_info}", parse_mode='Markdown')
                    context.user_data['order'] = None
                    return
            await query.message.reply_text("❌ Món này vừa hết hàng! Vui lòng liên hệ Admin để nhận hàng thủ công hoặc hoàn tiền.")
        else:
            await query.message.reply_text("❌ Hệ thống chưa thấy giao dịch của bạn. Vui lòng thử lại sau 30 giây.")
    except Exception as e:
        await query.message.reply_text(f"❌ Lỗi kỹ thuật: {str(e)}")

# --- KHỞI CHẠY ---
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^NHAP"), admin_import))
    application.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(verify_payment, pattern="verify"))
    
    threading.Thread(target=run_flask, daemon=True).start()
    print(f"Bot Web Service started on port {PORT}")
    application.run_polling()

if __name__ == '__main__':
    main()
