# تثبيت بوت التداول على VPS من Hostinger

البوت يعمل داخل Docker، فالتثبيت كله أوامر قليلة. الخطوات أدناه لخادم **Ubuntu 24.04** وتُنفَّذ من PowerShell على ويندوز.

## محتويات الحزمة
| الملف | وظيفته |
|---|---|
| `main.py` | البوت وخادم لوحة التحكم |
| `index_lime.html` و`index.html` | شكلا لوحة التحكم: الفسفورية الليمونية (الافتراضية) والبنفسجية المتوهجة |
| `Dockerfile` و`docker-compose.yaml` | بناء وتشغيل الحاوية |
| `requirements.txt` | مكتبات بايثون |
| `.env.example` | قالب الإعدادات والمفاتيح (انسخه إلى `.env`) |
| `test_news.py` | اختبار الاتصال بالأخبار وGroq (اختياري) |
| `update.sh` | تحديث البوت على الخادم بعد `git push` |
| `GITHUB-DEPLOY.md` | التثبيت والتحديث عبر GitHub |

---

> تفضّل التثبيت عبر GitHub؟ اتبع الملف `GITHUB-DEPLOY.md` بدل الخطوة 4 (رفع الملفات).

## 1) أنشئ الخادم في Hostinger
1. اطلب خطة **VPS** (أرخص خطة تكفي، فالبوت خفيف جداً).
2. اختر نظام التشغيل: **Ubuntu 24.04 with Docker**. هذا قالب جاهز من Hostinger فيه Docker وDocker Compose مثبّتان.
3. اختر موقع مركز البيانات **خارج الولايات المتحدة** (مثلاً أوروبا أو آسيا). Binance تحجب عناوين IP أمريكية، وإن ظهر في اللوج خطأ مثل `restricted location` أو `HTTP 451` فغيّر الموقع.
4. ضع كلمة مرور قوية للمستخدم root، وسجّل **عنوان IP** للخادم (سنسميه `SERVER_IP`).

## 2) اتصل بالخادم
من PowerShell على جهازك:
```powershell
ssh root@SERVER_IP
```
(أو استخدم "Browser terminal" من لوحة hPanel.) ثم تحقق من Docker:
```bash
docker --version
docker compose version
```
إذا لم يعمل `docker compose` جرّب `docker-compose` بدله في كل الأوامر أدناه.

## 3) فعّل الجدار الناري (مرة واحدة)
```bash
ufw allow OpenSSH
ufw enable
```
اللوحة مربوطة على `127.0.0.1` افتراضياً، فلا تُفتح للإنترنت أصلاً.

## 4) ارفع ملفات البوت
على جهازك (PowerShell)، وأنت في المجلد الذي فيه `crypto-bot-vps.zip`:
```powershell
scp .\crypto-bot-vps.zip root@SERVER_IP:/opt/
```
ثم على الخادم:
```bash
cd /opt
apt-get install -y unzip
unzip crypto-bot-vps.zip
cd crypto-bot
```

## 5) اضبط الإعدادات والمفاتيح
```bash
cp .env.example .env
nano .env
chmod 600 .env
```
في `nano`: عدّل القيم ثم احفظ بـ `Ctrl+O` ثم `Enter` ثم اخرج بـ `Ctrl+X`. أهم ما تعبّئه:
- `BINANCE_API_KEY` و`BINANCE_SECRET_KEY`: مفاتيح **Demo** من demo.binance.com.
- `TELEGRAM_BOT_TOKEN` و`TELEGRAM_CHAT_ID`.
- `GROQ_API_KEY` لفلتر الأخبار.
- `SYMBOLS` (قائمة العملات) و`TRADE_AMOUNT_USDT` و`MAX_OPEN_POSITIONS` حسب ما تريد.
- `DASHBOARD_THEME`: `lime` للفسفورية الليمونية أو `neon` للبنفسجية.
- أبقِ `BINANCE_ENV=demo`.

> استخدم مفاتيح **جديدة** على الخادم، ولا تعِد استخدام مفاتيح سبق أن شاركتها في أي محادثة أو ملف.

## 6) شغّل البوت
```bash
docker compose up -d --build
docker logs -f telegram_crypto_bot
```
(أوقف عرض اللوج بـ `Ctrl+C`، فالبوت يستمر بالعمل.) يجب أن ترى أسطراً مثل:
```
🧭 [MARKET] SPOT | symbols: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT, ADA/USDT
✅ [SYMBOLS] كل الرموز متاحة في Demo
📰 [NEWS] ... (Groq)
🔄 [SCANNER] جاري فحص الأسواق ...
```
وتصلك رسالة "تم تشغيل بوت التداول" على تيليجرام.

البوت يعاد تشغيله تلقائياً إذا أُعيد تشغيل الخادم (`restart: unless-stopped`)، والصفقات المفتوحة تُستعاد من قاعدة البيانات في مجلد `data`.

