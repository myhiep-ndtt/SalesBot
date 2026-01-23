import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH HỆ THỐNG ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID", "")        # Ví dụ: MB, VCB, ICB
BANK_ACC = os.getenv("BANK_ACC", "")      # Số tài khoản nhận tiền
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {} # Lưu đơn hàng đang chờ thanh toán

# --- 2. KẾT NỐI DATABASE (GOOGLE SHEETS) ---
def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_SHEETS_JSON")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        return authorize(creds).open("BotSales")
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheets: {e}")
        return None

# --- 3. CÁC HÀM BỔ TRỢ ---
def name_to_cmd(name):
    """Chuyển 'ChatGPT Plus' -> 'chatgptplus' để làm lệnh /buy"""
    return re.sub(r'[^a-zA-Z0-9]', '', str(name)).lower()

def clean_price(price_val):
    """Xử lý giá tiền dù là số 50000 hay chuỗi '50.000đ'"""
    try:
        if isinstance(price_val, (int, float)): return int(price_val)
        res = re.sub(r'[^\d]', '', str(price_val))
        return int(res) if res else 0
    except: return 0

# --- 4. LOGIC GIAO HÀNG TỰ ĐỘNG ---
def process_delivery(order_id, info):
    try:
        db = get_db()
        acc_sheet = db.worksheet("acc")
        orders_sheet = db.worksheet("Orders")
        records = acc_sheet.get_all_records()
        
        for i, row in enumerate(records, 2): # Bắt đầu từ hàng 2 (sau tiêu đề)
            # So khớp tên sản phẩm và trạng thái 'Sẵn sàng'
            sheet_sp = str(row.get('Tên Sản Phẩm', '')).strip()
            sheet_stt = str(row.get('Trạng Thái', '')).strip()
            
            if sheet_sp == info['product'] and sheet_stt == "Sẵn sàng":
                tk = str(row.get('Tài khoản', '')).strip()
                mk = str(row.get('Mật khẩu', '')).strip()
                
                # Định dạng nội dung giao (Nếu có mật khẩu thì gửi Account|Pass, nếu không thì chỉ gửi link)
                acc_delivery = f"{tk} | {mk}" if mk and mk.lower() != "n/a" else tk
                
                # Cập nhật sheet 'acc' sang 'Đã bán'
                acc_sheet.update_cell(i, 4, "Đã bán")
                
                # Ghi lịch sử vào sheet 'Orders' theo đúng thứ tự ảnh bạn gửi:
                # Thời gian | Mã Đơn | User ID | Tên Sản Phẩm | Số Tiền | Trạng Thái | Key Đã Giao
                orders_sheet.append_row([
                    datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                    order_id,
                    str(info['user_id']),
                    info['product'],
                    info['price'],
                    "Thành công",
                    acc_delivery
                ])
                
                # Gửi tin nhắn nhận hàng cho người dùng
                success_msg = (
                    f"✅ **THANH TOÁN THÀNH CÔNG**\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"📦 SP: **{info['product']}**\n"
                    f"💰 Giá: `{info['price']:,}`đ\n"
                    f"🆔 Mã đơn: `{order_id}`\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🎁 **Thông tin tài khoản/Key:**\n\n"
                    f"`{acc_delivery}`\n\n"
                    f"⚠️ *Vui lòng kiểm tra và đổi mật khẩu nếu cần.*"
                )
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                             params={"chat_id": info['user_id'], "text": success_msg, "parse_mode": "Markdown"})
                print(f"✅ Đã giao đơn {order_id} thành công.")
                return True
        return False
    except Exception as e:
        print(f"❌ Lỗi xử lý giao hàng: {e}")
        return False

# --- 5. XỬ LÝ THANH TOÁN (WEBHOOK SEPAY) ---
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
def home(): return "Bot NDTT Store is online!", 200

