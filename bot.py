import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CẤU HÌNH ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BANK_ID = os.getenv("BANK_ID", "")
BANK_ACC = os.getenv("BANK_ACC", "")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {}

# --- 2. KẾT NỐI DATABASE ---
def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_SHEETS_JSON")
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

# --- 3. LOGIC GIAO HÀNG & LỊCH SỬ ---
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
                       f"🎁 **Thông tin:**\n\n`{acc_info}`\n\n⚠️ *Vui lòng đổi mật khẩu bảo mật.*")
                
                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                             params={"chat_id": info['user_id'], "text": msg, "parse_mode": "Markdown"})
                return True
        return False
    except Exception as e:
        print(f"❌ Lỗi giao hàng: {e}"); return False

# --- 4. WEBHOOK & FLASK ---
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

# --- 5. TELEGRAM HANDLERS (USER) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ & Hướng dẫn"]]
    if update.effective_user.id == ADMIN_ID: btns.append(["📥 Nhập Kho Hàng Loạt"])
    await update.message.reply_text("🛒 **NDTT PREMIUM STORE**\nChọn chức năng bên dưới:", 
                                    reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_db().worksheet("DataBot").get_all_records()
        msg = "📊 **DANH SÁCH SẢN PHẨM**\n\n"
        keyboard = []
        for row in data:
            name, price = str(row['Tên Sản Phẩm']), clean_price(row['Giá Tiền'])
            cmd = "/buy" + name_to_cmd(name)
            msg += f"{row.get('Icon', '🔹')} *{name}*: `{price:,}`đ\n└ Mua nhanh: {cmd}\n\n"
            if "Sẵn sàng" in str(row.get('Trạng Thái', '')):
                keyboard.append([InlineKeyboardButton(f"Mua {name}", callback_query_data=f"buy_{name}")])
        await update.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except: await update.effective_message.reply_text("❌ Lỗi lấy bảng giá.")

async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE, product_name: str):
    data = get_db().worksheet("DataBot").get_all_records()
    selected = next((r for r in data if str(r['Tên Sản Phẩm']) == product_name), None)
    if not selected: return

    price = clean_price(selected['Giá Tiền'])
    order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    pending_orders[order_id] = {"user_id": update.effective_user.id, "product": product_name, "price": price}
    context.user_data['last_id'] = order_id
    
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={price}&addInfo={order_id}"
    await update.effective_message.reply_photo(
        photo=qr_url, 
        caption=f"💳 **THANH TOÁN**\n📦 SP: **{product_name}**\n💰 Giá: `{price:,}`đ\n📝 Nội dung: `{order_id}`", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kiểm tra thanh toán", callback_query_data="check_manual")]])
    )

async def support_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("☎️ **HỖ TRỢ KHÁCH HÀNG**\n\nNếu gặp vấn đề về thanh toán hoặc tài khoản, vui lòng liên hệ Admin: @ID_CUA_BAN")

# --- 6. ADMIN COMMANDS ---
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg: return
    users = get_db().worksheet("Users").col_values(1)[1:]
    count = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **THÔNG BÁO TỪ CỬA HÀNG:**\n\n{msg}", parse_mode='Markdown')
            count += 1
        except: pass
    await update.message.reply_text(f"✅ Đã gửi thông báo đến {count} người dùng.")

async def admin_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        lines = update.message.text.split('\n')
        product = lines[0].replace("NHAP", "").strip()
        new_rows = [[product, l.split('|')[0].strip(), l.split('|')[1].strip(), "Sẵn sàng", "Mới nhập"] for l in lines[1:] if "|" in l]
        get_db().worksheet("acc").append_rows(new_rows)
        await update.message.reply_text(f"✅ Đã nhập {len(new_rows)} tài khoản cho {product}")
    except: await update.message.reply_text("❌ Sai định dạng. Ví dụ:\nNHAP Canva\ntk1|mk1\ntk2|mk2")

# --- 7. HANDLERS ĐỘNG ---
async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("buy_"):
        await query.answer(); await create_order(update, context, query.data.replace("buy_", ""))
    elif query.data == "check_manual":
        order_id = context.user_data.get('last_id')
        info = pending_orders.get(order_id)
        if not info: return await query.answer("❌ Đơn hết hạn.", show_alert=True)
        await query.answer("🔄 Đang kiểm tra...")
        try:
            resp = requests.get(f"https://my.sepay.vn/userapi/transactions/list?account_number={BANK_ACC}", headers={"Authorization": f"Bearer {SEPAY_API_KEY}"}).json()
            for tr in resp.get('transactions', []):
                if order_id in str(tr.get('content', '')).upper() and int(float(tr.get('amount'))) >= info['price']:
                    if process_delivery(order_id, info): del pending_orders[order_id]; return
            await query.message.reply_text("❌ Chưa nhận được tiền. Thử lại sau 1 phút.")
        except: pass

async def quick_buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split('@')[0].lower()
    data = get_db().worksheet("DataBot").get_all_records()
    for row in data:
        if ("/buy" + name_to_cmd(row['Tên Sản Phẩm'])) == cmd:
            await create_order(update, context, row['Tên Sản Phẩm']); return

# --- 8. CHẠY BOT ---
def main():
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Đăng ký menu lệnh chuẩn
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_app.bot.set_my_commands([
        BotCommand("start", "Bắt đầu"), BotCommand("buy", "Bảng giá"), BotCommand("support", "Hỗ trợ")
    ]))

    # Thứ tự Handler (Cực kỳ quan trọng)
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("buy", show_catalog))
    bot_app.add_handler(CommandHandler("support", support_contact))
    bot_app.add_handler(CommandHandler("broadcast", admin_broadcast))
    
    bot_app.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    bot_app.add_handler(MessageHandler(filters.Text(["☎️ Hỗ trợ & Hướng dẫn"]), support_contact))
    bot_app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^NHAP"), admin_import))
    
    # Lệnh mua nhanh động /buy...
    bot_app.add_handler(MessageHandler(filters.COMMAND & filters.Regex(r"^/buy[a-zA-Z0-9]+$"), quick_buy_handler))
    bot_app.add_handler(CallbackQueryHandler(handle_interaction))

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    print("🚀 BOT LIVE!"); bot_app.run_polling()

if __name__ == '__main__': main()
