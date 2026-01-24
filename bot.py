import os, json, random, string, datetime, threading, requests, re, asyncio
from flask import Flask, request, jsonify
from gspread import authorize
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) 
BANK_ID = os.getenv("BANK_ID", "")
BANK_ACC = os.getenv("BANK_ACC", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "ndtt_secret")
PORT = int(os.getenv("PORT", "8000"))

app = Flask(__name__)
pending_orders = {}

def keep_alive():
    url = "https://salesbot-xrz9.onrender.com"
    while True:
        try: requests.get(url, timeout=10)
        except: pass
        threading.Event().wait(600)

@app.route('/')
def home(): return "Bot is running", 200

def get_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.getenv("GOOGLE_SHEETS_JSON")), scope)
        return authorize(creds).open("BotSales")
    except Exception as e:
        print(f"Lỗi kết nối Sheet: {e}")
        return None

class StockManager:
    @staticmethod
    def count():
        try:
            db = get_db()
            recs = db.worksheet("acc").get_all_records()
            stk = {}
            for r in recs:
                if str(r.get('Trạng Thái')).strip() in ["Sẵn sàng", "Hoạt Động"]:
                    n = str(r.get('Tên Sản Phẩm')).strip()
                    stk[n] = stk.get(n, 0) + 1
            return stk
        except: return {}

    @staticmethod
    def dispense(p_name, new_st, qty=1):
        try:
            db = get_db()
            sh = db.worksheet("acc")
            recs = sh.get_all_records()
            results = []
            count = 0
            for i, r in enumerate(recs, 2):
                if str(r.get('Tên Sản Phẩm')).strip() == p_name and \
                   str(r.get('Trạng Thái')).strip() in ["Sẵn sàng", "Hoạt Động"]:
                    tk = str(r.get('Tài khoản', '')).strip()
                    mk = str(r.get('Mật khẩu', '')).strip()
                    final_data = f"{tk} | {mk}" if mk and mk.upper() != "N/A" else tk
                    results.append(final_data)
                    sh.update_cell(i, 4, new_st)
                    count += 1
                    if count == qty: break
            return "\n".join(results) if results else None
        except: return None

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt = (
        "👋 **Kính chào Quý khách đến với NDTT STORE!**\n\n"
        "✨ Chúng tôi chuyên cung cấp các giải pháp tài khoản Premium tự động, uy tín hàng đầu.\n\n"
        "🚀 **Hướng dẫn:**\n"
        "• Bấm **📊 Xem Bảng Giá** để chọn sản phẩm.\n"
        "• Bấm **☎️ Hỗ trợ** nếu cần tư vấn trực tiếp.\n\n"
        "Chúc Quý khách một ngày mua sắm tuyệt vời!"
    )
    kb = ReplyKeyboardMarkup([["📊 Xem Bảng Giá"], ["☎️ Hỗ trợ"]], resize_keyboard=True)
    await u.message.reply_text(txt, reply_markup=kb, parse_mode='Markdown')

async def show_catalog(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        db = get_db()
        p_list = db.worksheet("DataBot").get_all_records()
        stk = StockManager.count()
        msg = "🎁 **DANH SÁCH SẢN PHẨM PREMIUM**\n\n"
        kb = []
        for p in p_list:
            n = str(p.get('Tên Sản Phẩm')).strip()
            pr = int(re.sub(r'[^\d]', '', str(p.get('Giá Tiền', 0))))
            qty = stk.get(n, 0)
            status = f"🟢 Còn {qty}" if qty > 0 else "🔴 Hết hàng"
            msg += f"🔹 **{n}**\n    └ Giá: `{pr:,}`đ — {status}\n\n"
            if qty > 0:
                kb.append([InlineKeyboardButton(f"💳 Đăng ký mua {n}", callback_data=f"ask_{n}")])
        m = InlineKeyboardMarkup(kb)
        if u.callback_query: await u.callback_query.message.edit_text(msg, parse_mode='Markdown', reply_markup=m)
        else: await u.effective_message.reply_text(msg, parse_mode='Markdown', reply_markup=m)
    except:
        await u.effective_message.reply_text("❌ Hệ thống đang bảo trì, Quý khách vui lòng thử lại sau.")

async def handle_ask_qty(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    p_name = q.data.replace("ask_", "")
    stk_count = StockManager.count().get(p_name, 0)
    kb = []
    row = [InlineKeyboardButton(f"Mua {i}", callback_data=f"buy_{p_name}_{i}") for i in [1, 2, 5, 10] if i <= stk_count]
    if not row and stk_count > 0:
        row = [InlineKeyboardButton(f"Mua {stk_count}", callback_data=f"buy_{p_name}_{stk_count}")]
    kb.append(row)
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="back_catalog")])
    await q.message.edit_text(f"🔢 Quý khách muốn mua bao nhiêu **{p_name}**?\n(Hiện còn: {stk_count} sản phẩm)", 
                              reply_markup=InlineKeyboardMarkup(kb))

