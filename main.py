import os
import time
import html
import ccxt
import sqlite3
import datetime
import threading
import base64
import secrets
from collections import deque
import urllib.request
import urllib.error
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

# =====================================================================
# 0. الإعدادات (من متغيرات البيئة)
# =====================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip().strip('"').strip("'")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "").strip().strip('"').strip("'")
BINANCE_ENV = os.getenv("BINANCE_ENV", "demo").strip().lower()  # demo | testnet | live
# حماية لوحة التحكم بكلمة مرور (HTTP Basic). إن تُركت DASHBOARD_PASSWORD فارغة فلا حماية (مناسب عند ربط اللوحة على localhost فقط)
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()

# حجم كل صفقة بالدولار (USDT)
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "100"))

# تنفيذ الأوامر الفعلية:
#  - demo/testnet: مفعّل افتراضياً (TRADING_ENABLED=false لجعله محاكاة فقط)
#  - live: لا يُنفَّذ أي أمر إلا إذا وضعت CONFIRM_LIVE_TRADING=yes صراحةً
if BINANCE_ENV == "live":
    TRADING_ENABLED = os.getenv("CONFIRM_LIVE_TRADING", "").strip().lower() == "yes"
else:
    TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").strip().lower() == "true"

# --- معاملات الاستراتيجية (مطابقة لـ backtest.py) ---
TIMEFRAME = os.getenv("TIMEFRAME", "1h").strip()          # 1h افتراضياً (مثل backtest.py)، جرّب 15m لإشارات أكثر
CANDLES_LIMIT = 500
RSI_MAX_ENTRY = float(os.getenv("RSI_MAX_ENTRY", "70"))
EMA_PERIOD = int(os.getenv("EMA_PERIOD", "50"))
REQUIRE_MACD = os.getenv("REQUIRE_MACD", "true").strip().lower() == "true"
TAKE_PROFIT_PCT = 1.0      # هدف الربح %
INITIAL_SL_PCT = 0.5       # وقف الخسارة الأولي %
TRAILING_SL_PCT = 0.3      # وقف الخسارة المتحرك %

# --- نوع السوق: spot (افتراضي) أو futures (عقود USDⓈ-M الدائمة للعملات الرقمية) ---
MARKET_TYPE = os.getenv("MARKET_TYPE", "spot").strip().lower()
IS_FUTURES = MARKET_TYPE in ("futures", "future", "swap", "usdm")
MARKET_TYPE = "futures" if IS_FUTURES else "spot"
LEVERAGE = max(1, int(float(os.getenv("LEVERAGE", "1") or 1)))      # يُطبَّق على الفيوتشرز فقط
MARGIN_MODE = os.getenv("MARGIN_MODE", "isolated").strip().lower()  # isolated | cross


def _normalize_symbol(raw):
    """يقبل BTCUSDT أو BTC/USDT أو BTC/USDT:USDT ويحوّلها لصيغة ccxt المناسبة لنوع السوق"""
    s = raw.strip().upper()
    if not s:
        return None
    if "/" in s:
        base, quote = s.split("/", 1)
        quote = quote.split(":")[0]
    elif s.endswith("USDT"):
        base, quote = s[:-4], "USDT"
    else:
        base, quote = s, "USDT"
    return "{}/{}:{}".format(base, quote, quote) if IS_FUTURES else "{}/{}".format(base, quote)


_default_symbols = "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT" if IS_FUTURES else "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT"
# أقصى عدد صفقات مفتوحة في نفس الوقت عبر كل العملات (حماية عند تعدد العملات)
MAX_OPEN_POSITIONS = max(1, int(float(os.getenv("MAX_OPEN_POSITIONS", "3") or 3)))
# شكل لوحة التحكم: lime (فسفورية ليمونية) أو neon (بنفسجية متوهجة)
DASHBOARD_THEME = os.getenv("DASHBOARD_THEME", "lime").strip().lower()
SYMBOLS = [x for x in (_normalize_symbol(p) for p in os.getenv("SYMBOLS", _default_symbols).split(",")) if x]


def disp(symbol):
    """اسم العرض بدون لاحقة :USDT"""
    return symbol.split(":")[0]

# صفقة اختبار واحدة: تُفتح فوراً عند التشغيل وتُغلق تلقائياً بعد دقيقتين (للتأكد من عمل الأوامر)
FORCE_TEST_TRADE = os.getenv("FORCE_TEST_TRADE", "false").strip().lower() == "true"
TEST_TRADE_SECONDS = 120
test_trade_done = False

# --- فلتر الأخبار: يوقف الشراء الجديد فقط عند أخبار سلبية قوية (لا يغيّر الاستراتيجية ولا يغلق صفقات مفتوحة) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"').strip("'")
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()   # فارغ = يكتشف البوت نموذجاً متاحاً لحسابك تلقائياً
NEWS_FILTER = os.getenv("NEWS_FILTER", "true").strip().lower() == "true"
NEWS_PAUSE_HOURS = float(os.getenv("NEWS_PAUSE_HOURS", "3"))     # مدة إيقاف الشراء بعد آخر خبر سلبي قوي
NEWS_WINDOW_HOURS = float(os.getenv("NEWS_WINDOW_HOURS", "6"))   # نافذة العناوين المعتبرة
NEWS_BLOCK_SEVERITY = int(os.getenv("NEWS_BLOCK_SEVERITY", "4")) # الخطورة (1-5) التي تُوقف الشراء
NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
]
NEWS_ASSETS = ", ".join(sorted({disp(x).split("/")[0] for x in SYMBOLS})) or "BTC and ETH"
NEWS_ROLE = "crypto market risk analyst"
NEWS_SEVERE = (
    "Use severity 4-5 ONLY for major market-wide negative events such as exchange hacks, major country bans, "
    "large lawsuits or enforcement actions, stablecoin depegs, insolvencies, or protocol exploits affecting major coins. "
    "Ordinary price drops, opinions, predictions and minor news are severity 1-2."
)
_feeds_env = os.getenv("NEWS_FEEDS", "").strip()     # صيغة: الاسم|الرابط;الاسم|الرابط
if _feeds_env:
    NEWS_FEEDS = [tuple(x.split("|", 1)) for x in _feeds_env.split(";") if "|" in x]

print("🔍 [DEBUG CHECK] API Key Length:", len(BINANCE_API_KEY))
print("🔍 [DEBUG CHECK] API Key Prefix:", BINANCE_API_KEY[:6] if BINANCE_API_KEY else "EMPTY")
print("🔍 [DEBUG CHECK] Secret Key Length:", len(BINANCE_SECRET_KEY))
print("🌐 [ENV] Binance environment:", BINANCE_ENV)
print("📐 [STRATEGY] Timeframe: {} | EMA{} | RSI<={} | MACD required: {}".format(TIMEFRAME, EMA_PERIOD, RSI_MAX_ENTRY, REQUIRE_MACD))
print("📰 [NEWS] Filter: {} | Classifier: {} | Pause: {}h".format(
    "ON" if NEWS_FILTER else "OFF", "Groq ({})".format(GROQ_MODEL or "auto model") if GROQ_API_KEY else "Keywords (no GROQ_API_KEY)", NEWS_PAUSE_HOURS))
print("🔐 [DASHBOARD] Password protection: {}".format("ON" if DASHBOARD_PASSWORD else "OFF (bind to localhost only)"))
print("🪙 [COINS] max open positions: {} | theme: {}".format(MAX_OPEN_POSITIONS, DASHBOARD_THEME))
print("🛒 [TRADING] Order execution:", "ENABLED" if TRADING_ENABLED else "SIMULATION ONLY", "| Amount per trade: {} USDT".format(TRADE_AMOUNT_USDT))