# --- 6. TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    btns = [["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ & Hướng dẫn"]]
    if user.id == ADMIN_ID: btns.append(["📥 Nhập Kho Hàng Loạt"])
    
    await update.message.reply_text(
        f"👋 Chào {user.first_name}! Chào mừng bạn đến với **NDTT Premium Store**.\n\n"
        "Hệ thống bán acc tự động 24/7.",
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True),
        parse_mode='Markdown'
    )

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_db().worksheet("DataBot").get_all_records()
        msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
        keyboard = []
        
        for row in data:
            name = str(row['Tên Sản Phẩm'])
            price = clean_price(row['Giá Tiền'])
            icon = str(row.get('Icon', '🔹'))
            status = str(row.get('Trạng Thái', ''))
            cmd = "/buy" + name_to_cmd(name)
            
            msg += f"{icon} *{name}*: `{price:,}`đ\n└ Mua nhanh: {cmd}\n\n"
            
            if "Sẵn sàng" in status or "Còn hàng" in status:
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_query_data=f"buy_{name}")])
        
        await update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Lỗi Catalog: {e}")

async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE, product_name: str):
    data = get_db().worksheet("DataBot").get_all_records()
    selected = next((r for r in data if str(r['Tên Sản Phẩm']) == product_name), None)
    
    if not selected: return

    price = clean_price(selected['Giá Tiền'])
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Lưu vào hàng chờ thanh toán
    pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
    context.user_data['last_id'] = order_id
    
    # Tạo QR thanh toán qua VietQR
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
    
    caption = (
        f"💳 **THANH TOÁN ĐƠN HÀNG**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 Sản phẩm: **{product_name}**\n"
        f"💰 Giá tiền: `{price:,}`đ\n"
        f"📝 Nội dung CK: `{order_id}`\n\n"
        f"⚠️ *Lưu ý: Bạn phải chuyển đúng số tiền và nội dung để hệ thống tự động giao hàng trong 30s.*"
    )
    
    await update.effective_message.reply_photo(
        photo=qr_url, 
        caption=caption, 
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kiểm tra thanh toán", callback_query_data="check_manual")]])
    )

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Xử lý khi bấm nút "Mua"
    query = update.callback_query
    if query and query.data.startswith("buy_"):
        await query.answer()
        await create_order(update, context, query.data.replace("buy_", ""))
        
    # Xử lý nút kiểm tra thủ công
    elif query and query.data == "check_manual":
        order_id = context.user_data.get('last_id')
        info = pending_orders.get(order_id)
        if not info:
            await query.answer("❌ Đơn hàng không tìm thấy hoặc đã quá hạn.", show_alert=True)
            return

        await query.answer("🔄 Đang kiểm tra giao dịch, vui lòng đợi...")
        try:
            resp = requests.get(f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}", 
                                headers={"Authorization": f"Bearer {SEPAY_API_KEY}"}).json()
            for tr in resp.get('transactions', []):
                content = str(tr.get('content', '')).upper()
                amount = int(float(tr.get('amount', 0)))
                if order_id in content and amount >= info['price']:
                    if process_delivery(order_id, info):
                        del pending_orders[order_id]
                        return
            await query.message.reply_text("❌ Hệ thống chưa thấy tiền vào. Nếu bạn đã chuyển, hãy đợi 1 phút rồi bấm lại.")
        except: pass

async def quick_buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Xử lý lệnh dạng /buycapcutpro
    cmd = update.message.text.split('@')[0].lower()
    data = get_db().worksheet("DataBot").get_all_records()
    for row in data:
        if ("/buy" + name_to_cmd(row['Tên Sản Phẩm'])) == cmd:
            await create_order(update, context, row['Tên Sản Phẩm'])
            return

# --- 7. CHẠY BOT ---
def main():
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Đăng ký lệnh menu Telegram
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_app.bot.set_my_commands([
        BotCommand("start", "Khởi động bot"),
        BotCommand("buy", "Xem bảng giá")
    ]))

    # Cài đặt Handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("buy", show_catalog))
    bot_app.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    bot_app.add_handler(MessageHandler(filters.Regex(r"^/buy"), quick_buy_command))
    bot_app.add_handler(CallbackQueryHandler(handle_interaction))
    
    # Khởi chạy Flask Webhook trong một Thread riêng
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    
    print("🚀 Bot đang chạy...")
    bot_app.run_polling()

if __name__ == '__main__':
    main()
