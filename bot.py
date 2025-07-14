import logging
import re
import requests
from bs4 import BeautifulSoup
import time
import random
import os
from urllib.parse import quote, urljoin
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from io import BytesIO
import warnings
import urllib3
from flask import Flask, jsonify
import threading
from datetime import datetime

# Tắt warnings
warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

PORT = int(os.getenv('PORT', 5000))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Flask app
app = Flask(__name__)
start_time = datetime.now()
request_count = 0

@app.route('/')
def health_check():
    uptime = datetime.now() - start_time
    return jsonify({
        'status': 'healthy',
        'bot': 'TikTok Downloader Bot',
        'uptime': str(uptime),
        'requests_processed': request_count,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health(): return "OK", 200

@app.route('/ping')
def ping(): return "pong", 200

class TikTokDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': '*/*'
        })
        self.session.verify = False

    def clean_url(self, url):
        if not url: return None
        url = url.split('?')[0]
        return url if 'tiktok.com' in url or 'douyin.com' in url else None

    def resolve_short_url(self, short_url):
        try:
            resp = self.session.head(short_url, allow_redirects=True, timeout=15)
            return resp.url
        except Exception as e:
            logger.error(f"Error resolving short URL: {e}")
            return None

    def download_from_ssstik(self, url):
        try:
            home = self.session.get('https://ssstik.io/', timeout=15)
            data = {'id': url, 'locale': 'en', 'tt': 'bWF2aWE='}
            resp = self.session.post('https://ssstik.io/abc', data=data, timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                if a['href'].startswith('http') and 'download' in a.text.lower():
                    return a['href'], None
            return None, "Không tìm thấy link download từ SSSTik"
        except Exception as e:
            return None, str(e)

    def download_from_tikmate(self, url):
        try:
            resp = self.session.post('https://tikmate.app/api/lookup', json={'url': url}, timeout=20)
            data = resp.json()
            video_url = data.get('video_url') or data.get('videoUrl') or data.get('url')
            return (video_url, None) if video_url else (None, "TikMate không trả về video")
        except Exception as e:
            return None, str(e)

    def download_from_snaptik(self, url):
        try:
            home = self.session.get('https://snaptik.app/', timeout=15)
            soup = BeautifulSoup(home.text, 'html.parser')
            token = soup.find('input', {'name': 'token'}).get('value')
            resp = self.session.post('https://snaptik.app/abc2.php', data={'url': url, 'token': token}, timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                if a['href'].startswith('http') and 'download' in a.text.lower():
                    return a['href'], None
            return None, "Không tìm thấy link download từ SnapTik"
        except Exception as e:
            return None, str(e)

    def download_video_best(self, url):
        url = self.clean_url(url)
        if not url: return None, "URL TikTok không hợp lệ"
        if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
            resolved = self.resolve_short_url(url)
            url = resolved if resolved else url
        for name, func in [
            ("SSSTik", self.download_from_ssstik),
            ("TikMate", self.download_from_tikmate),
            ("SnapTik", self.download_from_snaptik)
        ]:
            logger.info(f"Đang thử {name}...")
            video_url, error = func(url)
            if video_url: return video_url, None
            logger.warning(f"{name} thất bại: {error}")
            time.sleep(random.uniform(1,2))
        return None, "Tất cả dịch vụ đều thất bại"

downloader = TikTokDownloader()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Chào mừng! Gửi link TikTok để tải video không watermark.")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global request_count
    request_count += 1
    urls = re.findall(r'https?://[^\s]+', update.message.text)
    if not urls:
        await update.message.reply_text("❌ Không tìm thấy link TikTok!")
        return
    processing = await update.message.reply_text("🔄 Đang xử lý...")
    video_url, error = downloader.download_video_best(urls[0])
    if video_url:
        r = requests.get(video_url, headers={'User-Agent': USER_AGENT}, stream=True, timeout=90, verify=False)
        if r.status_code == 200:
            bio = BytesIO(r.content)
            bio.name = 'video.mp4'
            await context.bot.send_video(chat_id=update.effective_chat.id, video=bio)
            await processing.delete()
        else:
            await processing.edit_text(f"❌ Lỗi tải video: HTTP {r.status_code}")
    else:
        await processing.edit_text(f"❌ Không thể tải video: {error}")

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False)

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    logger.info("🚀 Bot đang chạy...")
    app_bot.run_polling()

if __name__ == '__main__':
    main()