def send_telegram_message(message: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("Telegram Error: {}".format(e))


# =====================================================================
# 1. قاعدة البيانات
# =====================================================================
DB_NAME = os.getenv("DB_PATH", "data/trading_bot.db")
_db_dir = os.path.dirname(DB_NAME)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)


def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        # الصفقات المفتوحة تُحفظ هنا كي لا تضيع عند إعادة تشغيل الحاوية
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL NOT NULL,
                amount REAL NOT NULL,
                cost REAL NOT NULL,
                highest_price REAL NOT NULL,
                opened_at TEXT NOT NULL,
                simulated INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # لقطات قيمة المحفظة (للرسم البياني في لوحة التحكم)
        cursor.execute('CREATE TABLE IF NOT EXISTS equity (ts INTEGER PRIMARY KEY, value REAL NOT NULL)')
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN pnl_usdt REAL')
        except sqlite3.OperationalError:
            pass  # العمود موجود مسبقاً
        conn.commit()
        conn.close()
    except Exception as e:
        print("Database Init Error: {}".format(e))


def log_trade(symbol, entry_price, exit_price, pnl_pct, status, pnl_usdt=None):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO trades (symbol, entry_price, exit_price, pnl_pct, status, timestamp, pnl_usdt)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, entry_price, exit_price, pnl_pct, status, now_str, pnl_usdt))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Log Trade Error: {}".format(e))


def get_recent_trades(limit=15):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, symbol, entry_price, exit_price, pnl_pct, status, timestamp,
                   COALESCE(pnl_usdt, pnl_pct / 100.0 * ?)
            FROM trades ORDER BY id DESC LIMIT ?
        ''', (TRADE_AMOUNT_USDT, limit))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print("Get Trades Error: {}".format(e))
        return []


def get_performance_stats():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*), SUM(CASE WHEN status="WIN" THEN 1 ELSE 0 END), SUM(pnl_pct), MAX(pnl_pct), MIN(pnl_pct) FROM trades')
        row = cursor.fetchone()
        conn.close()
        if row and row[0] > 0:
            total = row[0]
            wins = row[1] or 0
            losses = total - wins
            win_rate = (wins / total) * 100
            total_pnl = row[2] or 0.0
            best = row[3] or 0.0
            worst = row[4] or 0.0
            return total, wins, losses, win_rate, total_pnl, best, worst
        return 0, 0, 0, 0.0, 0.0, 0.0, 0.0
    except Exception as e:
        print("Stats Error: {}".format(e))
        return 0, 0, 0, 0.0, 0.0, 0.0, 0.0


def get_realized_stats():
    """إجمالي الربح/الخسارة المحققة بالدولار (الكلي + اليوم)"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        expr = "COALESCE(pnl_usdt, pnl_pct / 100.0 * ?)"
        cursor.execute("SELECT COALESCE(SUM({}), 0) FROM trades".format(expr), (TRADE_AMOUNT_USDT,))
        total = cursor.fetchone()[0] or 0.0
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT COALESCE(SUM({}), 0) FROM trades WHERE timestamp LIKE ?".format(expr),
                       (TRADE_AMOUNT_USDT, today + "%"))
        today_val = cursor.fetchone()[0] or 0.0
        conn.close()
        return float(total), float(today_val)
    except Exception as e:
        print("Realized Stats Error: {}".format(e))
        return 0.0, 0.0


EQUITY_RANGES = {'1h': 3600, '8h': 8 * 3600, '1d': 86400, '1w': 7 * 86400, '1m': 30 * 86400, 'all': None}


def record_equity():
    """حفظ قيمة المحفظة الحالية (تُستدعى مع كل فحص)"""
    try:
        _, total = get_binance_balance()
        if total and total > 0:
            conn = sqlite3.connect(DB_NAME)
            conn.execute('INSERT OR REPLACE INTO equity (ts, value) VALUES (?, ?)', (int(time.time()), float(total)))
            conn.commit()
            conn.close()
    except Exception as e:
        print("Record Equity Error: {}".format(e))


def get_equity_series(range_key="1d", max_points=300):
    try:
        seconds = EQUITY_RANGES.get(range_key, 86400)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if seconds:
            cursor.execute('SELECT ts, value FROM equity WHERE ts >= ? ORDER BY ts', (int(time.time()) - seconds,))
        else:
            cursor.execute('SELECT ts, value FROM equity ORDER BY ts')
        rows = cursor.fetchall()
        conn.close()
        if len(rows) > max_points:
            step = len(rows) / float(max_points)
            rows = [rows[int(i * step)] for i in range(max_points)] + [rows[-1]]
        return [{'t': r[0], 'v': r[1]} for r in rows]
    except Exception as e:
        print("Equity Series Error: {}".format(e))
        return []


def save_position(symbol, pos):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute('''
            INSERT OR REPLACE INTO open_positions
            (symbol, entry_price, amount, cost, highest_price, opened_at, simulated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, pos['entry_price'], pos['amount'], pos['cost'],
              pos['highest_price'], pos['time'], 1 if pos.get('simulated') else 0))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Save Position Error: {}".format(e))


def delete_position(symbol):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute('DELETE FROM open_positions WHERE symbol = ?', (symbol,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Delete Position Error: {}".format(e))


def load_positions():
    result = {}
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT symbol, entry_price, amount, cost, highest_price, opened_at, simulated FROM open_positions')
        for r in cursor.fetchall():
            result[r[0]] = {
                'entry_price': r[1], 'amount': r[2], 'cost': r[3],
                'highest_price': r[4], 'time': r[5], 'simulated': bool(r[6])
            }
        conn.close()
    except Exception as e:
        print("Load Positions Error: {}".format(e))
    return result


# =====================================================================
# 2. الاتصال بـ Binance (demo / testnet / live)
# =====================================================================
if IS_FUTURES:
    # عقود USDⓈ-M الدائمة
    exchange = ccxt.binanceusdm({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET_KEY,
        'enableRateLimit': True,
        'options': {'adjustForTimeDifference': True}
    })
else:
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET_KEY,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
            'adjustForTimeDifference': True
        }
    })
# مصدر بيانات عام (بدون مفاتيح) من Binance الحقيقية: يُستخدم فقط للرموز غير المتاحة في بيئة Demo
# (مثلاً عملة غير مدرجة في Demo). الأوامر لا تُرسل إليه أبداً، فتبقى كل العمليات في وضع Demo/محاكاة.
data_exchange = None
SIM_ONLY = set()
if IS_FUTURES and BINANCE_ENV != "live":
    try:
        data_exchange = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'adjustForTimeDifference': True}})
    except Exception as e:
        print("Data Exchange Error: {}".format(e))


def md(symbol):
    """مصدر بيانات السوق للرمز: Binance الحقيقية (عام) إن كان الرمز غير موجود في Demo، وإلا بيئة Demo"""
    return data_exchange if (symbol in SIM_ONLY and data_exchange is not None) else exchange


print("🧭 [MARKET] {} | symbols: {}{}".format(
    MARKET_TYPE.upper(), ", ".join(SYMBOLS), " | leverage x{} | {}".format(LEVERAGE, MARGIN_MODE) if IS_FUTURES else ""))

if BINANCE_ENV == "demo":
    if hasattr(exchange, "enable_demo_trading"):
        exchange.enable_demo_trading(True)
    else:
        print("❌ [ENV] إصدار ccxt قديم ولا يدعم enable_demo_trading. حدّث ccxt: pip install -U ccxt")
elif BINANCE_ENV == "testnet":
    exchange.set_sandbox_mode(True)
elif BINANCE_ENV == "live":
    print("⚠️ [ENV] الاتصال بالحساب الحقيقي! الأوامر {}".format("مفعّلة" if TRADING_ENABLED else "معطّلة (محاكاة)"))
else:
    print("❌ [ENV] قيمة BINANCE_ENV غير معروفة: {} (استخدم demo أو testnet أو live)".format(BINANCE_ENV))

