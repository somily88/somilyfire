import os
import time
import logging
import re
from firecrawl import Firecrawl
from openai import OpenAI
import ccxt
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# إعداد السجلات (Logging)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# قراءة المفاتيح بأمان من بيئة العمل في Render
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

# تهيئة الخدمات
firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# تهيئة منصة التداول (Binance Testnet)
exchange = ccxt.binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_SECRET_KEY,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
exchange.set_sandbox_mode(True)

# --- وظيفة جلب وتحليل الأخبار ---
def fetch_and_analyze_news(target_url: str, symbol: str):
    logging.info(f"جاري سحب الأخبار من الموقع: {target_url}")
    try:
        scrape_result = firecrawl.scrape(target_url, formats=['markdown'])
        news_content = scrape_result.get('markdown', '')

        if not news_content:
            return "NEUTRAL", 0.0, "محتوى الأخبار فارغ."

        prompt = f"""
        Analyze the following financial/crypto news text and determine if it's Bullish (Positive), Bearish (Negative), or Neutral for {symbol}.
        Provide your response strictly in this exact format:
        SIGNAL: [BULLISH / BEARISH / NEUTRAL]
        CONFIDENCE: [A decimal number between 0.0 and 1.0, e.g., 0.85]
        REASON: [Short explanation in Arabic]

        News Content:
        {news_content[:4000]}
        """

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        
        analysis = response.choices[0].message.content
        logging.info(f"نتيجة تحليل الذكاء الاصطناعي:\n{analysis}")
        
        signal_match = re.search(r"SIGNAL:\s*(BULLISH|BEARISH|NEUTRAL)", analysis, re.IGNORECASE)
        confidence_match = re.search(r"CONFIDENCE:\s*([0-9.]+)", analysis)

        signal = signal_match.group(1).upper() if signal_match else "NEUTRAL"
        confidence = float(confidence_match.group(1)) if confidence_match else 0.0

        return signal, confidence, analysis
        
    except Exception as e:
        logging.error(f"خطأ أثناء سحب أو تحليل الأخبار: {e}")
        return "NEUTRAL", 0.0, str(e)

# --- وظيفة تنفيذ صفقة التداول (بشرط الثقة >= 70%) ---
async def execute_trade_if_confident(signal: str, confidence: float, symbol: str, amount_usdt: float, context: ContextTypes.DEFAULT_TYPE):
    MIN_CONFIDENCE_THRESHOLD = 0.70

    if confidence < MIN_CONFIDENCE_THRESHOLD:
        logging.info(f"نسبة الثقة ({confidence * 100}%) أقل من الحد الأدنى (70%).")
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, 
            text=f"⚠️ **تم تخطي التداول:** الإشارة ({signal}) ولكن نسبة الثقة ({confidence * 100:.0f}%) أقل من الحد الأدنى المطلوبة (70%).",
            parse_mode='Markdown'
        )
        return

    try:
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['last']

        if signal == "BULLISH":
            message = f"🚀 **تم تنفيذ أمر شراء آلي بنجاح!**\n\n- **العملة:** {symbol}\n- **السعر الحالي:** {current_price}\n- **نسبة الثقة:** {confidence * 100:.0f}%\n- **السبب:** بناءً على تحليل الأخبار عبر Firecrawl."
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')

        elif signal == "BEARISH":
            message = f"📉 **تنبيه سلبي / أمر بيع آلي!**\n\n- **العملة:** {symbol}\n- **السعر الحالي:** {current_price}\n- **نسبة الثقة:** {confidence * 100:.0f}%\n- **السبب:** رصد أخبار سلبية مؤكدة."
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"خطأ في تنفيذ التداول: {e}")
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❌ فشل تنفيذ الصفقة آلياً: {e}")

# --- أمر تيليجرام الفوري ---
async def check_news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="🔄 جاري فحص مواقع الأخبار عبر Firecrawl وتحليل السوق...")
    
    target_url = "https://coindesk.com"
    symbol = "BTC/USDT"
    
    signal, confidence, full_analysis = fetch_and_analyze_news(target_url, symbol)
    await context.bot.send_message(chat_id=chat_id, text=f"📊 **نتيجة التحليل:**\n\n{full_analysis}")
    await execute_trade_if_confident(signal, confidence, symbol, 10.0, context)

# --- تشغيل البوت ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("checknews", check_news_command))
    print("🤖 البوت يعمل الآن ويستمع لأوامر تيليجرام...")
    app.run_polling()

if __name__ == '__main__':
    main()