FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata لدعم المنطقة الزمنية عبر متغير TZ
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ننسخ ملفات البوت فقط (لا نسخ لملف .env ولا للمفاتيح داخل الصورة)
COPY main.py index.html index_lime.html ./
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["python", "main.py"]