init_db()
active_positions = load_positions()
market_snapshot = {}
buy_err_seen = {}           # آخر خطأ شراء لكل رمز (لتجنب تكرار الإشعارات)
last_entry_candle = {}   # آخر شمعة تمت محاولة الدخول عليها لكل عملة (لمنع تكرار الدخول على نفس الشمعة)
scan_lock = threading.Lock()
event_log = deque(maxlen=40)   # آخر الأحداث المهمة لعرضها في لوحة التحكم


def log_event(tag, text):
    event_log.appendleft({'t': datetime.datetime.now().strftime("%H:%M:%S"), 'tag': tag, 'text': text})
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        exchange.load_markets()
        validate_symbols()
    except Exception as e:
        print("Load Markets Error: {}".format(e))
    scheduler.add_job(automated_market_scan, 'interval', minutes=1, max_instances=1, coalesce=True)
    if NEWS_FILTER:
        scheduler.add_job(check_news, 'interval', minutes=60, max_instances=1, coalesce=True,
                          next_run_time=datetime.datetime.now())
    scheduler.start()
    log_event("START", "البوت يعمل ({} | {} | EMA{})".format(BINANCE_ENV, TIMEFRAME, EMA_PERIOD))
    print("🚀 [SCHEDULER] تم تفعيل الجدولة الآلية (البيئة: {})".format(BINANCE_ENV))
    if active_positions:
        print("♻️ [RESTORE] تم استرجاع {} صفقة مفتوحة من قاعدة البيانات".format(len(active_positions)))
    send_telegram_message("🤖 <b>تم تشغيل بوت التداول ({} | {})</b>\n🛒 تنفيذ الأوامر: {}\n💵 حجم الصفقة: {} USDT{}\n🪙 الرموز: {}".format(
        BINANCE_ENV, MARKET_TYPE, "فعلي" if TRADING_ENABLED else "محاكاة", TRADE_AMOUNT_USDT,
        " (رافعة x{})".format(LEVERAGE) if IS_FUTURES else "", ", ".join(disp(x) for x in SYMBOLS)))
    yield
    scheduler.shutdown()


app = FastAPI(title="Crypto Trading Bot Spot Dashboard", lifespan=lifespan)


