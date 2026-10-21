# التثبيت على VPS عبر GitHub

الفكرة: ترفع ملفات البوت إلى مستودع GitHub **خاص (Private)**، ثم تسحبها على الخادم بـ `git clone`. المفاتيح لا تدخل GitHub أبداً، تبقى في ملف `.env` على الخادم فقط.

## 1) أنشئ المستودع على GitHub
1. ادخل إلى github.com ← **New repository**.
2. الاسم مثلاً `crypto-bot`، واختر **Private**.
3. **لا** تضف README ولا .gitignore ولا ترخيصاً (اتركه فارغاً)، ثم Create.
4. انسخ رابط المستودع، يشبه: `https://github.com/USERNAME/crypto-bot.git`

## 2) ارفع الملفات من جهازك (ويندوز)
ثبّت Git إن لم يكن عندك (PowerShell): `winget install --id Git.Git -e` ثم أغلق PowerShell وافتحه من جديد.

فك ضغط `crypto-bot-vps.zip`، وادخل إلى مجلد `crypto-bot` الناتج:
```powershell
cd C:\Users\amalp\Pictures\crypto-bot-vps\crypto-bot
git init
git add .
git status
```
**تحقق من نتيجة `git status`:** يجب ألا يظهر فيها `.env` ولا `_env` ولا أي ملف `.db`. تظهر فقط ملفات مثل `main.py` و`Dockerfile` و`.env.example`. إن ظهر ملف فيه مفاتيح فأوقف الأمر ولا تكمل.

ثم:
```powershell
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/USERNAME/crypto-bot.git
git push -u origin main
```
(سيفتح المتصفح لتسجيل الدخول إلى GitHub أول مرة.)

> لا تضع مجلدك القديم الذي فيه `.env` الحقيقي داخل المستودع. استخدم مجلد الحزمة الجديد النظيف.

## 3) أعطِ الخادم صلاحية القراءة (Deploy Key)
على الخادم (بعد `ssh root@SERVER_IP`):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/crypto_bot_deploy -N ""
cat ~/.ssh/crypto_bot_deploy.pub
```
انسخ السطر الذي طُبع (يبدأ بـ `ssh-ed25519`). في GitHub: المستودع ← **Settings** ← **Deploy keys** ← **Add deploy key** ← الصق المفتاح، وسمّه `hostinger`، واترك **Allow write access غير مفعّل** (قراءة فقط) ← Add key.

ثم اضبط SSH ليستخدم هذا المفتاح لـ GitHub:
```bash
cat >> ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/crypto_bot_deploy
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
ssh -T git@github.com
```
أجب بـ `yes` عند السؤال. يجب أن ترى رسالة ترحيب فيها اسم المستودع.

## 4) اسحب البوت وشغّله على الخادم
```bash
cd /opt
git clone git@github.com:USERNAME/crypto-bot.git
cd crypto-bot
cp .env.example .env
nano .env
chmod 600 .env
docker compose up -d --build
docker logs -f telegram_crypto_bot
```
في `nano` عبّئ المفاتيح (مفاتيح **جديدة**) وأبقِ `BINANCE_ENV=demo`. وإن لم يعمل `docker compose` فاستخدم `docker-compose`.

## 5) التحديثات لاحقاً
على جهازك بعد أي تعديل:
```powershell
git add .
git commit -m "وصف التعديل"
git push
```
على الخادم:
```bash
cd /opt/crypto-bot
bash update.sh
```
السكربت يسحب آخر نسخة ويعيد بناء الحاوية. ملف `.env` ومجلد `data` لا يتأثران.

## ملاحظات
- لو جعلت المستودع **Public** فلن تحتاج Deploy Key (`git clone https://github.com/USERNAME/crypto-bot.git`)، لكن كودك واستراتيجيتك تصبح مكشوفة. أنصح بـ Private.
- إن حدث خطأ `Permission denied (publickey)` عند السحب فالمفتاح لم يُضَف لهذا المستودع تحديداً، أو لم تُنفَّذ خطوة ضبط `~/.ssh/config`.
- إن أردت أن يتحدث الخادم تلقائياً عند كل `git push` (GitHub Actions)، يمكن إعداده لاحقاً، لكنه يحتاج مفتاح SSH إضافياً كسرّ في GitHub، فاتركه حتى يستقر البوت.
