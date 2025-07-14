import logging
import re
import requests
from bs4 import BeautifulSoup
import time
import random
import os
from urllib.parse import urljoin
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

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

PORT = int(os.getenv('PORT', 5000))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Flask app cho health check
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
def health():
    return "OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

class TikTokDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.session.verify = False

    def clean_url(self, url):
        if url and ('tiktok.com' in url or 'douyin.com' in url):
            return url.split('?')[0]
        return None

    def resolve_short_url(self, short_url):
        try:
            return self.session.head(short_url, allow_redirects=True, timeout=15).url
        except Exception as e:
            logger.error(f"Error resolving short URL: {e}")
            return None

    def download_from_ssstik(self, tiktok_url):
        try:
            home = self.session.get('https://ssstik.io/', timeout=20)
            data = {'id': tiktok_url, 'locale': 'en', 'tt': 'bWF2aWE='}
            res = self.session.post('https://ssstik.io/abc', data=data, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')
            for link in soup.find_all('a', href=True):
                if link.get('href', '').startswith('http'):
                    return link['href'], None
            return None, "Không tìm thấy link từ SSSTik"
        except Exception as e:
            return None, f"SSSTik error: {e}"

    def download_from_tikmate(self, tiktok_url):
        try:
            res = self.session.post('https://tikmate.app/api/lookup', json={'url': tiktok_url}, timeout=20)
            data = res.json()
            video_url = data.get('video_url') or data.get('videoUrl') or data.get('url')
            if video_url:
                return video_url, None
            return None, "TikMate không trả về video"
        except Exception as e:
            return None, f"TikMate error: {e}"

    def download_from_snaptik(self, tiktok_url):
        try:
            home = self.session.get('https://snaptik.app/', timeout=20)
            soup = BeautifulSoup(home.text, 'html.parser')
            token = soup.find('input', {'name': 'token'}).get('value', '')
            res = self.session.post('https://snaptik.app/abc2.php', data={'url': tiktok_url, 'token': token}, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')
            for link in soup.find_all('a', href=True):
                if link.get('href', '').startswith('http'):
                    return link['href'], None
            return None, "Không tìm thấy link từ SnapTik"
        except Exception as e:
            return None, f"SnapTik error: {e}"

    def download_video_best(self, tiktok_url):
        tiktok_url = self.clean_url(tiktok_url)
        if not tiktok_url:
            return None, "URL TikTok không hợp lệ"

        if 'vm.tiktok.com' in tiktok_url or 'vt.tiktok.com' in tiktok_url:
            tiktok_url = self.resolve_short_url(tiktok_url) or tiktok_url

        services = [
            ("SSSTik", self.download_from_ssstik),
            ("TikMate", self.download_from_tikmate),
            ("SnapTik", self.download_from_snaptik),
        ]

        for name, func in services:
            video_url, error = func(tiktok_url)
            if video_url:
                return video_url, None
            logger.warning(f"{name} failed: {error}")
            time.sleep(random.uniform(1, 2))

        return None, "Tất cả dịch vụ đều thất bại"

downloader = TikTokDownloader()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Gửi link TikTok để tải video! 🚀")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆘 Gửi link TikTok vào đây, bot sẽ tải video cho bạn!")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - start_time
    await update.message.reply_text(f"🤖 Bot đang chạy!\n⏱ Uptime: {uptime}")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global request_count
    request_count += 1
    urls = re.findall(r'https?://[^\s]+', update.message.text)
    if not urls:
        return await update.message.reply_text("❌ Không tìm thấy link TikTok!")

    tiktok_url = urls[0]
    msg = await update.message.reply_text("🔄 Đang xử lý...")

    video_url, error = downloader.download_video_best(tiktok_url)
    if video_url:
        try:
            r = requests.get(video_url, stream=True, timeout=60)
            video = BytesIO(r.content)
            video.name = 'video.mp4'
            await context.bot.send_video(chat_id=update.effective_chat.id, video=video)
        except Exception as e:
            await msg.edit_text(f"❌ Lỗi gửi video: {e}")
    else:
        await msg.edit_text(f"❌ Không thể tải video: {error}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    application.add_error_handler(error_handler)

    logger.info("🚀 Bot đang chạy...")
    application.run_polling()

if __name__ == '__main__':
    main()