@app.middleware("http")
async def dashboard_auth(request, call_next):
    """يطلب اسم مستخدم وكلمة مرور لكل المسارات إذا ضُبطت DASHBOARD_PASSWORD"""
    if not DASHBOARD_PASSWORD:
        return await call_next(request)
    ok = False
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            ok = secrets.compare_digest(user, DASHBOARD_USER) and secrets.compare_digest(pwd, DASHBOARD_PASSWORD)
        except Exception:
            ok = False
    if not ok:
        return Response(content="Unauthorized", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Trading Bot"'})
    return await call_next(request)



_balance_cache = {'t': 0.0, 'v': (0.0, 0.0)}


def get_binance_balance(ttl=10):
    now = time.time()
    if now - _balance_cache['t'] < ttl:
        return _balance_cache['v']
    value = _get_binance_balance_uncached()
    if value != (0.0, 0.0):
        _balance_cache['t'] = now
        _balance_cache['v'] = value
    return value


def _get_binance_balance_uncached():
    try:
        balance = exchange.fetch_balance()
        if IS_FUTURES:
            info = balance.get('info') or {}
            free = float(info.get('availableBalance') or balance.get('free', {}).get('USDT', 0.0) or 0.0)
            total = float(info.get('totalMarginBalance') or info.get('totalWalletBalance')
                          or balance.get('total', {}).get('USDT', 0.0) or 0.0)
            return free, total
        free_usdt = float(balance.get('free', {}).get('USDT', 0.0) or 0.0)
        free_usdc = float(balance.get('free', {}).get('USDC', 0.0) or 0.0)
        total_usdt = float(balance.get('total', {}).get('USDT', 0.0) or 0.0) + float(balance.get('total', {}).get('USDC', 0.0) or 0.0)

        if 'info' in balance and 'balances' in balance['info']:
            total_usdt = 0.0
            for b in balance['info']['balances']:
                asset = b.get('asset')
                free_val = float(b.get('free', 0.0))
                locked_val = float(b.get('locked', 0.0))
                if asset == 'USDT':
                    free_usdt = free_val
                    total_usdt += free_val + locked_val
                elif asset == 'USDC':
                    free_usdc = free_val
                    total_usdt += free_val + locked_val

        # أضف قيمة العملات المحتفظ بها في الصفقات المفتوحة الفعلية
        for sym, pos in active_positions.items():
            if pos.get('simulated'):
                continue
            price = market_snapshot.get(sym, {}).get('price')
            if isinstance(price, (int, float)):
                total_usdt += pos['amount'] * price

        available_total = free_usdt if free_usdt > 0 else free_usdc
        return available_total, total_usdt if total_usdt > 0 else available_total
    except Exception as e:
        print("Balance Fetch Error details: {}".format(e))
        return 0.0, 0.0


# =====================================================================
# 3. المؤشرات الفنية (نفس دوال backtest.py)
# =====================================================================
def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_ema(closes, period=50):
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calculate_macd(closes):
    if len(closes) < 35:
        return 0, 0, 0

    def get_ema_list(data, period):
        k = 2 / (period + 1)
        ema_list = [data[0]]
        for price in data[1:]:
            ema_list.append((price * k) + (ema_list[-1] * (1 - k)))
        return ema_list

    fast_list = get_ema_list(closes, 12)
    slow_list = get_ema_list(closes, 26)
    macd_line = [f - s for f, s in zip(fast_list, slow_list)]
    signal_list = get_ema_list(macd_line, 9)
    return macd_line[-1], signal_list[-1], macd_line[-1] - signal_list[-1]


# =====================================================================
# 4. تنفيذ الأوامر (شراء / بيع)
# =====================================================================
def _fees_in(order, currency):
    total = 0.0
    fees = order.get('fees') or ([order['fee']] if order.get('fee') else [])
    for f in fees:
        if f and f.get('currency') == currency and f.get('cost'):
            total += float(f['cost'])
    return total


def _complete_order(order, symbol):
    """تأكد أن الأمر نُفّذ بالكامل وفيه بيانات التنفيذ."""
    if order.get('status') != 'closed' or not order.get('cost'):
        time.sleep(1)
        order = exchange.fetch_order(order['id'], symbol)
    return order


def _base_quote(symbol):
    try:
        m = exchange.market(symbol)
        return m['base'], m['quote']
    except Exception:
        base, quote = symbol.split('/')
        return base, quote.split(':')[0]


def _futures_free():
    info = exchange.fetch_balance().get('info') or {}
    return float(info.get('availableBalance') or 0.0)


_futures_ready = set()


def _prepare_futures_symbol(symbol):
    """يضبط نمط الهامش والرافعة مرة واحدة لكل رمز"""
    if symbol in _futures_ready:
        return
    try:
        exchange.set_margin_mode(MARGIN_MODE, symbol)
    except Exception as e:
        if 'no need to change' not in str(e).lower() and '-4046' not in str(e):
            print("⚠️ [FUTURES] تعذر ضبط نمط الهامش لـ {}: {}".format(symbol, e))
    exchange.set_leverage(LEVERAGE, symbol)
    _futures_ready.add(symbol)


def explain_error(e):
    """يضيف تفسيراً عربياً لأخطاء Binance الشائعة"""
    t = str(e)
    low = t.lower()
    hint = ""
    if '-1121' in t or 'invalid symbol' in low:
        hint = "الرمز غير موجود في هذه البيئة. "
    elif '-2015' in t or 'permissions' in low:
        hint = "صلاحيات المفتاح لا تشمل Futures، فعّل Enable Futures في إعدادات المفتاح. "
    elif '-2019' in t or 'margin is insufficient' in low:
        hint = "الهامش غير كافٍ. "
    return hint + t[:140]


def validate_symbols():
    """يتحقق من وجود الرموز في Demo. الرموز الناقصة تعمل بمحاكاة على أسعار Binance الحقيقية (بيانات عامة فقط)"""
    try:
        missing = [x for x in SYMBOLS if x not in exchange.markets]
        dropped = []
        if missing and data_exchange is not None:
            try:
                data_exchange.load_markets()
            except Exception as e:
                print("⚠️ [SYMBOLS] تعذر تحميل أسواق Binance العامة: {}".format(e))
        for x in missing:
            if data_exchange is not None and x in getattr(data_exchange, 'markets', {}):
                SIM_ONLY.add(x)
                print("🧪 [SYMBOLS] {} غير متاح في Demo، سيعمل بمحاكاة على الأسعار الحقيقية".format(x))
                log_event("SIM", "{} محاكاة (غير متاح في Demo)".format(disp(x)))
            else:
                dropped.append(x)
                print("❌ [SYMBOLS] الرمز {} غير موجود في بيئة {}، سيتم تجاهله".format(x, BINANCE_ENV))
                log_event("ERR", "الرمز {} غير موجود (تم تجاهله)".format(disp(x)))
        if dropped:
            SYMBOLS[:] = [x for x in SYMBOLS if x not in dropped]
        if not missing:
            print("✅ [SYMBOLS] كل الرموز متاحة في Demo: {}".format(", ".join(SYMBOLS)))
    except Exception as e:
        print("Validate Symbols Error: {}".format(e))


def _parse_fill(order, symbol):
    base, quote = _base_quote(symbol)
    filled = float(order.get('filled') or 0)
    cost = float(order.get('cost') or 0)
    if filled <= 0 or cost <= 0:
        raise RuntimeError("الأمر لم يُنفَّذ (الحالة: {})".format(order.get('status')))
    avg = float(order.get('average') or (cost / filled))
    return filled, cost, avg, _fees_in(order, base), _fees_in(order, quote)


def place_buy(symbol, ref_price):
    """يرجع dict فيه: price, amount (الصافي بعد العمولة), cost, simulated"""
    if not TRADING_ENABLED or symbol in SIM_ONLY:
        # محاكاة: لا يُرسل أي أمر (إما التنفيذ معطّل أو الرمز غير متاح في Demo)
        return {'price': ref_price, 'amount': TRADE_AMOUNT_USDT / ref_price,
                'cost': TRADE_AMOUNT_USDT, 'simulated': True}

    market = exchange.market(symbol)
    min_cost = ((market.get('limits') or {}).get('cost') or {}).get('min')
    if min_cost and TRADE_AMOUNT_USDT < float(min_cost):
        raise ValueError("حجم الصفقة {} أقل من الحد الأدنى {} USDT".format(TRADE_AMOUNT_USDT, min_cost))

    if IS_FUTURES:
        margin_needed = TRADE_AMOUNT_USDT / LEVERAGE
        free_usdt = _futures_free()
        if free_usdt < margin_needed * 1.05:
            raise ValueError("الهامش المتاح {:.2f} USDT أقل من المطلوب {:.2f}".format(free_usdt, margin_needed))
        _prepare_futures_symbol(symbol)
    else:
        free_usdt = float(exchange.fetch_balance().get('free', {}).get('USDT', 0.0) or 0.0)
        if free_usdt < TRADE_AMOUNT_USDT:
            raise ValueError("الرصيد المتاح {:.2f} USDT أقل من حجم الصفقة".format(free_usdt))

    amount = float(exchange.amount_to_precision(symbol, TRADE_AMOUNT_USDT / ref_price))
    min_amount = ((market.get('limits') or {}).get('amount') or {}).get('min')
    if min_amount and amount < float(min_amount):
        raise ValueError("الكمية {} أقل من الحد الأدنى {} (ارفع TRADE_AMOUNT_USDT)".format(amount, min_amount))
    order = exchange.create_order(symbol, 'market', 'buy', amount)
    order = _complete_order(order, symbol)
    filled, cost, avg, base_fee, quote_fee = _parse_fill(order, symbol)
    return {'price': avg, 'amount': filled - base_fee, 'cost': cost + quote_fee, 'simulated': False}


def place_sell(symbol, pos):
    """يرجع dict فيه: price, proceeds (الصافي بعد العمولة)"""
    if not TRADING_ENABLED or pos.get('simulated'):
        price = md(symbol).fetch_ticker(symbol)['last']
        return {'price': price, 'proceeds': pos['amount'] * price}

    if IS_FUTURES:
        amount = float(exchange.amount_to_precision(symbol, pos['amount']))
        if amount <= 0:
            raise ValueError("كمية الإغلاق صفر")
        # reduceOnly: لا يفتح صفقة بيع جديدة بالخطأ، يغلق المركز الموجود فقط
        order = exchange.create_order(symbol, 'market', 'sell', amount, None, {'reduceOnly': True})
    else:
        base = symbol.split('/')[0]
        free_base = float(exchange.fetch_balance().get('free', {}).get(base, 0.0) or 0.0)
        amount = float(exchange.amount_to_precision(symbol, min(pos['amount'], free_base)))
        if amount <= 0:
            raise ValueError("لا يوجد رصيد كافٍ من {} للبيع".format(base))
        order = exchange.create_order(symbol, 'market', 'sell', amount)
    order = _complete_order(order, symbol)
    filled, cost, avg, base_fee, quote_fee = _parse_fill(order, symbol)
    # الكمية التي لم تُبع بسبب تقريب Binance لخطوة الكمية (dust) ما زالت ملكاً لك في الحساب، فتُحسب بسعر الخروج
    dust = max(pos['amount'] - filled, 0.0)
    return {'price': avg, 'proceeds': cost - quote_fee + dust * avg}


# =====================================================================
# 4b. فلتر الأخبار (RSS + Groq)
# =====================================================================
news_state = {
    'sentiment': 'neutral', 'severity': 0, 'reason': 'لم يُفحص بعد', 'source': '',
    'checked_at': None, 'blocked_until': 0.0, 'headlines': [], 'alerted': False, 'error': ''
}

STRONG_NEGATIVE_WORDS = [
    "hack", "hacked", "exploit", "exploited", "stolen", "theft", "ban", "bans", "banned", "lawsuit",
    "sues", "sued", "bankrupt", "bankruptcy", "insolvent", "insolvency", "collapse", "depeg", "seized",
    "arrest", "arrested", "fraud", "crackdown", "halts withdrawals", "suspends withdrawals"
]


class _FollowRedirects(urllib.request.HTTPRedirectHandler):
    """Python 3.10 لا يتبع التوجيه 308، وبعض المواقع (مثل CoinDesk) تستعمله"""
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, 307, msg, headers)


_opener = urllib.request.build_opener(_FollowRedirects)


def news_blocked():
    return NEWS_FILTER and time.time() < news_state['blocked_until']


def fetch_headlines():
    """يجلب أحدث العناوين من مواقع RSS ضمن نافذة زمنية. يرجع قائمة (وقت, مصدر, عنوان)"""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=NEWS_WINDOW_HOURS)
    items, ok_feeds = [], 0
    for name, url in NEWS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (crypto-bot)'})
            data = _opener.open(req, timeout=15).read()
            root = ET.fromstring(data)
            ok_feeds += 1
            for it in root.iter('item'):
                title = (it.findtext('title') or '').strip()
                if not title:
                    continue
                pub = it.findtext('pubDate')
                try:
                    ts = parsedate_to_datetime(pub) if pub else None
                    if ts is not None and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    ts = None
                if ts is not None and ts < cutoff:
                    continue
                items.append((ts or datetime.datetime.now(datetime.timezone.utc), name, title))
        except Exception as e:
            print("📰 [NEWS] فشل جلب {}: {}".format(name, e))
    if ok_feeds == 0:
        raise RuntimeError("تعذر جلب أي مصدر أخبار")
    items.sort(key=lambda x: x[0], reverse=True)
    return items[:20]


GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_NON_CHAT = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "transcribe", "moderation", "safeguard")
GROQ_PREFERRED = ("llama-3.3-70b", "gpt-oss-120b", "llama-4", "gpt-oss-20b", "qwen", "llama-3.1-70b", "llama-3.1-8b")
_groq_model = {'name': GROQ_MODEL}


def _groq_call(path, body=None):
    req = urllib.request.Request(
        GROQ_BASE + path,
        data=json.dumps(body).encode('utf-8') if body is not None else None,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + GROQ_API_KEY,
                 'User-Agent': 'crypto-bot/1.0'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode('utf-8'))


def groq_pick_model():
    """يختار نموذج دردشة متاحاً لحساب المستخدم من قائمة Groq الحالية"""
    data = _groq_call("/models")
    ids = [m.get('id', '') for m in data.get('data', []) if m.get('active', True)]
    chat = [i for i in ids if i and not any(h in i.lower() for h in GROQ_NON_CHAT)]
    for pref in GROQ_PREFERRED:
        for i in chat:
            if pref in i.lower():
                return i
    if chat:
        return chat[0]
    raise RuntimeError("لا توجد نماذج دردشة متاحة لهذا المفتاح")


def _groq_chat(model, messages, json_mode=True):
    body = {"model": model, "temperature": 0, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = _groq_call("/chat/completions", body)
    return resp['choices'][0]['message']['content']


def classify_with_groq(titles):
    system = (
        "You are a {role}. Given recent news headlines, judge the overall "
        "impact on {assets} prices over the next few hours for LONG positions. Respond ONLY with a JSON object: "
        '{{"sentiment":"positive|neutral|negative","severity":1-5,"reason":"one short sentence in Arabic"}}. '
    ).format(role=NEWS_ROLE, assets=NEWS_ASSETS) + NEWS_SEVERE
    user = "Headlines:\n" + "\n".join("{}. {}".format(i + 1, t) for i, t in enumerate(titles))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    if not _groq_model['name']:
        _groq_model['name'] = groq_pick_model()
        print("📰 [NEWS] تم اختيار نموذج Groq تلقائياً: {}".format(_groq_model['name']))
    name = _groq_model['name']
    try:
        content = _groq_chat(name, messages, True)
    except urllib.error.HTTPError as e:
        if e.code == 400:      # قد لا يدعم النموذج وضع JSON، نعيد بدونه
            content = _groq_chat(name, messages, False)
        elif e.code == 404 and not GROQ_MODEL:   # النموذج لم يعد متاحاً، نختار غيره
            name = _groq_model['name'] = groq_pick_model()
            print("📰 [NEWS] تغيير نموذج Groq إلى: {}".format(name))
            content = _groq_chat(name, messages, True)
        else:
            raise

    match = re.search(r'\{.*\}', content, re.S)
    result = json.loads(match.group(0) if match else content)
    sentiment = str(result.get('sentiment', 'neutral')).lower()
    if sentiment not in ('positive', 'neutral', 'negative'):
        sentiment = 'neutral'
    severity = max(1, min(5, int(result.get('severity', 1))))
    return sentiment, severity, str(result.get('reason', '')).strip() or 'لا توجد تفاصيل'


def classify_with_keywords(titles):
    hits = []
    for t in titles:
        low = t.lower()
        for w in STRONG_NEGATIVE_WORDS:
            if re.search(r'\b' + re.escape(w) + r'\b', low):
                hits.append(t)
                break
    if len(hits) >= 2:
        return 'negative', 4, "عدة عناوين تحتوي كلمات خطر ({} عناوين)".format(len(hits))
    if len(hits) == 1:
        return 'negative', 3, "عنوان واحد يحتوي كلمة خطر (لا يكفي لإيقاف الشراء)"
    return 'neutral', 1, "لا توجد كلمات خطر في العناوين"


def check_news():
    """يُستدعى كل ساعة: يجلب العناوين، يصنّفها، ويحدّد هل يُوقف الشراء."""
    if not NEWS_FILTER:
        return
    try:
        items = fetch_headlines()
        titles = [t for (_, _, t) in items]
        if not titles:
            sentiment, severity, reason, source = 'neutral', 1, "لا توجد أخبار حديثة ضمن النافذة", "-"
        else:
            try:
                if not GROQ_API_KEY:
                    raise RuntimeError("no groq key")
                sentiment, severity, reason = classify_with_groq(titles)
                source = "Groq"
            except Exception as e:
                if GROQ_API_KEY:
                    print("📰 [NEWS] فشل تصنيف Groq ({}), استخدام الكلمات المفتاحية".format(e))
                sentiment, severity, reason = classify_with_keywords(titles)
                source = "Keywords"

        strong = (sentiment == 'negative' and severity >= NEWS_BLOCK_SEVERITY)
        if strong:
            news_state['blocked_until'] = max(news_state['blocked_until'], time.time() + NEWS_PAUSE_HOURS * 3600)

        news_state.update({
            'sentiment': sentiment, 'severity': severity, 'reason': reason, 'source': source,
            'checked_at': datetime.datetime.now().strftime("%H:%M"),
            'headlines': [(n, t) for (_, n, t) in items[:5]], 'error': ''
        })
        print("📰 [NEWS] {} | خطورة {} | {} ({})".format(sentiment, severity, reason, source))
        log_event("NEWS", "{} {}/5 ({})".format(sentiment, severity, source))

        if news_blocked() and not news_state['alerted']:
            news_state['alerted'] = True
            log_event("GATE", "الشراء موقوف بسبب الأخبار")
            top = "\n".join("• " + html.escape(t) for (_, _, t) in items[:3])
            send_telegram_message("📰 <b>إيقاف الشراء الجديد مؤقتاً</b>\n⚠️ {}\n{}\n⏱ حتى {} (الصفقات المفتوحة تبقى كما هي)".format(
                html.escape(reason), top,
                datetime.datetime.fromtimestamp(news_state['blocked_until']).strftime("%H:%M")))
        elif news_state['alerted'] and not news_blocked():
            news_state['alerted'] = False
            log_event("GATE", "عاد الشراء للعمل")
            send_telegram_message("📰 <b>عاد الشراء الجديد للعمل</b>\nانتهت مدة الإيقاف ولا توجد أخبار سلبية قوية الآن.")
    except Exception as e:
        news_state['error'] = str(e)
        print("📰 [NEWS] خطأ: {}".format(e))


# =====================================================================
# 5. إدارة الصفقات والفحص الدوري
# =====================================================================
def check_and_close_positions():
    for symbol in list(active_positions.keys()):
        try:
            pos = active_positions[symbol]
            current_price = md(symbol).fetch_ticker(symbol)['last']
            entry_price = pos['entry_price']

            highest_price = max(pos.get('highest_price', entry_price), current_price)
            if highest_price != pos.get('highest_price'):
                pos['highest_price'] = highest_price
                save_position(symbol, pos)

            pnl_pct = ((current_price - entry_price) / entry_price) * 100

            reason = None
            if pos.get('test'):
                opened = datetime.datetime.strptime(pos['time'], "%Y-%m-%d %H:%M:%S")
                if (datetime.datetime.now() - opened).total_seconds() >= TEST_TRADE_SECONDS:
                    reason = "TEST"
            elif pnl_pct >= TAKE_PROFIT_PCT:
                reason = "TP"
            else:
                initial_sl = entry_price * (1 - INITIAL_SL_PCT / 100)
                trailing_sl = highest_price * (1 - TRAILING_SL_PCT / 100)
                if current_price <= max(initial_sl, trailing_sl):
                    reason = "SL"

            if not reason:
                continue

            try:
                sale = place_sell(symbol, pos)
            except Exception as e:
                print("❌ [SELL ERROR] {}: {}".format(symbol, e))
                log_event("ERR", "بيع {}: {}".format(symbol, str(e)[:80]))
                if not pos.get('sell_error_notified'):
                    pos['sell_error_notified'] = True
                    send_telegram_message("❌ <b>فشل بيع {}</b>\n{}".format(symbol, html.escape(str(e))))
                continue

            realized_pnl = ((sale['proceeds'] - pos['cost']) / pos['cost']) * 100
            status = "WIN" if realized_pnl > 0 else "LOSS"
            title = {"TP": "🎯 تم تحقيق الهدف", "TEST": "🧪 إغلاق صفقة الاختبار"}.get(reason, "🛑 إغلاق بوقف الخسارة")
            print("{} [{}] {} | PnL: {:+.2f}%".format(title, status, symbol, realized_pnl))
            log_event({"TP": "TP", "TEST": "TEST"}.get(reason, "SL"), "{} {:+.2f}% ({})".format(symbol, realized_pnl, status))
            log_trade(symbol, entry_price, sale['price'], realized_pnl, status, sale['proceeds'] - pos['cost'])
            send_telegram_message(
                "{} ({})\n🪙 العملة: {}\n📥 سعر الدخول: {}\n📤 سعر الخروج: {}\n📊 النتيجة (بعد العمولة): {:+.2f}%{}".format(
                    title, status, symbol, entry_price, sale['price'], realized_pnl,
                    "\n🧪 محاكاة" if (not TRADING_ENABLED or pos.get('simulated')) else ""))
            del active_positions[symbol]
            delete_position(symbol)
        except Exception as e:
            print("Error in position monitoring ({}): {}".format(symbol, e))


def automated_market_scan():
    if not scan_lock.acquire(blocking=False):
        print("⏳ [SCANNER] فحص آخر قيد التنفيذ، تم التخطي")
        return
    try:
        print("🔄 [SCANNER] جاري فحص الأسواق والمؤشرات الفنية ({} | {})...".format(BINANCE_ENV, TIMEFRAME))
        check_and_close_positions()

        for symbol in SYMBOLS:
            try:
                ohlcv = md(symbol).fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_LIMIT)
                closed = ohlcv[:-1]  # نتجاهل الشمعة الجارية، الإشارة تُحسب على شموع مغلقة فقط
                if len(closed) < EMA_PERIOD + 1:
                    print("⚠️ {} بيانات غير كافية".format(symbol))
                    continue

                closes = [c[4] for c in closed]
                last_candle_ts = closed[-1][0]
                rsi = calculate_rsi(closes)
                ema = calculate_ema(closes, EMA_PERIOD)
                macd, signal, hist = calculate_macd(closes)
                current_price = md(symbol).fetch_ticker(symbol)['last']

                cond_ema = ema is not None and closes[-1] > ema
                cond_macd = hist > 0 or not REQUIRE_MACD
                cond_rsi = rsi <= RSI_MAX_ENTRY
                signal_ok = cond_rsi and cond_ema and cond_macd
                market_snapshot[symbol] = {
                    'price': current_price, 'rsi': rsi, 'ema': ema, 'hist': hist, 'signal': signal_ok,
                    'conds': {'ema': cond_ema, 'macd': cond_macd, 'rsi': cond_rsi}, 'ref_close': closes[-1],
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                }
                print("📊 {} - Price: {} | RSI: {:.2f} | EMA{}: {:.2f} | MACD hist: {:.4f}".format(
                    symbol, current_price, rsi, EMA_PERIOD, ema, hist))

                global test_trade_done
                if FORCE_TEST_TRADE and not test_trade_done and symbol not in active_positions:
                    test_trade_done = True
                    try:
                        fill = place_buy(symbol, current_price)
                        pos = {
                            'entry_price': fill['price'], 'amount': fill['amount'],
                            'cost': fill['cost'], 'highest_price': fill['price'],
                            'time': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            'simulated': fill['simulated'], 'test': True
                        }
                        active_positions[symbol] = pos
                        save_position(symbol, pos)
                        print("🧪 [TEST BUY] {} بسعر {}".format(symbol, fill['price']))
                        send_telegram_message("🧪 <b>صفقة اختبار (BUY)</b>\n🪙 {}\n💵 {}\n⏱ ستُغلق بعد {} ثانية".format(
                            symbol, fill['price'], TEST_TRADE_SECONDS))
                    except Exception as e:
                        print("❌ [TEST BUY ERROR] {}: {}".format(symbol, e))
                        send_telegram_message("❌ <b>فشل شراء الاختبار</b>\n{}".format(html.escape(str(e))))
                    continue

                if symbol in active_positions:
                    continue
                if last_entry_candle.get(symbol) == last_candle_ts:
                    continue

                if not signal_ok:
                    continue

                if news_blocked():
                    print("📰 [NEWS] إشارة شراء على {} لكن الشراء موقوف بسبب الأخبار حتى {}".format(
                        symbol, datetime.datetime.fromtimestamp(news_state['blocked_until']).strftime("%H:%M")))
                    continue

                if len(active_positions) >= MAX_OPEN_POSITIONS:
                    print("⏸ [LIMIT] إشارة شراء على {} لكن الحد الأقصى للصفقات المفتوحة ({}) تحقق".format(symbol, MAX_OPEN_POSITIONS))
                    continue

                last_entry_candle[symbol] = last_candle_ts
                try:
                    fill = place_buy(symbol, current_price)
                except Exception as e:
                    msg = explain_error(e)
                    print("❌ [BUY ERROR] {}: {}".format(symbol, msg))
                    prev = buy_err_seen.get(symbol)
                    if not prev or prev[0] != msg or time.time() - prev[1] > 3600:   # لا نكرر نفس الرسالة كل شمعة
                        buy_err_seen[symbol] = (msg, time.time())
                        log_event("ERR", "شراء {}: {}".format(disp(symbol), msg[:90]))
                        send_telegram_message("❌ <b>فشل شراء {}</b>\n{}".format(disp(symbol), html.escape(msg)))
                    continue

                pos = {
                    'entry_price': fill['price'],
                    'amount': fill['amount'],
                    'cost': fill['cost'],
                    'highest_price': fill['price'],
                    'time': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'simulated': fill['simulated']
                }
                active_positions[symbol] = pos
                save_position(symbol, pos)
                print("🚀 [BUY] {} بسعر {} | الكمية {:.6f} | RSI: {:.2f}".format(symbol, fill['price'], fill['amount'], rsi))
                log_event("BUY", "{} @ {:,.2f} | {:.6f}".format(symbol, fill['price'], fill['amount']))
                send_telegram_message(
                    "🚀 <b>فتح صفقة جديدة (BUY)</b>\n🪙 العملة: {}\n💵 السعر: {}\n📦 الكمية: {:.6f}\n💰 التكلفة: {:.2f} USDT\n📉 RSI: {:.2f}{}".format(
                        symbol, fill['price'], fill['amount'], fill['cost'], rsi,
                        "\n🧪 محاكاة" if fill['simulated'] else ""))
            except Exception as e:
                print("Error scanning {}: {}".format(symbol, e))
        record_equity()
    except Exception as e:
        print("Error in automated market scan: {}".format(e))
    finally:
        scan_lock.release()


