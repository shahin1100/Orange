FROM python:3.11-slim

WORKDIR /app

# ডিপেন্ডেন্সি ইন্সটল
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright ব্রাউজার ইন্সটল
RUN playwright install chromium
RUN playwright install-deps

# কোড কপি
COPY bot.py .

# বট চালান
CMD ["python", "bot.py"]