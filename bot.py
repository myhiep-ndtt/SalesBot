import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

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
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.getenv("GOOGLE_SHEETS_JSON")), scope)
        return authorize(creds).open("BotSales")
    except: return None

class StockManager:
    @staticmethod
    def count():
        try:
            db = get_db(); recs = db.worksheet("acc").get_all_records()
            stk = {}
            for r in recs:
                if str(r.get('Trạng Thái')).strip() in ["Sẵn sàng", "Hoạt Động"]:
                    n = str(r.get('Tên Sản Phẩm')).strip()
                    stk[n] = stk.get(n, 0) + 1
            return stk
        except: return {}

    @staticmethod
    def dispense(p_name, new_st):
        try:
            db = get_db(); sh = db.worksheet("acc"); recs = sh.get_all_records()
            for i, r in enumerate(recs, 2):
                if str(r.get('Tên Sản Phẩm')).strip() == p_name and \
                   str(r.get('Trạng Thái')).strip() in ["Sẵn sàng", "Hoạt Động"]:
                    tk = str(r.get('Tài khoản', '')).strip()
                    mk = str(r.get('Mật khẩu', '')).strip()
                    final_data = f"{tk} | {mk}" if mk and mk.upper() != "N/A" else tk
                    sh.update_cell(i, 4, new_st)
                    return final_data
            return None
        except: return None

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt = "👋 **Chào Mừng Tới Với NDTT STORE**\n🛒 Hệ thống tự động 24/7\n💳 Nhận hàng ngay sau khi thanh toán."
    kb = ReplyKeyboardMarkup([["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ"]], resize_keyboard=True)
    await u.message.reply_text(txt, reply_markup=kb, parse_mode='Markdown')

async def huong_dan_mua(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt = "📖 **HƯỚNG DẪN**\n1️⃣ Chọn sản phẩm tại /list\n2️⃣ CK đúng số tiền & nội dung đơn hàng\n3️⃣ Nhận tài khoản sau 30s."
    await u.message.reply_text(txt, parse_mode='Markdown')

async def nhap_kho(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    raw = " ".join(c.args)
    if "|" not in raw: return
    try:
        p_name, data = raw.split("|", 1)
        p_name = p_name.strip()
        
        # Regex xử lý bóc tách từng cụm trong dấu ngoặc kép
        # it[0] sẽ lấy nội dung bao gồm cả dấu ngoặc kép
        items = re.findall(r'("[^"]+")', data.strip())
        
        if items:
            rows = [[p_name, it.strip(), "N/A", "Sẵn sàng", datetime.datetime.now().strftime('%d/%m %H:%M')] for it in items]
            get_db().worksheet("acc").append_rows(rows)
            await u.message.reply_text(f"✅ Đã nạp thành công {len(rows)} tài khoản cho `{p_name}`.")
        else:
            await u.message.reply_text("❌ Không tìm thấy dữ liệu định dạng trong dấu ngoặc kép.")
    except Exception as e:
        await u.message.reply_text(f"❌ Lỗi: {str(e)}")

async def clear_kho(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    p = " ".join(c.args).strip()
    try:
        db = get_db(); sh = db.worksheet("acc"); r = sh.get_all_values()
        nd = [r[0]] + [l for l in r[1:] if not (str(l[0]).strip() == p and str(l[3]).strip() in ["Sẵn sàng", "Hoạt Động"])]
        sh.clear(); sh.update('A1', nd)
        await u.message.reply_text(f"🗑️ Đã dọn sạch kho `{p}`")
    except: pass

async def show_catalog(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        db = get_db(); p_list = db.worksheet("DataBot").get_all_records(); stk = StockManager.count()
        msg = "🚀 **DANH SÁCH DỊCH VỤ**\n\n"; kb = []
        for p in p_list:
            n = str(p.get('Tên Sản Phẩm')).strip(); pr = int(re.sub(r'[^\d]', '', str(p.get('Giá Tiền', 0))))
            qty = stk.get(n, 0)
            msg += f"🔹 **{n}**: `{pr:,}`đ ({'🟢 '+str(qty) if qty > 0 else '🔴 Hết'})\n"
            if qty > 0: kb.append([InlineKeyboardButton(f"Mua {n}", callback_data=f"buy_{n}")])
        if u.callback_query: await u.callback_query.message.edit_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        else: await u.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except: pass

async def handle_buy(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data.startswith("buy_"):
        p = q.data.replace("buy_", ""); db = get_db(); data = db.worksheet("DataBot").get_all_records()
        pr = next((int(re.sub(r'[^\d]', '', str(r['Giá Tiền']))) for r in data if str(r['Tên Sản Phẩm']).strip() == p), 0)
        oid = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        pending_orders[oid] = {"user_id": u.effective_user.id, "product": p, "price": pr}
        qr = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={pr}&addInfo={oid}"
        await q.message.reply_photo(photo=qr, caption=f"💳 **{p}**\n💰 `{pr:,}`đ\n📝 Nội dung: `{oid}`", parse_mode='Markdown')

def worker(oid, info):
    res = StockManager.dispense(info['product'], "Đã bán")
    if res:
        db = get_db()
        db.worksheet("Orders").append_row([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), oid, info['user_id'], info['product'], info['price'], "Success", res])
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", params={"chat_id": info['user_id'], "text": f"🎉 **THÀNH CÔNG**\n🔑 `{res}`", "parse_mode": "Markdown"})

@app.route('/sepay-webhook', methods=['POST'])
def sepay():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}": return jsonify({"s": 401}), 401
    txt = str(request.json.get("content", "")).upper()
    for oid, info in list(pending_orders.items()):
        if oid in txt:
            threading.Thread(target=worker, args=(oid, info)).start()
            del pending_orders[oid]; break
    return jsonify({"s": 200}), 200

async def p_init(app: Application):
    await app.bot.set_my_commands([BotCommand("start", "Start"), BotCommand("list", "Menu"), BotCommand("buy", "Guide"), BotCommand("contact", "Support")])

def main():
    bot = Application.builder().token(TELEGRAM_TOKEN).post_init(p_init).build()
    bot.add_handler(CommandHandler("start", start)); bot.add_handler(CommandHandler("buy", huong_dan_mua))
    bot.add_handler(CommandHandler("nhap", nhap_kho)); bot.add_handler(CommandHandler("clear", clear_kho))
    bot.add_handler(CommandHandler("list", show_catalog))
    bot.add_handler(CommandHandler("contact", lambda u, c: u.message.reply_text("☎️ Admin: @NgDanhThanhTrung")))
    bot.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    bot.add_handler(CallbackQueryHandler(handle_buy))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    bot.run_polling()

if __name__ == '__main__': main()