# =====================================================================
# 6. لوحة التحكم (صفحة واحدة تقرأ البيانات الحية من /api/state)
# =====================================================================
COIN_STYLE = {
    'BTC': ('#f7931a', '₿'), 'ETH': ('#627eea', 'Ξ'), 'BNB': ('#f3ba2f', 'B'), 'SOL': ('#9945ff', 'S'),
    'XRP': ('#3b4a5a', 'X'), 'ADA': ('#2a5ada', 'A'), 'DOGE': ('#c2a633', 'Ð'), 'AVAX': ('#e84142', 'A'),
    'LINK': ('#2a5ada', 'L'), 'DOT': ('#e6007a', '●'), 'LTC': ('#6b7b8c', 'Ł'), 'TRX': ('#e50914', 'T'),
}


def coin_badge(symbol):
    base = disp(symbol).split('/')[0]
    color, glyph = COIN_STYLE.get(base, ('#3a3a3a', base[:1]))
    return '<span class="coin" style="background:{}">{}</span>'.format(color, glyph)


def bdi(v):
    """يعزل الأرقام والرموز حتى لا تنقلب علامات + و - و % داخل النص العربي"""
    return "<bdi>{}</bdi>".format(v)


def money(v, signed=False):
    if signed:
        sign = '+' if v >= 0 else '-'
    else:
        sign = '-' if v < 0 else ''
    return "{}${:,.2f}".format(sign, abs(v))