async def handle_buy(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    data_parts = q.data.split("_")
    p, qty = data_parts[1], int(data_parts[2])
    db = get_db()
    data = db.worksheet("DataBot").get_all_records()
    pr_single = next((int(re.sub(r'[^\d]', '', str(r['Giá Tiền']))) for r in data if str(r['Tên Sản Phẩm']).strip() == p), 0)
    total_pr = pr_single * qty
    oid = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    pending_orders[oid] = {"user_id": u.effective_user.id, "product": p, "price": total_pr, "qty": qty}
    qr = f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACC}-compact2.png?amount={total_pr}&addInfo={oid}"
    caption = (
        f"✨ **THÔNG TIN THANH TOÁN**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Sản phẩm: `{p}`\n"
        f"🔢 Số lượng: `{qty}`\n"
        f"💰 Tổng tiền: `{total_pr:,}`đ\n"
        f"📝 Nội dung CK: `{oid}`\n\n"
        f"⚠️ **Lưu ý:** Quý khách vui lòng chuyển đúng số tiền và nội dung để hệ thống tự động giao hàng."
    )
    await q.message.reply_photo(photo=qr, caption=caption, parse_mode='Markdown')

async def broadcast(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    msg_text = " ".join(c.args)
    if not msg_text:
        await u.message.reply_text("❌ Cú pháp: `/bc [Nội dung]`")
        return
    db = get_db()
    order_data = db.worksheet("Orders").get_all_values()[1:]
    uids = list(set([r[2] for r in order_data if r[2]]))
    success = 0
    for uid in uids:
        try:
            await c.bot.send_message(chat_id=uid, text=f"📢 **THÔNG BÁO MỚI**\n\n{msg_text}", parse_mode='Markdown')
            success += 1
            await asyncio.sleep(0.05)
        except: continue
    await u.message.reply_text(f"✅ Đã gửi thông báo tới {success} khách hàng.")

async def lenh_trung(u: Update, c: ContextTypes.DEFAULT_TYPE):
    res = StockManager.dispense("CapCut Pro", "Đã tặng (Lệnh /trung)", 1)
    
    if res:
        msg = (
            "🎁 **QUÀ TẶNG ĐẶC BIỆT**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Sản phẩm: **CapCut Pro**\n"
            f"🔑 Tài khoản: `{res}`\n\n"
            "✨ Chúc em tạo ra những video triệu view nhé!"
        )
        await u.message.reply_text(msg, parse_mode='Markdown')
    else:
        await u.message.reply_text("😢 Rất tiếc, kho quà tặng CapCut hiện đã hết. Hãy quay lại sau nhé!")
        
async def nhap_kho(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    raw = " ".join(c.args)
    if "|" not in raw: return
    try:
        p_name, data = raw.split("|", 1)
        p_name = p_name.strip()
        items = re.findall(r'"([^"]+)"', data.strip())
        if items:
            rows = [[p_name, it.strip(), "N/A", "Sẵn sàng", datetime.datetime.now().strftime('%d/%m %H:%M')] for it in items]
            get_db().worksheet("acc").append_rows(rows)
            await u.message.reply_text(f"✅ **Admin:** Đã nạp thành công **{len(rows)}** tài khoản `{p_name}` vào kho.")
    except Exception as e: await u.message.reply_text(f"❌ Lỗi: {e}")

async def clear_kho(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    p_name = " ".join(c.args).strip()
    try:
        db = get_db()
        sh = db.worksheet("acc")
        data = sh.get_all_values()
        new_rows = [data[0]] + [r for r in data[1:] if not (str(r[0]).strip() == p_name and str(r[3]).strip() in ["Sẵn sàng", "Hoạt Động"])]
        sh.clear(); sh.update('A1', new_rows)
        await u.message.reply_text(f"🗑️ **Hệ thống:** Đã dọn sạch các mục khả dụng của `{p_name}`.")
    except: pass

async def clear_bin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    try:
        db = get_db()
        sh = db.worksheet("acc")
        data = sh.get_all_values()
        new_rows = [data[0]] + [r for r in data[1:] if str(r[3]).strip() != "Đã bán"]
        removed = len(data) - len(new_rows)
        sh.clear(); sh.update('A1', new_rows)
        await u.message.reply_text(f"🧹 **Hệ thống:** Đã xóa bỏ hoàn toàn **{removed}** tài khoản đã bán.")
    except: pass

async def handle_report(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer("Đã gửi báo cáo lỗi tới Admin!", show_alert=True)
    oid = q.data.replace("report_", "")
    user = u.effective_user
    db = get_db()
    order = next((r for r in db.worksheet("Orders").get_all_records() if str(r.get('Mã Đơn')) == oid), None)
    if order:
        admin_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Đổi tài khoản mới", callback_data=f"replace_{oid}_{user.id}")]])
        await c.bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"🚨 **BÁO LỖI ĐƠN HÀNG**\n\n📝 Mã Đơn: `{oid}`\n📦 SP: {order.get('Tên Sản Phẩm')}\n👤 Khách: @{user.username}\n🔑 Key lỗi: `{order.get('Key Đã Giao')}`", 
            reply_markup=admin_kb
        )

async def handle_replace(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    _, oid, uid = q.data.split("_")
    db = get_db()
    order_sh = db.worksheet("Orders")
    order = next((r for r in order_sh.get_all_records() if str(r.get('Mã Đơn')) == oid), None)
    if not order: return

    acc_sh = db.worksheet("acc")
    acc_data = acc_sh.get_all_values()
    for i, row in enumerate(acc_data, 1):
        full_acc = f"{row[1]} | {row[2]}" if row[2] != "N/A" else row[1]
        if row[0] == order.get('Tên Sản Phẩm') and full_acc.strip() == str(order.get('Key Đã Giao')).strip():
            acc_sh.update_cell(i, 4, "Lỗi/Bảo hành"); break
            
    new_res = StockManager.dispense(order.get('Tên Sản Phẩm'), "Đã bán (Bảo hành)", 1)
    if new_res:
        await c.bot.send_message(chat_id=uid, text=f"🎁 **BẢO HÀNH THÀNH CÔNG**\n\n📦 SP: {order.get('Tên Sản Phẩm')}\n🔑 Key mới: `{new_res}`")
        await q.edit_message_text(q.message.text + f"\n\n✅ Đã đổi: `{new_res}`")
    else: await q.message.reply_text("❌ Kho đã hết hàng để đổi!")

def worker(oid, info):
    res = StockManager.dispense(info['product'], "Đã bán", info.get('qty', 1))
    if res:
        db = get_db()
        db.worksheet("Orders").append_row([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 
            oid, info['user_id'], info['product'], info['price'], "Success", res
        ])
        msg = (
            f"🎉 **GIAO HÀNG THÀNH CÔNG!**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 Sản phẩm: **{info['product']}**\n"
            f"🔢 Số lượng: **{info.get('qty', 1)}**\n"
            f"🔑 Thông tin truy cập:\n`{res}`\n\n"
            f"🙏 Trân trọng cảm ơn Quý khách đã tin dùng sản phẩm của chúng tôi!"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚩 Báo cáo lỗi", callback_data=f"report_{oid}")]])
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                     params={"chat_id": info['user_id'], "text": msg, "parse_mode": "Markdown", "reply_markup": json.dumps(kb.to_dict())})

@app.route('/sepay-webhook', methods=['POST'])
def sepay():
    if request.headers.get("Authorization") != f"Apikey {WEBHOOK_SECRET}": return jsonify({"s": 401}), 401
    txt = str(request.json.get("content", "")).upper()
    for oid, info in list(pending_orders.items()):
        if oid in txt:
            threading.Thread(target=worker, args=(oid, info)).start()
            del pending_orders[oid]; break
    return jsonify({"s": 200}), 200

async def post_init(application: Application):
    user_cmds = [
        BotCommand("start", "🏠 Khởi động"),
        BotCommand("list", "📊 Bảng giá dịch vụ"),
        BotCommand("contact", "☎️ Liên hệ hỗ trợ")
    ]
    admin_cmds = user_cmds + [
        BotCommand("bc", "📢 Thông báo hàng loạt"),
        BotCommand("nhap", "➕ Nạp hàng"),
        BotCommand("clear", "🗑️ Xóa kho SP"),
        BotCommand("clearbin", "🧹 Dọn rác đã bán")
    ]
    await application.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    if ADMIN_ID != 0:
        await application.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

def main():
    threading.Thread(target=keep_alive, daemon=True).start()
    bot = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("bc", broadcast))
    bot.add_handler(CommandHandler("nhap", nhap_kho))
    bot.add_handler(CommandHandler("clear", clear_kho))
    bot.add_handler(CommandHandler("trung", lenh_trung))
    bot.add_handler(CommandHandler("clearbin", clear_bin))
    bot.add_handler(CommandHandler("list", show_catalog))
    bot.add_handler(CommandHandler("contact", lambda u, c: u.message.reply_text("✉️ Liên hệ Admin: @NgDanhThanhTrung")))
    bot.add_handler(MessageHandler(filters.Text(["📊 Xem Bảng Giá"]), show_catalog))
    bot.add_handler(MessageHandler(filters.Text(["☎️ Hỗ trợ"]), lambda u, c: u.message.reply_text("✉️ Liên hệ Admin: @NgDanhThanhTrung")))
    bot.add_handler(CallbackQueryHandler(handle_ask_qty, pattern="^ask_"))
    bot.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    bot.add_handler(CallbackQueryHandler(show_catalog, pattern="^back_catalog$"))
    bot.add_handler(CallbackQueryHandler(handle_report, pattern="^report_"))
    bot.add_handler(CallbackQueryHandler(handle_replace, pattern="^replace_"))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    bot.run_polling()

if __name__ == '__main__': main()