## 7) افتح لوحة التحكم
### الطريقة الآمنة (موصى بها): نفق SSH
من PowerShell على جهازك واتركه مفتوحاً:
```powershell
ssh -L 8000:127.0.0.1:8000 root@SERVER_IP -N
```
ثم افتح المتصفح على `http://localhost:8000`.

### الطريقة البديلة: فتحها من الإنترنت
1. في `.env`: `DASHBOARD_BIND=0.0.0.0` و`DASHBOARD_PASSWORD=` كلمة مرور قوية (والمستخدم `DASHBOARD_USER`).
2. افتح المنفذ: `ufw allow 8000/tcp`، وإن كان لديك جدار ناري في hPanel فافتح 8000 هناك أيضاً.
3. أعد التشغيل: `docker compose up -d`.
4. افتح `http://SERVER_IP:8000` وسيطلب اسم المستخدم وكلمة المرور.

تنبيه: هذه الطريقة ترسل كلمة المرور بدون تشفير (HTTP). الأفضل وضع HTTPS أمامها، انظر الفقرة التالية.

### اختياري: HTTPS بدومين عبر Caddy
أنشئ سجل DNS من نوع A يربط دومينك (مثل `bot.example.com`) بـ `SERVER_IP`، ثم أنشئ ملف `Caddyfile` بجانب `docker-compose.yaml`:
```
bot.example.com {
    reverse_proxy trading_bot:8000
}
```
وأضف إلى `docker-compose.yaml` (تحت `services:` وفي نهاية الملف):
```yaml
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data

volumes:
  caddy_data:
```
افتح المنفذين: `ufw allow 80/tcp` و`ufw allow 443/tcp`، ثم `docker compose up -d`. اترك `DASHBOARD_BIND=127.0.0.1` وضع `DASHBOARD_PASSWORD`. يحصل Caddy على شهادة HTTPS تلقائياً.

---

## أوامر يومية
```bash
cd /opt/crypto-bot
docker logs -f telegram_crypto_bot      # متابعة اللوج
docker logs --tail 100 telegram_crypto_bot
docker compose restart                  # إعادة تشغيل (لا يعيد قراءة .env)
docker compose up -d                    # يعيد إنشاء الحاوية ويقرأ .env الجديد
docker compose down                     # إيقاف
docker compose ps                       # الحالة
```
> بعد أي تعديل على `.env` نفّذ `docker compose up -d` (لا يكفي `restart`).

## تحديث البوت لاحقاً
ارفع الملفات الجديدة (`main.py` و`index.html`) إلى `/opt/crypto-bot` بـ `scp` ثم:
```bash
cd /opt/crypto-bot
docker compose up -d --build
```
بياناتك في مجلد `data` لا تتأثر.

## النسخ الاحتياطي
قاعدة البيانات (الصفقات وسجل المحفظة) في:
```
/opt/crypto-bot/data/trading_bot.db
```
انسخها إلى جهازك من حين لآخر:
```powershell
scp root@SERVER_IP:/opt/crypto-bot/data/trading_bot.db .
```

## اختبار الأخبار وGroq (اختياري)
```bash
docker cp test_news.py telegram_crypto_bot:/app/test_news.py
docker exec telegram_crypto_bot python /app/test_news.py
```

## مشاكل شائعة
| المشكلة | الحل |
|---|---|
| `restricted location` أو `HTTP 451` | موقع الخادم محجوب من Binance. أعد إنشاء الخادم في موقع آخر |
| `Invalid Api-Key ID` | المفتاح لا يطابق البيئة. مفاتيح Demo تعمل مع `BINANCE_ENV=demo` فقط |
| `-2015` أو `permissions` | صلاحيات المفتاح ناقصة، أعد إنشاءه مع تفعيل Spot Trading |
| ظهر `❌ [SYMBOLS] ... سيتم تجاهله` | العملة غير متاحة في Demo، احذفها من `SYMBOLS` |
| `docker compose: command not found` | استخدم `docker-compose` بدل `docker compose` |
| تغيير `.env` لا يظهر أثره | نفّذ `docker compose up -d` وليس `restart` |
| اللوحة لا تفتح | تأكد من النفق (`ssh -L ...`) ومن أن الحاوية تعمل: `docker compose ps` |

## تذكيرات أمان
- **لا تفعّل صلاحية السحب (Withdrawals)** في أي مفتاح Binance.
- قيّد المفتاح بعنوان IP الخادم من إعدادات المفتاح في Binance إن أتاح ذلك.
- ما دمت على Demo فلا مال حقيقي يُخاطَر به. لا تنتقل للحساب الحقيقي قبل مراقبة النتائج أياماً طويلة.
- وقف الخسارة والهدف يديرهما البوت، فإن توقف الخادم أو البوت بقيت الصفقات المفتوحة بلا حماية، خاصة في الفيوتشرز.