def tone(v):
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def build_market_cards():
    cards = ""
    for sym in SYMBOLS:
        d = market_snapshot.get(sym)
        badge = coin_badge(sym)
        if not d:
            cards += ('<div class="mkt"><div class="mkt-head">{}<div class="mkt-title"><b>{}</b>'
                      '<small>بانتظار أول فحص</small></div></div></div>').format(badge, bdi(disp(sym)))
            continue
        ready = d.get('signal')
        price = "${:,.4f}".format(d['price']) if (isinstance(d['price'], (int, float)) and d['price'] < 10) else (
            "${:,.2f}".format(d['price']) if isinstance(d['price'], (int, float)) else str(d['price']))
        ema = "{:,.4f}".format(d['ema']) if (isinstance(d.get('ema'), (int, float)) and d['ema'] < 10) else (
            "{:,.2f}".format(d['ema']) if isinstance(d.get('ema'), (int, float)) else "-")
        hist = d.get('hist', 0.0)
        cards += (
            '<div class="mkt">'
            '<div class="mkt-head">{badge}<div class="mkt-title"><b>{sym}</b><small>آخر تحديث {time}</small></div>'
            '<span class="pill {pill}">{pill_text}</span></div>'
            '<div class="mkt-price">{price}</div>'
            '<div class="mkt-grid">'
            '<div><small>RSI</small><b class="{rsi_cls}">{rsi}</b></div>'
            '<div><small>EMA {ema_p}</small><b>{ema}</b></div>'
            '<div><small>MACD</small><b class="{hist_cls}">{hist}</b></div>'
            '</div></div>'
        ).format(
            badge=badge, sym=bdi(disp(sym)), time=bdi(d['time']),
            pill="pill-on" if ready else "pill-off",
            pill_text="إشارة دخول" if ready else "انتظار",
            price=bdi(price), rsi=bdi("{:.1f}".format(d['rsi'])),
            rsi_cls="down" if d['rsi'] > RSI_MAX_ENTRY else "",
            ema_p=EMA_PERIOD, ema=bdi(ema), hist=bdi("{:+.4f}".format(hist) if abs(hist) < 1 else "{:+.2f}".format(hist)),
            hist_cls=tone(hist))
    return cards


def build_tx_cards(recent_trades):
    cards = ""
    # الصفقات المفتوحة أولاً
    for sym, pos in active_positions.items():
        price = market_snapshot.get(sym, {}).get('price')
        if isinstance(price, (int, float)):
            pnl_usdt = pos['amount'] * price - pos['cost']
            pnl_pct = pnl_usdt / pos['cost'] * 100
        else:
            pnl_usdt, pnl_pct = 0.0, 0.0
        cards += (
            '<div class="tx">'
            '<div class="tx-head">{badge}<div><b>صفقة مفتوحة</b><small>{time}</small></div></div>'
            '<div class="tx-body"><span class="tx-sym">{base}{sim}</span>'
            '<span class="tx-val {cls}">{usdt}</span>'
            '<span class="tx-sub">{pct} منذ الدخول عند {entry}</span></div>'
            '<div class="tx-status open">قيد التنفيذ</div></div>'
        ).format(badge=coin_badge(sym), time=bdi(pos.get('time', '')[5:16]), base=bdi(disp(sym).split('/')[0]),
                 sim=" (محاكاة)" if pos.get('simulated') else "", cls=tone(pnl_usdt),
                 usdt=bdi(money(pnl_usdt, True)), pct=bdi("{:+.2f}%".format(pnl_pct)),
                 entry=bdi("{:,.4f}".format(pos['entry_price']) if pos['entry_price'] < 10 else "{:,.2f}".format(pos['entry_price'])))

    for idx, t in enumerate(recent_trades):
        trade_id, sym, entry, exit_p, pnl_pct, status, ts, pnl_usdt = t
        is_win = status == "WIN"
        highlight = " hl" if (idx == 0 and is_win) else ""
        fmt = (lambda v: "{:,.4f}".format(v)) if entry < 10 else (lambda v: "{:,.2f}".format(v))
        cards += (
            '<div class="tx{hl}">'
            '<div class="tx-head">{badge}<div><b>صفقة مغلقة</b><small>{time}</small></div></div>'
            '<div class="tx-body"><span class="tx-sym">{base}</span>'
            '<span class="tx-val {cls}">{usdt}</span>'
            '<span class="tx-sub">{pct} (من {entry} إلى {exit})</span></div>'
            '<div class="tx-status {st}">{label}</div></div>'
        ).format(hl=highlight, badge=coin_badge(sym), time=bdi(ts[5:16]), base=bdi(disp(sym).split('/')[0]),
                 cls=tone(pnl_usdt), usdt=bdi(money(pnl_usdt, True)),
                 pct=bdi("{:+.2f}%".format(pnl_pct)),
                 entry=bdi(fmt(entry)), exit=bdi(fmt(exit_p)),
                 st="win" if is_win else "loss", label="رابحة" if is_win else "خاسرة")

    if not cards:
        cards = ('<div class="empty">لا توجد صفقات بعد. يشتري البوت عندما يكون السعر فوق EMA {} '
                 'ويكون MACD صاعداً وRSI أقل من {}.</div>').format(EMA_PERIOD, bdi("{:.0f}".format(RSI_MAX_ENTRY)))
    return cards


def build_news_card():
    if not NEWS_FILTER:
        return '<div class="news-card"><span class="pill pill-off">فلتر الأخبار معطّل</span></div>'
    st = news_state
    labels = {'positive': ('إيجابية', 'up'), 'neutral': ('محايدة', 'flat'), 'negative': ('سلبية', 'down')}
    label, cls = labels.get(st['sentiment'], labels['neutral'])
    if news_blocked():
        until = datetime.datetime.fromtimestamp(st['blocked_until']).strftime("%H:%M")
        status = '<span class="pill pill-off">الشراء موقوف حتى {}</span>'.format(bdi(until))
    else:
        status = '<span class="pill pill-on">الشراء مسموح</span>'
    if st['checked_at'] is None:
        body = '<p class="news-reason">جاري أول فحص للأخبار...</p>'
    else:
        heads = "".join('<li><bdi>{}</bdi> <small>{}</small></li>'.format(html.escape(t), bdi(html.escape(n)))
                        for (n, t) in st['headlines'])
        body = ('<p class="news-reason"><b class="{cls}">أخبار {label}</b>، الخطورة {sev} من 5. {reason}</p>'
                '<ul class="news-list">{heads}</ul>'
                '<small>آخر فحص {time}، التصنيف: {src}</small>').format(
            cls=cls, label=label, sev=bdi(st['severity']), reason=html.escape(st['reason']),
            heads=heads, time=bdi(st['checked_at']), src=bdi(st['source']))
    if st['error']:
        body += '<small class="down"> تعذّر آخر تحديث: {}</small>'.format(html.escape(st['error']))
    return '<div class="news-card"><div class="news-head">{}</div>{}</div>'.format(status, body)


