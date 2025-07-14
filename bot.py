import logging
import re
import requests
from bs4 import BeautifulSoup
import json
import time
import random
import os
from urllib.parse import quote, urljoin
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio
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

# Lấy token từ environment variable
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

# Port cho health check
PORT = int(os.getenv('PORT', 5000))

# User agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Flask app cho health check
app = Flask(__name__)

# Biến để theo dõi uptime
start_time = datetime.now()
request_count = 0

@app.route('/')
def health_check():
    """Health check endpoint"""
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
    """Alternative health check endpoint"""
    return "OK", 200

@app.route('/ping')
def ping():
    """Ping endpoint for uptime monitoring"""
    return "pong", 200

class TikTokDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        # Tắt SSL verification để tránh lỗi
        self.session.verify = False
        
        # Timeout settings
        self.session.timeout = 30

    def clean_url(self, url):
        """Làm sạch URL TikTok"""
        if not url:
            return None
        
        # Loại bỏ tham số không cần thiết
        url = url.split('?')[0]
        
        # Kiểm tra định dạng URL hợp lệ
        if 'tiktok.com' in url or 'douyin.com' in url:
            return url
        return None

    def resolve_short_url(self, short_url):
        """Giải quyết URL ngắn"""
        try:
            response = self.session.head(short_url, allow_redirects=True, timeout=15)
            return response.url
        except Exception as e:
            logger.error(f"Error resolving short URL: {e}")
            return None

    def download_from_ssstik(self, tiktok_url):
        """Download từ SSSTwitter/SSSInstagram"""
        try:
            headers = {
                'User-Agent': USER_AGENT,
                'Referer': 'https://ssstik.io/',
                'Origin': 'https://ssstik.io',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            
            # Lấy trang chủ để lấy token
            try:
                home = self.session.get('https://ssstik.io/', headers=headers, timeout=20)
                home.raise_for_status()
            except Exception as e:
                return None, f"Không thể truy cập SSSTwitter: {e}"
            
            # Gửi form
            data = {
                'id': tiktok_url,
                'locale': 'en',
                'tt': 'bWF2aWE='
            }
            
            response = self.session.post('https://ssstik.io/abc', 
                                       headers=headers, data=data, timeout=20)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Tìm link download
                download_links = soup.find_all('a', href=True)
                for link in download_links:
                    href = link.get('href', '')
                    text = link.get_text().lower()
                    if href.startswith('http') and ('download' in text or 'quality' in text or 'without watermark' in text):
                        return href, None
                
                # Tìm trong script tags
                script_tags = soup.find_all('script')
                for script in script_tags:
                    if script.string:
                        video_match = re.search(r'(https?://[^\s"\']+\.mp4[^\s"\']*)', script.string)
                        if video_match:
                            return video_match.group(1), None
            
            return None, "Không tìm thấy link download từ SSSTwitter"
            
        except Exception as e:
            logger.error(f"SSSTwitter error: {e}")
            return None, f"SSSTwitter error: {e}"

    def download_from_tikmate(self, tiktok_url):
        """Download từ TikMate"""
        try:
            headers = {
                'User-Agent': USER_AGENT,
                'Referer': 'https://tikmate.app/',
                'Accept': 'application/json, text/plain, */*',
                'Content-Type': 'application/json',
                'Origin': 'https://tikmate.app',
            }
            
            # Thử API endpoint
            api_data = {'url': tiktok_url}
            
            response = self.session.post('https://tikmate.app/api/lookup', 
                                       headers=headers, json=api_data, timeout=20)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    
                    # Tìm video URL từ response
                    video_url = None
                    if data.get('success') and data.get('video_url'):
                        video_url = data['video_url']
                    elif data.get('videoUrl'):
                        video_url = data['videoUrl']
                    elif data.get('url'):
                        video_url = data['url']
                    elif data.get('result') and isinstance(data['result'], dict):
                        result = data['result']
                        video_url = result.get('video_url') or result.get('videoUrl')
                    
                    if video_url and video_url.startswith('http'):
                        return video_url, None
                        
                except ValueError as e:
                    return None, f"JSON decode error: {e}"
            
            return None, "TikMate không trả về video"
            
        except Exception as e:
            logger.error(f"TikMate error: {e}")
            return None, f"TikMate error: {e}"

    def download_from_snaptik(self, tiktok_url):
        """Download từ SnapTik"""
        try:
            headers = {
                'User-Agent': USER_AGENT,
                'Referer': 'https://snaptik.app/',
                'Origin': 'https://snaptik.app',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            
            # Lấy trang chủ
            home = self.session.get('https://snaptik.app/', headers=headers, timeout=20)
            soup = BeautifulSoup(home.text, 'html.parser')
            
            # Tìm token
            token_input = soup.find('input', {'name': 'token'})
            if not token_input:
                token_input = soup.find('input', {'type': 'hidden'})
            
            token = token_input.get('value') if token_input else ''
            
            # Gửi form
            data = {
                'url': tiktok_url,
                'token': token
            }
            
            response = self.session.post('https://snaptik.app/abc2.php', 
                                       headers=headers, data=data, timeout=20)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Tìm link download
                download_links = soup.find_all('a', href=True)
                for link in download_links:
                    href = link.get('href', '')
                    text = link.get_text().lower()
                    if href.startswith('http') and ('download' in text or 'server' in text):
                        return href, None
            
            return None, "Không tìm thấy link download từ SnapTik"
            
        except Exception as e:
            logger.error(f"SnapTik error: {e}")
            return None, f"SnapTik error: {e}"

    def download_video_best(self, tiktok_url):
        """Thử download từ nhiều nguồn"""
        
        # Làm sạch URL
        tiktok_url = self.clean_url(tiktok_url)
        if not tiktok_url:
            return None, "URL TikTok không hợp lệ"
        
        # Giải quyết URL ngắn
        if 'vt.tiktok.com' in tiktok_url or 'vm.tiktok.com' in tiktok_url:
            logger.info("Đang giải quyết URL ngắn...")
            full_url = self.resolve_short_url(tiktok_url)
            if full_url:
                tiktok_url = full_url
                logger.info(f"URL đầy đủ: {tiktok_url}")
        
        # Danh sách các service
        services = [
            ("SSSTwitter", self.download_from_ssstik),
            ("TikMate", self.download_from_tikmate),
            ("SnapTik", self.download_from_snaptik),
        ]
        
        errors = []
        
        for service_name, service_func in services:
            try:
                logger.info(f"Đang thử {service_name}...")
                video_url, error = service_func(tiktok_url)
                
                if video_url and video_url.startswith('http'):
                    logger.info(f"✅ Thành công với {service_name}")
                    return video_url, None
                else:
                    errors.append(f"{service_name}: {error}")
                    logger.warning(f"❌ {service_name} thất bại: {error}")
                
                # Delay để tránh rate limit
                time.sleep(random.uniform(1, 3))
                
            except Exception as e:
                errors.append(f"{service_name}: {str(e)}")
                logger.error(f"Lỗi {service_name}: {e}")
        
        return None, "Tất cả services đều thất bại. " + "; ".join(errors)

# Khởi tạo downloader
downloader = TikTokDownloader()

# Bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /start"""
    welcome_message = """
🎬 **TikTok Video Downloader Bot**

Chào mừng bạn! Tôi có thể tải video TikTok không watermark cho bạn.

**Cách sử dụng:**
• Gửi link TikTok vào chat
• Tôi sẽ tải video và gửi lại cho bạn
• Hỗ trợ cả link ngắn và link đầy đủ

**Lệnh:**
/start - Hiển thị menu này
/help - Hướng dẫn sử dụng
/status - Kiểm tra trạng thái bot

Hãy thử gửi một link TikTok! 🚀
    """
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /help"""
    help_message = """
🆘 **Hướng dẫn sử dụng**

1. **Gửi link TikTok:**
   • Link đầy đủ: https://www.tiktok.com/@username/video/1234567890
   • Link ngắn: https://vm.tiktok.com/abcdef

2. **Bot sẽ:**
   • Tải video không watermark
   • Gửi video chất lượng tốt nhất
   • Thông báo nếu có lỗi

3. **Lưu ý:**
   • Video phải là public
   • Bot có thể mất vài giây để xử lý
   • Nếu lỗi, hãy thử lại sau

**Hỗ trợ:** Liên hệ admin nếu có vấn đề!
    """
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /status"""
    uptime = datetime.now() - start_time
    status_message = f"""
🤖 **Trạng thái Bot**

• **Trạng thái:** Đang hoạt động ✅
• **Uptime:** {uptime}
• **Requests đã xử lý:** {request_count}
• **Thời gian:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Bot đang hoạt động bình thường! 🚀
    """
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý download video"""
    global request_count
    request_count += 1
    
    user_message = update.message.text
    
    # Kiểm tra xem có phải link TikTok không
    if not ('tiktok.com' in user_message or 'douyin.com' in user_message):
        await update.message.reply_text("❌ Vui lòng gửi link TikTok hợp lệ!")
        return
    
    # Trích xuất URL từ message
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, user_message)
    
    if not urls:
        await update.message.reply_text("❌ Không tìm thấy link trong tin nhắn!")
        return
    
    tiktok_url = urls[0]
    
    # Gửi thông báo đang xử lý
    processing_message = await update.message.reply_text("🔄 Đang xử lý video, vui lòng chờ...")
    
    try:
        # Download video
        video_url, error = downloader.download_video_best(tiktok_url)
        
        if video_url:
            try:
                # Cập nhật thông báo
                await processing_message.edit_text("📥 Đang tải video...")
                
                # Tải video về bằng requests với streaming
                video_response = requests.get(
                    video_url, 
                    headers={'User-Agent': USER_AGENT}, 
                    timeout=90, 
                    verify=False, 
                    stream=True
                )
                
                if video_response.status_code == 200:
                    # Đọc video theo chunks để tránh memory overflow
                    video_data = BytesIO()
                    total_size = 0
                    max_size = 50 * 1024 * 1024  # 50MB limit
                    
                    for chunk in video_response.iter_content(chunk_size=8192):
                        if chunk:
                            total_size += len(chunk)
                            if total_size > max_size:
                                await processing_message.edit_text("❌ Video quá lớn (>50MB), không thể gửi qua Telegram!")
                                return
                            video_data.write(chunk)
                    
                    video_data.seek(0)
                    
                    # Cập nhật thông báo
                    await processing_message.edit_text("📤 Đang gửi video...")
                    
                    # Gửi video
                    video_data.name = 'tiktok_video.mp4'
                    
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video_data,
                        caption="✅ Video TikTok đã tải thành công!\n🎬 Không có watermark",
                        supports_streaming=True,
                        read_timeout=90,
                        write_timeout=90
                    )
                    
                    # Xóa thông báo xử lý
                    await processing_message.delete()
                    
                else:
                    await processing_message.edit_text(f"❌ Lỗi tải video: HTTP {video_response.status_code}")
                            
            except Exception as e:
                logger.error(f"Error sending video: {e}")
                await processing_message.edit_text(f"❌ Lỗi gửi video: {str(e)}")
                
        else:
            await processing_message.edit_text(f"❌ Không thể tải video!\n\n**Chi tiết:** {error}")
            
    except Exception as e:
        logger.error(f"Error in download_video: {e}")
        await processing_message.edit_text(f"❌ Lỗi hệ thống: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lỗi"""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.message:
        try:
            await update.message.reply_text("❌ Đã xảy ra lỗi! Vui lòng thử lại sau.")
        except:
            pass

def run_flask():
    """Chạy Flask server cho health check"""
    app.run(host='0.0.0.0', port=PORT, debug=False)

def main():
    """Chạy bot"""
    try:
        # Chạy Flask server trong thread riêng
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        # Tạo application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Thêm handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
        
        # Thêm error handler
        application.add_error_handler(error_handler)
        
        # Chạy bot
        logger.info("🚀 Bot đang chạy...")
        logger.info(f"🌐 Health check server running on port {PORT}")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"❌ Lỗi khởi động bot: {e}")

if __name__ == '__main__':
    main()