def render_lime():
    """اللوحة الفسفورية الليمونية (تُبنى على الخادم وتتحدث كل دقيقة)"""
    total, wins, losses, win_rate, total_pnl, best, worst = get_performance_stats()
    realized_total, realized_today = get_realized_stats()
    recent_trades = get_recent_trades(limit=8)
    free_usdt, total_usdt = get_binance_balance()

    invested = 0.0
    unrealized = 0.0
    for sym, pos in active_positions.items():
        invested += pos['cost']
        price = market_snapshot.get(sym, {}).get('price')
        if isinstance(price, (int, float)):
            unrealized += pos['amount'] * price - pos['cost']
    unrealized_pct = (unrealized / invested * 100) if invested else 0.0
    exposure = min(invested / (LEVERAGE if IS_FUTURES else 1) / total_usdt * 100, 100.0) if total_usdt > 0 else 0.0
    exposure_note = "تعرّض منخفض" if exposure < 20 else ("تعرّض متوسط" if exposure < 50 else "تعرّض مرتفع")

    values = {
        'env_label': BINANCE_ENV,
        'mode_label': "تنفيذ فعلي" if TRADING_ENABLED else "محاكاة",
        'mode_class': "pill-on" if TRADING_ENABLED else "pill-off",
        'wallet_value': bdi(money(total_usdt)),
        'free_usdt': bdi(money(free_usdt)),
        'realized_pl': bdi(money(realized_total, True)),
        'realized_cls': tone(realized_total),
        'realized_sub': "{} اليوم".format(bdi(money(realized_today, True))),
        'unrealized_pl': bdi(money(unrealized, True)),
        'unrealized_cls': tone(unrealized),
        'unrealized_sub': "{} على {} صفقة مفتوحة".format(bdi("{:+.2f}%".format(unrealized_pct)), bdi(len(active_positions))),
        'win_rate': bdi("{:.1f}%".format(win_rate)),
        'win_sub': "{} رابحة، {} خاسرة".format(bdi(wins), bdi(losses)),
        'trades_total': bdi(total),
        'trades_sub': "الأفضل {} والأسوأ {}".format(bdi("{:+.2f}%".format(best)), bdi("{:+.2f}%".format(worst))),
        'exposure_pct': "{:.0f}".format(exposure),
        'exposure_note': exposure_note,
        'open_count': len(active_positions),
        'market_cards': build_market_cards(),
        'tx_cards': build_tx_cards(recent_trades),
        'trade_amount': bdi(money(TRADE_AMOUNT_USDT)),
        'news_card': build_news_card(),
    }
    with open("index_lime.html", "r", encoding="utf-8") as f:
        page = f.read()
    for key, val in values.items():
        page = page.replace("[[{}]]".format(key), str(val))
    return page


@app.get("/", response_class=HTMLResponse)
def read_root():
    try:
        if DASHBOARD_THEME != "neon":
            return HTMLResponse(content=render_lime())
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        return HTMLResponse(content="<h1>Error loading dashboard: {}</h1>".format(html.escape(str(e))))


@app.get("/api/state")
def api_state():
    total, wins, losses, win_rate, total_pnl, best, worst = get_performance_stats()
    realized_total, realized_today = get_realized_stats()
    free_usdt, total_usdt = get_binance_balance()

    positions = []
    invested = 0.0
    unrealized = 0.0
    for sym, pos in active_positions.items():
        entry = pos['entry_price']
        high = pos.get('highest_price', entry)
        price = market_snapshot.get(sym, {}).get('price')
        price = price if isinstance(price, (int, float)) else entry
        pnl_usdt = pos['amount'] * price - pos['cost']
        invested += pos['cost']
        unrealized += pnl_usdt
        positions.append({
            'symbol': sym, 'entry': entry, 'amount': pos['amount'], 'cost': pos['cost'],
            'highest': high, 'price': price, 'pnl_usdt': pnl_usdt,
            'pnl_pct': pnl_usdt / pos['cost'] * 100 if pos['cost'] else 0.0,
            'stop': max(entry * (1 - INITIAL_SL_PCT / 100), high * (1 - TRAILING_SL_PCT / 100)),
            'target': entry * (1 + TAKE_PROFIT_PCT / 100),
            'simulated': bool(pos.get('simulated')), 'time': pos.get('time', '')
        })

    markets = []
    for sym in SYMBOLS:
        d = market_snapshot.get(sym)
        if not d:
            markets.append({'symbol': sym, 'sim': sym in SIM_ONLY, 'ready': False, 'in_position': sym in active_positions})
            continue
        markets.append({
            'symbol': sym, 'sim': sym in SIM_ONLY, 'ready': True, 'price': d['price'], 'rsi': d['rsi'], 'ema': d['ema'],
            'ref_close': d.get('ref_close', d['price']), 'hist': d['hist'], 'signal': d['signal'], 'conds': d.get('conds', {}),
            'time': d['time'], 'in_position': sym in active_positions
        })

    trades = [{
        'id': t[0], 'symbol': t[1], 'entry': t[2], 'exit': t[3], 'pnl_pct': t[4],
        'status': t[5], 'time': t[6], 'pnl_usdt': t[7]
    } for t in get_recent_trades(limit=8)]

    return {
        'env': BINANCE_ENV,
        'trading_enabled': TRADING_ENABLED,
        'telegram': bool(TELEGRAM_BOT_TOKEN),
        'trade_amount': TRADE_AMOUNT_USDT,
        'market_type': MARKET_TYPE,
        'leverage': LEVERAGE if IS_FUTURES else 1,
        'margin_required': TRADE_AMOUNT_USDT / (LEVERAGE if IS_FUTURES else 1),
        'wallet': {'total': total_usdt, 'free': free_usdt},
        'stats': {
            'realized_total': realized_total, 'realized_today': realized_today,
            'unrealized': unrealized, 'wins': wins, 'losses': losses, 'total': total,
            'win_rate': win_rate, 'best': best, 'worst': worst,
            'exposure': min(invested / (LEVERAGE if IS_FUTURES else 1) / total_usdt * 100, 100.0) if total_usdt > 0 else 0.0
        },
        'params': {
            'timeframe': TIMEFRAME, 'ema_period': EMA_PERIOD, 'rsi_max': RSI_MAX_ENTRY,
            'require_macd': REQUIRE_MACD, 'tp': TAKE_PROFIT_PCT, 'sl': INITIAL_SL_PCT,
            'trail': TRAILING_SL_PCT, 'news_severity': NEWS_BLOCK_SEVERITY, 'max_open': MAX_OPEN_POSITIONS,
            'news_pause_h': NEWS_PAUSE_HOURS
        },
        'markets': markets,
        'positions': positions,
        'trades': trades,
        'news': {
            'enabled': NEWS_FILTER, 'sentiment': news_state['sentiment'],
            'severity': news_state['severity'], 'reason': news_state['reason'],
            'source': news_state['source'], 'checked_at': news_state['checked_at'],
            'blocked': bool(news_blocked()),
            'blocked_until': datetime.datetime.fromtimestamp(news_state['blocked_until']).strftime("%H:%M") if news_state['blocked_until'] else None,
            'model': _groq_model.get('name') or '',
            'headlines': [{'source': n, 'title': t} for (n, t) in news_state['headlines']],
            'error': news_state['error']
        },
        'events': list(event_log)
    }


@app.get("/api/equity")
def api_equity(range: str = "1d"):
    return get_equity_series(range)


@app.get("/check")
def manual_check():
    automated_market_scan()
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
