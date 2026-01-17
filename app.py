from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import yt_dlp
from werkzeug.utils import secure_filename
import tempfile
import shutil
import threading
import time
import uuid
import re
from datetime import datetime, timedelta
import logging
from urllib.parse import urlparse, parse_qs
import mimetypes

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())  # For CSRF protection

# Enable CORS for React dev server - allow all routes since we don't use /api prefix
CORS(app, resources={r"/*": {"origins": ["http://localhost:5173", "http://localhost:3000"]}})

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Security functions (defined early for use throughout the app)
def validate_file_type(file_path):
    """Validate that file is a valid video/audio file"""
    allowed_extensions = {'.mp4', '.webm', '.mkv', '.m4a', '.mp3', '.ogg', '.flac', '.wav', '.avi', '.mov', '.flv'}
    allowed_mime_types = {
        'video/mp4', 'video/webm', 'video/x-matroska', 'video/quicktime',
        'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/flac', 'audio/wav',
        'video/x-msvideo', 'video/x-flv'
    }
    
    # Check extension
    _, ext = os.path.splitext(file_path.lower())
    if ext not in allowed_extensions:
        return False
    
    # Check MIME type
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type and mime_type not in allowed_mime_types:
        return False
    
    return True


def log_security_event(event_type, details, client_ip=None):
    """Log security-related events"""
    try:
        if not client_ip:
            from flask import request
            client_ip = get_client_ip() if hasattr(request, 'remote_addr') else 'unknown'
        
        logger.warning(f"SECURITY EVENT [{event_type}] from {client_ip}: {details}")
        # In production, you might want to send this to a security monitoring system
    except Exception as e:
        logger.error(f"Error logging security event: {e}")


def get_client_ip_for_limiter():
    """Get client IP for rate limiting, handling proxy headers"""
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip
    return request.remote_addr or 'unknown'

# File cleanup configuration - MUST be defined before get_downloads_folder() is called
FILE_CLEANUP_ENABLED = os.environ.get('FILE_CLEANUP_ENABLED', 'true').lower() == 'true'
FILE_MAX_AGE_HOURS = int(os.environ.get('FILE_MAX_AGE_HOURS', '1'))  # Default: 1 hour (aggressive cleanup for Railway)
MAX_STORAGE_GB = float(os.environ.get('MAX_STORAGE_GB', '2.0'))  # Default: 2GB (conservative for Railway)
AUTO_DELETE_AFTER_DOWNLOAD = os.environ.get('AUTO_DELETE_AFTER_DOWNLOAD', 'true').lower() == 'true'  # Delete file after user downloads it
USE_EPHEMERAL_STORAGE = os.environ.get('USE_EPHEMERAL_STORAGE', 'true').lower() == 'true'  # Use /tmp for cloud (cleared on restart)

# Rate limiting configuration
MAX_DOWNLOADS_PER_HOUR = int(os.environ.get('MAX_DOWNLOADS_PER_HOUR', '10'))  # Max downloads per IP per hour
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', '3'))  # Max concurrent downloads per IP

# File size limits
MAX_FILE_SIZE_GB = float(os.environ.get('MAX_FILE_SIZE_GB', '2.0'))  # Max file size per download (2GB default)
MAX_FILE_SIZE_BYTES = int(MAX_FILE_SIZE_GB * 1024 * 1024 * 1024)

# Download speed throttling (optional, in MB/s, 0 = no throttling)
DOWNLOAD_SPEED_THROTTLE_MB = float(os.environ.get('DOWNLOAD_SPEED_THROTTLE_MB', '0'))  # 0 = unlimited

# Get the user's Downloads folder (works on Windows, macOS, and Linux)
# For cloud deployment, use a downloads directory in the app folder
def get_downloads_folder():
    """Get the base Downloads folder path with cloud deployment support"""
    import platform
    
    # Check if we're in a cloud environment (Railway, Heroku, etc.)
    # Use environment variable or default to ephemeral storage
    cloud_downloads = os.environ.get('DOWNLOADS_DIR')
    if cloud_downloads:
        try:
            # Ensure the directory exists and is writable
            os.makedirs(cloud_downloads, exist_ok=True)
            # Test write permissions
            test_file = os.path.join(cloud_downloads, '.write_test')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.info(f"Using cloud downloads directory: {cloud_downloads}")
                return cloud_downloads
            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot write to DOWNLOADS_DIR {cloud_downloads}: {e}. Using fallback.")
        except Exception as e:
            logger.warning(f"Error setting up DOWNLOADS_DIR {cloud_downloads}: {e}. Using fallback.")
    
    # Check if we're in a cloud environment (Railway sets RAILWAY_ENVIRONMENT)
    # or if we're in a container (common cloud indicator)
    is_cloud = (
        os.environ.get('RAILWAY_ENVIRONMENT') or 
        os.environ.get('DYNO') or  # Heroku
        os.environ.get('VERCEL') or  # Vercel
        os.path.exists('/.dockerenv')  # Docker container
    )
    
    if is_cloud:
        # For cloud: use ephemeral storage (/tmp) by default to save storage costs
        # Files in /tmp are cleared on container restart, saving Railway storage
        if USE_EPHEMERAL_STORAGE:
            tmp_downloads = '/tmp/downloads'
            try:
                os.makedirs(tmp_downloads, exist_ok=True)
                logger.info(f"Using ephemeral storage: {tmp_downloads} (files cleared on restart)")
                return tmp_downloads
            except Exception as e:
                logger.error(f"Cannot create /tmp/downloads: {e}")
        
        # Fallback to app directory if ephemeral storage disabled
        app_dir = os.path.dirname(os.path.abspath(__file__))
        cloud_default = os.path.join(app_dir, 'downloads')
        try:
            os.makedirs(cloud_default, exist_ok=True)
            logger.info(f"Using persistent cloud downloads directory: {cloud_default}")
            return cloud_default
        except Exception as e:
            logger.error(f"Cannot create cloud downloads directory {cloud_default}: {e}")
            # Last resort: use /tmp
            tmp_downloads = '/tmp/downloads'
            os.makedirs(tmp_downloads, exist_ok=True)
            logger.warning(f"Using /tmp/downloads as fallback")
            return tmp_downloads
    
    # For local development, use user's Downloads folder
    home = os.path.expanduser('~')
    
    if platform.system() == 'Windows':
        # Windows: C:\Users\Username\Downloads
        downloads = os.path.join(home, 'Downloads')
    elif platform.system() == 'Darwin':  # macOS
        # macOS: /Users/Username/Downloads
        downloads = os.path.join(home, 'Downloads')
    else:  # Linux
        # Linux: /home/username/Downloads
        downloads = os.path.join(home, 'Downloads')
    
    return downloads

def get_client_ip():
    """Get client IP address, handling proxy headers for cloud deployments"""
    # Check for X-Forwarded-For header (used by Railway, Heroku, etc.)
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        client_ip = forwarded_for.split(',')[0].strip()
        logger.debug(f"Using X-Forwarded-For IP: {client_ip}")
        return client_ip
    
    # Check for X-Real-IP header (alternative proxy header)
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        logger.debug(f"Using X-Real-IP: {real_ip}")
        return real_ip
    
    # Fallback to remote_addr
    client_ip = request.remote_addr
    logger.debug(f"Using remote_addr: {client_ip}")
    return client_ip

def get_client_downloads_folder(client_ip):
    """Get downloads folder for a specific client IP address with error handling"""
    try:
        base_downloads = get_downloads_folder()
        # Ensure base directory exists (important for cloud deployments)
        os.makedirs(base_downloads, exist_ok=True)
        
        # Create folder structure: Downloads/kids/{client_ip}/
        # Replace dots and colons in IP for folder name safety
        safe_ip = client_ip.replace('.', '_').replace(':', '_') if client_ip else 'unknown'
        client_folder = os.path.join(base_downloads, 'kids', safe_ip)
        
        # Create directory with error handling
        try:
            os.makedirs(client_folder, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.error(f"Cannot create client folder {client_folder}: {e}")
            # Fallback to base directory
            return base_downloads
        
        return client_folder
    except Exception as e:
        logger.error(f"Error getting client downloads folder: {e}")
        # Last resort: use temp directory
        temp_dir = tempfile.gettempdir()
        fallback = os.path.join(temp_dir, 'downloads')
        os.makedirs(fallback, exist_ok=True)
        return fallback

# Base downloads directory (for backward compatibility)
# Ensure it exists (important for cloud deployments)
BASE_DOWNLOADS_DIR = get_downloads_folder()
os.makedirs(BASE_DOWNLOADS_DIR, exist_ok=True)

# Download queue and status tracking
download_queue = []
download_status = {}  # {job_id: {'status': 'queued'|'downloading'|'paused'|'completed'|'failed', 'progress': 0-100, 'title': '', 'error': '', 'source_url': ''}}
paused_jobs = set()  # Set of job_ids that are paused
active_downloads = {}  # {job_id: thread} - track active download threads for cancellation
queue_lock = threading.Lock()
processing = False

# Download history/analytics tracking
download_history = []  # List of completed/failed downloads for analytics
history_lock = threading.Lock()
MAX_HISTORY_SIZE = 1000  # Keep last 1000 downloads in history

# Rate limiting tracking
download_counts = {}  # {client_ip: {'count': int, 'reset_time': timestamp}}
concurrent_downloads = {}  # {client_ip: int} - track concurrent downloads per IP
rate_limit_lock = threading.Lock()


def cleanup_old_files(downloads_dir=None, max_age_hours=None, max_storage_gb=None):
    """Clean up old files from downloads directory
    
    Args:
        downloads_dir: Directory to clean (defaults to BASE_DOWNLOADS_DIR)
        max_age_hours: Maximum age in hours (defaults to FILE_MAX_AGE_HOURS)
        max_storage_gb: Maximum storage in GB (defaults to MAX_STORAGE_GB)
    """
    if not FILE_CLEANUP_ENABLED:
        return
    
    try:
        if downloads_dir is None:
            downloads_dir = BASE_DOWNLOADS_DIR
        if max_age_hours is None:
            max_age_hours = FILE_MAX_AGE_HOURS
        if max_storage_gb is None:
            max_storage_gb = MAX_STORAGE_GB
        
        if not os.path.exists(downloads_dir):
            return
        
        max_age_seconds = max_age_hours * 3600
        max_storage_bytes = max_storage_gb * 1024 * 1024 * 1024
        current_time = time.time()
        
        # Collect all files recursively
        files_to_check = []
        total_size = 0
        
        for root, dirs, files in os.walk(downloads_dir):
            for file in files:
                # Skip .part files (incomplete downloads)
                if file.endswith('.part'):
                    continue
                
                file_path = os.path.join(root, file)
                try:
                    file_stat = os.stat(file_path)
                    file_age = current_time - file_stat.st_mtime
                    file_size = file_stat.st_size
                    
                    files_to_check.append({
                        'path': file_path,
                        'age': file_age,
                        'size': file_size,
                        'mtime': file_stat.st_mtime
                    })
                    total_size += file_size
                except (OSError, PermissionError) as e:
                    logger.warning(f"Cannot access file {file_path}: {e}")
                    continue
        
        # Sort by age (oldest first)
        files_to_check.sort(key=lambda x: x['mtime'])
        
        deleted_count = 0
        deleted_size = 0
        
        # Delete files older than max_age
        for file_info in files_to_check:
            if file_info['age'] > max_age_seconds:
                try:
                    os.remove(file_info['path'])
                    deleted_count += 1
                    deleted_size += file_info['size']
                    total_size -= file_info['size']
                    logger.info(f"Deleted old file: {file_info['path']} (age: {file_info['age']/3600:.1f}h)")
                except (OSError, PermissionError) as e:
                    logger.warning(f"Cannot delete file {file_info['path']}: {e}")
        
        # If still over storage limit, delete oldest files
        if total_size > max_storage_bytes:
            for file_info in files_to_check:
                if total_size <= max_storage_bytes:
                    break
                if os.path.exists(file_info['path']):
                    try:
                        os.remove(file_info['path'])
                        deleted_count += 1
                        deleted_size += file_info['size']
                        total_size -= file_info['size']
                        logger.info(f"Deleted file for storage limit: {file_info['path']}")
                    except (OSError, PermissionError) as e:
                        logger.warning(f"Cannot delete file {file_info['path']}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleanup completed: deleted {deleted_count} files, freed {deleted_size/(1024*1024):.2f} MB")
    
    except Exception as e:
        logger.error(f"Error during file cleanup: {e}")


def cleanup_worker():
    """Background worker thread for periodic file cleanup"""
    while True:
        try:
            time.sleep(3600)  # Run every hour
            cleanup_old_files()
        except Exception as e:
            logger.error(f"Error in cleanup worker: {e}")
            time.sleep(60)  # Wait 1 minute before retrying


# Start cleanup worker thread
if FILE_CLEANUP_ENABLED:
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    logger.info(f"File cleanup enabled: max age {FILE_MAX_AGE_HOURS}h, max storage {MAX_STORAGE_GB}GB")
    logger.info(f"Auto-delete after download: {AUTO_DELETE_AFTER_DOWNLOAD}")
    logger.info(f"Ephemeral storage: {USE_EPHEMERAL_STORAGE}")


def _get_format_selector(quality):
    """Get format selector string based on quality preference"""
    if quality == 'best':
        return 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    else:
        return 'worst[ext=mp4]/worst'


def _get_client_headers(client):
    """Get appropriate headers for each client type"""
    headers = {
        'ios': {
            'User-Agent': 'com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
        },
        'android': {
            'User-Agent': 'com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
        },
        'tv': {
            'User-Agent': 'Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version',
            'Accept': '*/*',
            'Accept-Language': 'en-US',
        },
        'web': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
        }
    }
    return headers.get(client, headers['web'])


def _find_downloaded_file(video_title, downloads_dir):
    """Find the downloaded file by matching title in the specified downloads directory"""
    safe_title = secure_filename(video_title[:50])
    
    if not os.path.exists(downloads_dir):
        return None
    
    files = os.listdir(downloads_dir)
    
    # Try exact match first
    for file in files:
        if file.startswith(safe_title):
            return file
    
    # Try partial match
    for file in files:
        if safe_title.lower() in file.lower():
            return file
    
    # Get most recently modified file
    if files:
        files_with_paths = [(f, os.path.getmtime(os.path.join(downloads_dir, f))) 
                           for f in files if os.path.isfile(os.path.join(downloads_dir, f))]
        if files_with_paths:
            files_with_paths.sort(key=lambda x: x[1], reverse=True)
            return files_with_paths[0][0]
    
    return None


class ConcurrentDownloadTracker:
    """Context manager to track concurrent downloads and ensure cleanup"""
    def __init__(self, client_ip):
        self.client_ip = client_ip
        self.incremented = False
    
    def __enter__(self):
        if self.client_ip:
            with rate_limit_lock:
                concurrent_downloads[self.client_ip] = concurrent_downloads.get(self.client_ip, 0) + 1
                self.incremented = True
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.client_ip and self.incremented:
            with rate_limit_lock:
                if self.client_ip in concurrent_downloads:
                    concurrent_downloads[self.client_ip] = max(0, concurrent_downloads[self.client_ip] - 1)
        return False  # Don't suppress exceptions


class ProgressHook:
    """Hook to track download progress"""
    def __init__(self, job_id):
        self.job_id = job_id
        self.downloaded_bytes = 0
        self.total_bytes = None
        self.last_update = 0
        
    def hook(self, d):
        current_time = time.time()
        # Update at least every 0.5 seconds for smoother progress
        if current_time - self.last_update < 0.5 and d['status'] == 'downloading':
            return
        
        self.last_update = current_time
        
        if d['status'] == 'downloading':
            if 'total_bytes' in d and d['total_bytes']:
                self.total_bytes = d['total_bytes']
            if 'downloaded_bytes' in d:
                self.downloaded_bytes = d['downloaded_bytes']
            
            if self.total_bytes and self.total_bytes > 0:
                progress = min(99, int((self.downloaded_bytes / self.total_bytes) * 100))
            elif 'speed' in d and d['speed']:
                # Estimate progress based on download speed (rough estimate)
                progress = min(50, self.downloaded_bytes // 1000000)  # 1MB = ~1%
            else:
                progress = min(10, self.downloaded_bytes // 5000000)  # Very rough estimate
            
            with queue_lock:
                if self.job_id in download_status:
                    download_status[self.job_id]['progress'] = progress
                    download_status[self.job_id]['status'] = 'downloading'
                    # Add speed info if available
                    if 'speed' in d:
                        speed_mb = d['speed'] / (1024 * 1024) if d['speed'] else 0
                        download_status[self.job_id]['speed'] = f'{speed_mb:.2f} MB/s'
        elif d['status'] == 'finished':
            with queue_lock:
                if self.job_id in download_status:
                    download_status[self.job_id]['progress'] = 99  # Almost done, will be 100 when file is found
                    download_status[self.job_id]['status'] = 'downloading'


def download_video(job_id, url, quality='best', client_ip=None, format_id=None, throttle_speed=None):
    """
    Download video from YouTube URL using multiple fallback strategies.
    Updates download_status dict with progress.
    Supports pause/resume and network interruption recovery.
    
    Args:
        job_id: Unique job identifier
        url: YouTube URL to download
        quality: Video quality ('best' or 'worst')
        client_ip: IP address of the client requesting the download
        format_id: Specific format ID to download (optional)
        throttle_speed: Download speed limit in MB/s (optional, None = use global setting)
    """
    # Use context manager to track concurrent downloads and ensure cleanup on all exit paths
    with ConcurrentDownloadTracker(client_ip):
        try:
            # Get client-specific downloads folder with error handling
            if client_ip:
                downloads_dir = get_client_downloads_folder(client_ip)
            else:
                # Fallback to default folder for backward compatibility
                downloads_dir = os.path.join(BASE_DOWNLOADS_DIR, 'kids')
                try:
                    os.makedirs(downloads_dir, exist_ok=True)
                except (OSError, PermissionError) as e:
                    logger.error(f"Cannot create downloads directory {downloads_dir}: {e}")
                    with queue_lock:
                        if job_id in download_status:
                            download_status[job_id]['status'] = 'failed'
                            download_status[job_id]['error'] = f'Cannot create downloads directory: {str(e)}'
                    return False
            
            # Check available disk space (for cloud environments)
            try:
                stat = os.statvfs(downloads_dir)
                free_space_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
                if free_space_gb < 0.5:  # Less than 500MB free
                    logger.warning(f"Low disk space: {free_space_gb:.2f} GB free")
                    # Trigger cleanup
                    cleanup_old_files(downloads_dir)
            except (OSError, AttributeError):
                # statvfs not available on Windows, skip check
                pass
        except Exception as e:
            logger.error(f"Error setting up downloads directory: {e}")
            with queue_lock:
                if job_id in download_status:
                    download_status[job_id]['status'] = 'failed'
                    download_status[job_id]['error'] = f'Setup error: {str(e)}'
            return False
    
    # Check if paused before starting
    with queue_lock:
        if job_id in paused_jobs:
            if job_id in download_status:
                download_status[job_id]['status'] = 'paused'
            return False
    
    with queue_lock:
        if job_id not in download_status:
            download_status[job_id] = {
                'status': 'downloading',
                'progress': 0,
                'title': 'Extracting video info...',
                'error': None,
                'filename': None,
                'source_url': None,
                'client_ip': client_ip,
                'downloads_dir': downloads_dir
            }
        else:
            # Double-check not paused before setting to downloading
            if job_id not in paused_jobs:
                download_status[job_id]['status'] = 'downloading'
            else:
                download_status[job_id]['status'] = 'paused'
                return False
    
    # Try web client first (most stable), then mobile clients
    # Rotate client order to avoid patterns that trigger bot detection
    client_priority = ['web', 'ios', 'android', 'tv']
    last_error = None
    
    for idx, client in enumerate(client_priority):
        # Check if paused before each client attempt
        with queue_lock:
            if job_id in paused_jobs:
                if job_id in download_status:
                    download_status[job_id]['status'] = 'paused'
                return False
        
        try:
            progress_hook = ProgressHook(job_id)
            
            # Check if partial file exists for resume
            normalized_url = normalize_youtube_url(url)
            
            # Format selection: use format_id if provided, otherwise use quality selector
            format_selector = format_id if format_id else _get_format_selector(quality)
            
            # Get throttle speed from job settings or use global default
            effective_throttle = throttle_speed if throttle_speed is not None else DOWNLOAD_SPEED_THROTTLE_MB
            
            ydl_opts = {
                'outtmpl': os.path.join(downloads_dir, '%(title)s.%(ext)s'),
                'format': format_selector,
                'quiet': False,
                'no_warnings': False,
                'extractor_args': {
                    'youtube': {
                        'player_client': [client],
                        'player_skip': ['webpage', 'configs'],
                        # Try to get player response more reliably
                        'skip': ['dash'] if client == 'android' else [],
                    }
                },
                'http_headers': _get_client_headers(client),
                'retries': 5,
                'fragment_retries': 5,
                'file_access_retries': 3,
                'socket_timeout': 30,
                'quiet': True,
                'no_warnings': False,
                'ignoreerrors': False,
                'hls_prefer_native': True,
                'extract_flat': False,
                'progress_hooks': [progress_hook.hook],
                'continue_dl': True,  # Enable resume for interrupted downloads
            }
            
            # Add download speed throttling if enabled
            if effective_throttle > 0:
                # Convert MB/s to bytes/s for yt-dlp
                throttle_bytes = int(effective_throttle * 1024 * 1024)
                ydl_opts['ratelimit'] = throttle_bytes
                logger.info(f"Download speed throttled to {effective_throttle} MB/s for job {job_id}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info first
                info = ydl.extract_info(normalized_url, download=False)
                video_title = info.get('title', 'video')
                
                # Check file size before downloading
                file_size = info.get('filesize') or info.get('filesize_approx', 0)
                if file_size > MAX_FILE_SIZE_BYTES:
                    file_size_gb = file_size / (1024 ** 3)
                    max_size_gb = MAX_FILE_SIZE_GB
                    error_msg = f'Video file is too large ({file_size_gb:.2f} GB). Maximum file size is {max_size_gb} GB. Please try downloading a lower quality version.'
                    with queue_lock:
                        if job_id in download_status:
                            download_status[job_id]['status'] = 'failed'
                            download_status[job_id]['error'] = error_msg
                            download_status[job_id]['title'] = video_title
                    return False
                
                # Get source URL (direct video URL if available)
                source_url = None
                if 'url' in info:
                    source_url = info['url']
                elif 'requested_formats' in info and info['requested_formats']:
                    source_url = info['requested_formats'][0].get('url', normalized_url)
                else:
                    source_url = normalized_url
                
                with queue_lock:
                    # Check again if paused during info extraction
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                        download_status[job_id]['title'] = video_title
                        download_status[job_id]['source_url'] = source_url
                        return False
                    download_status[job_id]['title'] = video_title
                    download_status[job_id]['progress'] = 10
                    download_status[job_id]['source_url'] = source_url
                    if file_size:
                        download_status[job_id]['estimated_size'] = file_size
                
                # Download the video with pause checks
                # Note: yt-dlp doesn't support pause mid-download easily, 
                # but we check before and after
                ydl.download([normalized_url])
                
                # Check if paused after download completes
                with queue_lock:
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                        return False
                
                # Find the downloaded file
                filename = _find_downloaded_file(video_title, downloads_dir)
                
                if filename:
                    # Check actual file size
                    file_path = os.path.join(downloads_dir, filename)
                    if os.path.exists(file_path):
                        actual_size = os.path.getsize(file_path)
                        if actual_size > MAX_FILE_SIZE_BYTES:
                            # File exceeded limit after download, remove it
                            try:
                                os.remove(file_path)
                            except:
                                pass
                            file_size_gb = actual_size / (1024 ** 3)
                            max_size_gb = MAX_FILE_SIZE_GB
                            error_msg = f'Downloaded file is too large ({file_size_gb:.2f} GB). Maximum file size is {max_size_gb} GB. The file has been removed.'
                            with queue_lock:
                                download_status[job_id]['status'] = 'failed'
                                download_status[job_id]['error'] = error_msg
                            return False
                    
                    actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                    
                    with queue_lock:
                        download_status[job_id]['status'] = 'completed'
                        download_status[job_id]['progress'] = 100
                        download_status[job_id]['filename'] = filename
                        download_status[job_id]['title'] = video_title
                        download_status[job_id]['completed_at'] = datetime.now().isoformat()
                        download_status[job_id]['file_size'] = actual_size
                    
                    # Add to download history
                    with history_lock:
                        history_entry = {
                            'job_id': job_id,
                            'title': video_title,
                            'url': url,
                            'filename': filename,
                            'file_size': actual_size,
                            'status': 'completed',
                            'completed_at': datetime.now().isoformat(),
                            'client_ip': client_ip,
                            'quality': quality,
                            'format_id': format_id
                        }
                        download_history.append(history_entry)
                        # Keep only last MAX_HISTORY_SIZE entries
                        if len(download_history) > MAX_HISTORY_SIZE:
                            download_history.pop(0)
                    
                    # Trigger a refresh of downloads list (will be picked up by frontend polling)
                    return True
                else:
                    raise Exception('Download completed but file not found')
                    
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            last_error = error_msg
            
            # Check for various YouTube blocking patterns
            error_lower = error_msg.lower()
            
            # Check for permanent errors (don't retry)
            is_permanent = (
                'private video' in error_lower or
                'video unavailable' in error_lower or
                'sign in to confirm your age' in error_lower or
                'this video is not available' in error_lower or
                'sign in to confirm' in error_lower and 'bot' in error_lower
            )
            
            # Check for retryable errors (player response, extraction failures)
            is_retryable = (
                'failed to extract' in error_lower or
                'player response' in error_lower or
                'unable to extract' in error_lower or
                '403' in error_msg or 
                'forbidden' in error_lower or
                'http error 400' in error_lower or
                'precondition check failed' in error_lower
            )
            
            if is_permanent:
                # Permanent error - don't try other clients
                logger.warning(f"Permanent error for {normalized_url}: {error_msg[:200]}")
                break
            elif is_retryable:
                # Retryable error - try next client with a small delay
                logger.info(f"[DEBUG] Client '{client}' failed (retrying with next client): {error_msg[:200]}")
                # Increase delay for bot detection errors
                if 'bot' in error_lower or 'sign in' in error_lower:
                    time.sleep(2 + idx)  # Longer delay for bot detection (2s, 3s, 4s, 5s)
                else:
                    time.sleep(1)  # Small delay between client attempts
                continue
            else:
                # Other errors - still try next client as fallback
                logger.info(f"[DEBUG] Client '{client}' error (non-blocking): {error_msg[:200]}")
                time.sleep(0.5 + (idx * 0.3))  # Small delay, slightly increasing
                continue
                
        except Exception as e:
            error_msg = str(e)
            last_error = error_msg
            error_lower = error_msg.lower()
            is_blocked = (
                '403' in error_msg or 
                'forbidden' in error_lower or
                'http error 400' in error_lower or
                'precondition check failed' in error_lower
            )
            if is_blocked:
                print(f"[DEBUG] Client '{client}' blocked for {normalized_url}: {error_msg[:200]}")
            else:
                print(f"[DEBUG] Client '{client}' exception: {error_msg[:200]}")
            continue
    
    # All clients failed
    # Improve error messages for common issues
    error_msg = last_error or 'Unknown error'
    error_lower = error_msg.lower()
    
    if 'private video' in error_lower or 'video unavailable' in error_lower:
        user_error = 'This video is private or unavailable. It may have been removed or made private by the uploader.'
    elif 'sign in to confirm your age' in error_lower or 'age-restricted' in error_lower:
        user_error = 'This video is age-restricted and cannot be downloaded. Please try a different video.'
    elif ('sign in to confirm' in error_lower and 'bot' in error_lower) or 'cookies' in error_lower and 'bot' in error_lower:
        user_error = 'YouTube is requiring verification for this video. Please try again in a few minutes. If the issue persists, YouTube may be temporarily blocking automated access.'
    elif 'player response' in error_lower or ('failed to extract' in error_lower and 'player' in error_lower):
        user_error = 'YouTube temporarily blocked access to this video. Please try again in a few minutes, or try a different video. This usually resolves itself quickly.'
    elif '403' in error_msg or 'forbidden' in error_lower:
        user_error = 'Access denied. YouTube may be blocking this video. Please try again later or try a different video.'
    elif 'network' in error_lower or 'connection' in error_lower or 'timeout' in error_lower:
        user_error = 'Network error occurred. Please check your internet connection and try again.'
    else:
        # Truncate long error messages but keep important parts
        if len(error_msg) > 200:
            user_error = f'Download failed: {error_msg[:150]}...'
        else:
            user_error = f'Download failed: {error_msg}'
    
    with queue_lock:
        download_status[job_id]['status'] = 'failed'
        download_status[job_id]['error'] = user_error
        download_status[job_id]['failed_at'] = datetime.now().isoformat()
    
    # Add to download history
    with history_lock:
        history_entry = {
            'job_id': job_id,
            'title': download_status[job_id].get('title', 'Unknown'),
            'url': url,
            'status': 'failed',
            'error': user_error,
            'failed_at': datetime.now().isoformat(),
            'client_ip': client_ip,
            'quality': quality,
            'format_id': format_id
        }
        download_history.append(history_entry)
        # Keep only last MAX_HISTORY_SIZE entries
        if len(download_history) > MAX_HISTORY_SIZE:
            download_history.pop(0)
    
    return False


def process_queue():
    """Process download queue in background"""
    global processing
    
    while True:
        with queue_lock:
            if download_queue and not processing:
                # Filter out paused jobs from queue
                # Handle both old format (3 items) and new format (4 items with client_ip)
                active_jobs = []
                for item in download_queue:
                    if len(item) == 3:
                        jid, url, qual = item
                        client_ip = None
                    else:
                        jid, url, qual, client_ip = item
                    if jid not in paused_jobs:
                        active_jobs.append((jid, url, qual, client_ip))
                
                if active_jobs:
                    processing = True
                    job = active_jobs[0]
                    job_id = job[0]
                    # Remove from queue (will be re-added if needed)
                    download_queue[:] = [item for item in download_queue if item[0] != job_id]
                else:
                    job = None
            else:
                job = None
        
        if job:
            job_id, url, quality, client_ip = job
            try:
                # Double-check not paused before starting download
                with queue_lock:
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                        processing = False
                        continue
                
                # Get format_id and throttle_speed from download_status if available
                format_id = None
                throttle_speed = None
                if job_id in download_status:
                    format_id = download_status[job_id].get('format_id')
                    throttle_speed = download_status[job_id].get('throttle_speed')
                download_video(job_id, url, quality, client_ip, format_id, throttle_speed)
            except KeyboardInterrupt:
                # Handle pause signal
                with queue_lock:
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                    else:
                        download_status[job_id]['status'] = 'failed'
                        download_status[job_id]['error'] = 'Download interrupted'
            except Exception as e:
                with queue_lock:
                    if job_id not in paused_jobs:
                        download_status[job_id]['status'] = 'failed'
                        download_status[job_id]['error'] = str(e)
            finally:
                processing = False
        else:
            # Small delay when no jobs to process
            time.sleep(0.5)
        
        time.sleep(0.1)  # Check queue more frequently


# Start queue processor thread
queue_thread = threading.Thread(target=process_queue, daemon=True)
queue_thread.start()


@app.route('/')
def index():
    """Main page - serve React app in production, fallback to old template"""
    # In production, serve React app from frontend/dist
    react_dist = os.path.join(os.path.dirname(__file__), 'frontend', 'dist', 'index.html')
    react_dist_dir = os.path.join(os.path.dirname(__file__), 'frontend', 'dist')
    
    # Check if React build exists
    if os.path.exists(react_dist):
        return send_file(react_dist)
    
    # Log warning if React build is missing (helps debug Railway deployments)
    logger.warning(f"React build not found at {react_dist}. "
                   f"React dist directory exists: {os.path.exists(react_dist_dir)}. "
                   f"Falling back to old template.")
    
    # Fallback to old template for development/backwards compatibility
    return render_template('index.html')


@app.route('/<path:path>')
def serve_react(path):
    """Serve React app static files and handle client-side routing"""
    # Don't interfere with API routes
    api_routes = ['api/', 'download_file/', 'video_info', 'download', 'queue', 'pause', 
                  'resume', 'history', 'health', 'cleanup', 'list_downloads', 'open_']
    if any(path.startswith(route) for route in api_routes):
        return jsonify({'error': 'Not found'}), 404
    
    # Try to serve static file from React dist (use send_from_directory to prevent path traversal)
    react_dist_dir = os.path.join(os.path.dirname(__file__), 'frontend', 'dist')
    if os.path.exists(react_dist_dir):
        try:
            return send_from_directory(react_dist_dir, path)
        except (FileNotFoundError, ValueError):
            # File not found or path traversal attempt - fall through to index.html
            pass
    
    # For React Router - serve index.html for all other routes
    react_dist = os.path.join(os.path.dirname(__file__), 'frontend', 'dist', 'index.html')
    if os.path.exists(react_dist):
        return send_file(react_dist)
    
    return jsonify({'error': 'Not found'}), 404


@app.route('/video_info', methods=['POST'])
def get_video_info():
    """Get video information including thumbnail, formats, and metadata"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Invalid request data'}), 400
    
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400
    
    # Strictly validate YouTube URL
    is_valid, video_id, is_playlist_detected, normalized_url_check = validate_youtube_url(url)
    if not is_valid:
        return jsonify({'success': False, 'error': 'Invalid YouTube URL format'}), 400
    
    try:
        normalized_url = normalized_url_check
        
        # Check if it's a playlist
        is_playlist = 'playlist' in url.lower() or 'list=' in url.lower()
        
        if is_playlist:
            # Extract playlist info
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
                
                playlist_title = info.get('title', 'Playlist')
                entries = info.get('entries', [])
                
                videos = []
                for entry in entries[:50]:  # Limit to first 50 videos
                    if entry:
                        video_id = entry.get('id') or entry.get('url', '').split('v=')[-1].split('&')[0]
                        videos.append({
                            'id': video_id,
                            'title': entry.get('title', 'Unknown'),
                            'url': f"https://www.youtube.com/watch?v={video_id}"
                        })
                
                return jsonify({
                    'success': True,
                    'is_playlist': True,
                    'title': playlist_title,
                    'video_count': len(videos),
                    'videos': videos
                })
        else:
            # Single video info
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
                
                # Get available formats
                formats = []
                if 'formats' in info:
                    for fmt in info['formats']:
                        if fmt.get('vcodec') != 'none' or fmt.get('acodec') != 'none':  # Has video or audio
                            formats.append({
                                'format_id': fmt.get('format_id'),
                                'ext': fmt.get('ext', 'unknown'),
                                'resolution': fmt.get('resolution', 'unknown'),
                                'filesize': fmt.get('filesize') or fmt.get('filesize_approx', 0),
                                'vcodec': fmt.get('vcodec', 'unknown'),
                                'acodec': fmt.get('acodec', 'unknown'),
                                'format_note': fmt.get('format_note', ''),
                            })
                
                # Get thumbnail (fix IndexError if thumbnails is empty list)
                thumbnails = info.get('thumbnails') or [{}]
                thumbnail = info.get('thumbnail') or (thumbnails[0].get('url', '') if thumbnails else '')
                
                return jsonify({
                    'success': True,
                    'is_playlist': False,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': thumbnail,
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown'),
                    'view_count': info.get('view_count', 0),
                    'formats': formats[:20],  # Limit to first 20 formats
                    'filesize': info.get('filesize') or info.get('filesize_approx', 0)
                })
    
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if 'Private video' in error_msg or 'Video unavailable' in error_msg:
            return jsonify({
                'success': False,
                'error': 'This video is private or unavailable'
            }), 400
        return jsonify({
            'success': False,
            'error': f'Unable to fetch video info: {error_msg[:200]}'
        }), 400
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return jsonify({
            'success': False,
            'error': f'Error processing video: {str(e)[:200]}'
        }), 500


def validate_youtube_url(url):
    """
    Strictly validate YouTube URL format and extract video/playlist ID.
    Returns (is_valid, video_id, is_playlist, normalized_url)
    """
    if not url or not isinstance(url, str):
        return False, None, False, None
    
    # Remove whitespace
    url = url.strip()
    
    # Must be HTTP/HTTPS
    if not url.startswith(('http://', 'https://')):
        return False, None, False, None
    
    try:
        parsed = urlparse(url)
    except Exception:
        return False, None, False, None
    
    # Must be YouTube domain
    if 'youtube.com' not in parsed.netloc and 'youtu.be' not in parsed.netloc:
        return False, None, False, None
    
    # Extract video ID or playlist ID
    video_id = None
    playlist_id = None
    is_playlist = False
    
    if 'youtu.be' in parsed.netloc:
        # Short URL format: youtu.be/VIDEO_ID
        video_id = parsed.path.lstrip('/').split('?')[0]
        if len(video_id) != 11 or not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
            return False, None, False, None
    elif 'youtube.com' in parsed.netloc:
        # Standard URL format
        query_params = parse_qs(parsed.query)
        
        # Check for playlist
        if 'list' in query_params:
            playlist_id = query_params['list'][0]
            is_playlist = True
        
        # Get video ID
        if 'v' in query_params:
            video_id = query_params['v'][0]
        elif '/embed/' in parsed.path:
            video_id = parsed.path.split('/embed/')[-1].split('?')[0]
        elif '/v/' in parsed.path:
            video_id = parsed.path.split('/v/')[-1].split('?')[0]
        
        # Validate video ID format
        if video_id and (len(video_id) != 11 or not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id)):
            return False, None, False, None
    
    # Build normalized URL
    if is_playlist and playlist_id:
        normalized_url = f'https://www.youtube.com/playlist?list={playlist_id}'
        if video_id:
            normalized_url = f'https://www.youtube.com/watch?v={video_id}&list={playlist_id}'
    elif video_id:
        normalized_url = f'https://www.youtube.com/watch?v={video_id}'
    else:
        return False, None, False, None
    
    return True, video_id, is_playlist, normalized_url


def normalize_youtube_url(url):
    """
    Normalize YouTube URL to handle all formats.
    Uses strict validation.
    """
    is_valid, video_id, is_playlist, normalized_url = validate_youtube_url(url)
    if is_valid:
        return normalized_url
    # Fallback for edge cases (let yt-dlp handle it)
    return url


def extract_video_title(job_id, url):
    """Extract video title in background thread - optimized for speed with fallback"""
    normalized_url = normalize_youtube_url(url)
    
    # Try multiple clients for title extraction (fallback if one fails)
    # Use web client first (most stable), then mobile clients
    clients = ['web', 'ios', 'android']
    
    for client in clients:
        try:
            # Optimized options for fast title extraction
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,  # Faster - only get basic info, don't process formats
                'skip_download': True,
                'socket_timeout': 10,  # Shorter timeout for faster failure
                'extractor_args': {
                    'youtube': {
                        'player_client': [client],  # Try different clients
                        'player_skip': ['webpage', 'configs', 'js'],  # Skip unnecessary processing
                    }
                },
                'http_headers': _get_client_headers(client),  # Use appropriate headers
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
                title = info.get('title', url)
                
                # Try to get source URL (may not be available in extract_flat mode)
                source_url = None
                try:
                    if 'url' in info:
                        source_url = info['url']
                    elif 'requested_formats' in info and info['requested_formats']:
                        source_url = info['requested_formats'][0].get('url', normalized_url)
                except:
                    pass
                
                with queue_lock:
                    if job_id in download_status:
                        download_status[job_id]['title'] = title
                        if source_url:
                            download_status[job_id]['source_url'] = source_url
                
                # Success - break out of client loop
                return
                
        except Exception as e:
            # If this client failed, try next one
            if client == clients[-1]:  # Last client failed
                # Set a fallback title
                with queue_lock:
                    if job_id in download_status:
                        download_status[job_id]['title'] = normalized_url
            continue


def check_rate_limit(client_ip):
    """Check if client has exceeded rate limits"""
    current_time = time.time()
    
    with rate_limit_lock:
        # Check hourly download limit
        if client_ip not in download_counts:
            download_counts[client_ip] = {'count': 0, 'reset_time': current_time + 3600}
        
        client_data = download_counts[client_ip]
        
        # Reset counter if hour has passed
        if current_time >= client_data['reset_time']:
            client_data['count'] = 0
            client_data['reset_time'] = current_time + 3600
        
        # Check if limit exceeded
        if client_data['count'] >= MAX_DOWNLOADS_PER_HOUR:
            remaining_time = int(client_data['reset_time'] - current_time)
            log_security_event('RATE_LIMIT_EXCEEDED', f'Hourly limit: {client_data["count"]}/{MAX_DOWNLOADS_PER_HOUR}', client_ip)
            return False, f'Rate limit exceeded. You can download {MAX_DOWNLOADS_PER_HOUR} videos per hour. Please try again in {remaining_time // 60} minutes.'
        
        # Check concurrent downloads
        concurrent = concurrent_downloads.get(client_ip, 0)
        if concurrent >= MAX_CONCURRENT_DOWNLOADS:
            log_security_event('CONCURRENT_LIMIT_EXCEEDED', f'Concurrent: {concurrent}/{MAX_CONCURRENT_DOWNLOADS}', client_ip)
            return False, f'Too many concurrent downloads. Maximum {MAX_CONCURRENT_DOWNLOADS} downloads at once. Please wait for current downloads to complete.'
        
        return True, None


def sanitize_input(text, max_length=500):
    """Sanitize user input to prevent XSS and injection attacks"""
    if not text or not isinstance(text, str):
        return ''
    
    # Limit length
    text = text[:max_length]
    
    # Remove potentially dangerous characters
    text = re.sub(r'[<>"\']', '', text)
    
    # Remove control characters
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    return text.strip()


@app.route('/download', methods=['POST'])
def add_to_queue():
    """Add video to download queue
    
    Supports all YouTube URL formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtu.be/VIDEO_ID?si=...
    - https://www.youtube.com/embed/VIDEO_ID
    - And more...
    """
    # Get client IP from request (handles proxy headers for cloud)
    client_ip = get_client_ip()
    
    # Validate and sanitize input
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({
            'success': False,
            'error': 'Invalid request data',
            'error_type': 'invalid_request'
        }), 400
    
    url = sanitize_input(data.get('url', ''), max_length=500)
    quality = sanitize_input(data.get('quality', 'best'), max_length=20)
    format_id = sanitize_input(data.get('format_id', ''), max_length=50) if data.get('format_id') else None
    is_playlist = data.get('is_playlist', False)
    playlist_videos = data.get('playlist_videos', [])
    throttle_speed = data.get('throttle_speed')
    
    # Validate throttle speed if provided
    if throttle_speed is not None:
        try:
            throttle_speed = float(throttle_speed)
            if throttle_speed < 0 or throttle_speed > 100:
                return jsonify({
                    'success': False,
                    'error': 'Throttle speed must be between 0 and 100 MB/s',
                    'error_type': 'invalid_request'
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'Invalid throttle speed value',
                'error_type': 'invalid_request'
            }), 400
    
    # Validate playlist_videos if provided
    if is_playlist and playlist_videos:
        if not isinstance(playlist_videos, list) or len(playlist_videos) > 100:
            return jsonify({
                'success': False,
                'error': 'Invalid playlist data',
                'error_type': 'invalid_request'
            }), 400
        playlist_videos = [sanitize_input(v, max_length=500) for v in playlist_videos if v]
    
    # Improved error messages
    if not url:
        return jsonify({
            'success': False, 
            'error': 'No URL provided. Please paste a YouTube video URL in the input field.',
            'error_type': 'missing_url'
        }), 400
    
    # Strictly validate YouTube URL
    is_valid, video_id, is_playlist_detected, normalized_url = validate_youtube_url(url)
    if not is_valid:
        return jsonify({
            'success': False, 
            'error': 'Invalid YouTube URL. Please provide a valid YouTube video or playlist URL.',
            'error_type': 'invalid_url'
        }), 400
    
    # Check rate limits
    rate_ok, rate_error = check_rate_limit(client_ip)
    if not rate_ok:
        return jsonify({
            'success': False,
            'error': rate_error,
            'error_type': 'rate_limit_exceeded'
        }), 429
    
    # Handle playlist downloads
    if is_playlist and playlist_videos:
        job_ids = []
        for video_url in playlist_videos:
            try:
                normalized_video_url = normalize_youtube_url(video_url)
                job_id = str(uuid.uuid4())
                
                # Check rate limit for each video
                rate_ok, rate_error = check_rate_limit(client_ip)
                if not rate_ok:
                    # Skip remaining videos if rate limit exceeded
                    break
                
                # Increment download count for each video (but NOT concurrent_downloads - that's when download starts)
                with rate_limit_lock:
                    if client_ip not in download_counts:
                        download_counts[client_ip] = {'count': 0, 'reset_time': time.time() + 3600}
                    download_counts[client_ip]['count'] += 1
                    # Note: concurrent_downloads is incremented when download actually starts, not when queued
                
                with queue_lock:
                    download_queue.append((job_id, normalized_video_url, quality, client_ip))
                    download_status[job_id] = {
                        'status': 'queued',
                        'progress': 0,
                        'title': 'Extracting video info...',
                        'error': None,
                        'filename': None,
                        'url': video_url,
                        'quality': quality,
                        'format_id': format_id,
                        'throttle_speed': throttle_speed,
                        'client_ip': client_ip,
                        'added_at': datetime.now().isoformat(),
                        'estimated_size': None,
                        'is_playlist_item': True
                    }
                
                job_ids.append(job_id)
                
                # Extract title immediately in background
                title_thread = threading.Thread(target=extract_video_title, args=(job_id, normalized_video_url), daemon=True)
                title_thread.start()
            except Exception as e:
                logger.error(f"Error adding playlist video {video_url}: {e}")
                # Rollback download count if it was incremented
                with rate_limit_lock:
                    if client_ip in download_counts and download_counts[client_ip]['count'] > 0:
                        download_counts[client_ip]['count'] = max(0, download_counts[client_ip]['count'] - 1)
                continue
        
        return jsonify({
            'success': True,
            'job_ids': job_ids,
            'message': f'{len(job_ids)} videos added to download queue',
            'queue_position': len(download_queue)
        })
    
    # Single video download
    # Normalize URL (already validated above)
    normalized_url = normalize_youtube_url(url)
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Increment download count
    with rate_limit_lock:
        if client_ip not in download_counts:
            download_counts[client_ip] = {'count': 0, 'reset_time': time.time() + 3600}
        download_counts[client_ip]['count'] += 1
        # Note: concurrent_downloads is incremented when download actually starts, not when queued
    
    with queue_lock:
        download_queue.append((job_id, normalized_url, quality, client_ip))
        download_status[job_id] = {
            'status': 'queued',
            'progress': 0,
            'title': 'Extracting video info...',
            'error': None,
            'filename': None,
            'url': url,  # Keep original URL for display
            'quality': quality,
            'format_id': format_id,
            'throttle_speed': throttle_speed,
            'client_ip': client_ip,
            'added_at': datetime.now().isoformat(),
            'estimated_size': None  # Will be populated when video info is extracted
        }
    
    # Extract title immediately in background
    title_thread = threading.Thread(target=extract_video_title, args=(job_id, normalized_url), daemon=True)
    title_thread.start()
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'message': 'Video added to download queue',
        'queue_position': len(download_queue)
    })


@app.route('/status/<job_id>')
def get_status(job_id):
    """Get download status for a job"""
    with queue_lock:
        if job_id in download_status:
            status = download_status[job_id].copy()
            # Add queue position if queued
            if status['status'] == 'queued':
                try:
                    queue_pos = [i for i, (jid, _, _) in enumerate(download_queue) if jid == job_id][0] + 1
                    status['queue_position'] = queue_pos
                except:
                    status['queue_position'] = 0
            return jsonify(status)
        else:
            return jsonify({'error': 'Job not found'}), 404


@app.route('/pause/<job_id>', methods=['POST'])
def pause_download(job_id):
    """Pause a download"""
    with queue_lock:
        if job_id not in download_status:
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        status = download_status[job_id]['status']
        if status == 'completed':
            return jsonify({'success': False, 'error': 'Download already completed'}), 400
        elif status == 'failed':
            return jsonify({'success': False, 'error': 'Download already failed'}), 400
        elif status == 'paused':
            return jsonify({'success': True, 'message': 'Download already paused'})
        
        # Remove from queue if it's there (to prevent duplicates)
        download_queue[:] = [(jid, url, qual) for jid, url, qual in download_queue if jid != job_id]
        
        # Add to paused set
        paused_jobs.add(job_id)
        download_status[job_id]['status'] = 'paused'
        
        return jsonify({'success': True, 'message': 'Download paused'})


@app.route('/resume/<job_id>', methods=['POST'])
def resume_download(job_id):
    """Resume a paused download"""
    with queue_lock:
        if job_id not in download_status:
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        if job_id not in paused_jobs:
            return jsonify({'success': False, 'error': 'Download is not paused'}), 400
        
        status = download_status[job_id]['status']
        if status == 'completed':
            return jsonify({'success': False, 'error': 'Download already completed'}), 400
        
        # Check if job is already in queue (prevent duplicates)
        already_in_queue = any(jid == job_id for jid, _, _ in download_queue)
        if already_in_queue:
            # Just remove from paused set and update status
            paused_jobs.remove(job_id)
            download_status[job_id]['status'] = 'queued'
            return jsonify({'success': True, 'message': 'Download resumed (already in queue)'})
        
        # Remove from paused set
        paused_jobs.remove(job_id)
        
        # Get job details
        url = download_status[job_id].get('url') or download_status[job_id].get('source_url')
        quality = download_status[job_id].get('quality', 'best')
        
        if not url:
            return jsonify({'success': False, 'error': 'Cannot resume: URL not found'}), 400
        
        # Normalize URL
        normalized_url = normalize_youtube_url(url)
        
        # Re-add to queue (only if not already there)
        download_queue.append((job_id, normalized_url, quality))
        download_status[job_id]['status'] = 'queued'
        download_status[job_id]['progress'] = download_status[job_id].get('progress', 0)
        
        return jsonify({'success': True, 'message': 'Download resumed'})


@app.route('/pause_all', methods=['POST'])
def pause_all_downloads():
    """Pause all active downloads"""
    with queue_lock:
        paused_count = 0
        
        # Pause all queued jobs
        for job_id, _, _ in list(download_queue):
            if job_id in download_status:
                status = download_status[job_id]['status']
                if status not in ['completed', 'failed', 'paused']:
                    paused_jobs.add(job_id)
                    download_status[job_id]['status'] = 'paused'
                    paused_count += 1
        
        # Pause all downloading jobs
        for job_id, status_info in download_status.items():
            status = status_info['status']
            if status == 'downloading' and job_id not in paused_jobs:
                paused_jobs.add(job_id)
                download_status[job_id]['status'] = 'paused'
                paused_count += 1
        
        # Remove all paused jobs from queue (handle both old and new format)
        new_queue = []
        for item in download_queue:
            if len(item) == 3:
                jid, url, qual = item
            else:
                jid, url, qual, _ = item
            if jid not in paused_jobs:
                new_queue.append(item)
        download_queue[:] = new_queue
        
        return jsonify({'success': True, 'message': f'Paused {paused_count} download(s)'})


@app.route('/resume_all', methods=['POST'])
def resume_all_downloads():
    """Resume all paused downloads"""
    with queue_lock:
        resumed_count = 0
        
        for job_id in list(paused_jobs):
            if job_id in download_status:
                status = download_status[job_id]['status']
                if status == 'paused':
                    # Check if already in queue
                    already_in_queue = any(jid == job_id for jid, _, _, *_ in download_queue)
                    if not already_in_queue:
                        url = download_status[job_id].get('url') or download_status[job_id].get('source_url')
                        quality = download_status[job_id].get('quality', 'best')
                        client_ip = download_status[job_id].get('client_ip')
                        
                        if url:
                            normalized_url = normalize_youtube_url(url)
                            download_queue.append((job_id, normalized_url, quality, client_ip))
                            download_status[job_id]['status'] = 'queued'
                            resumed_count += 1
                    
                    paused_jobs.remove(job_id)
        
        return jsonify({'success': True, 'message': f'Resumed {resumed_count} download(s)'})


@app.route('/prioritize/<job_id>/<direction>', methods=['POST'])
def prioritize_download(job_id, direction):
    """Move a download up or down in the queue (prioritize/deprioritize)"""
    with queue_lock:
        if job_id not in download_status:
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        # Find job in queue (handle both old and new format)
        queue_index = None
        for i, item in enumerate(download_queue):
            jid = item[0]
            if jid == job_id:
                queue_index = i
                break
        
        if queue_index is None:
            return jsonify({'success': False, 'error': 'Job not in queue'}), 400
        
        if direction == 'up':
            # Move up (higher priority)
            if queue_index > 0:
                download_queue[queue_index], download_queue[queue_index - 1] = \
                    download_queue[queue_index - 1], download_queue[queue_index]
                return jsonify({'success': True, 'message': 'Moved up in queue'})
            else:
                return jsonify({'success': False, 'error': 'Already at top of queue'}), 400
        elif direction == 'down':
            # Move down (lower priority)
            if queue_index < len(download_queue) - 1:
                download_queue[queue_index], download_queue[queue_index + 1] = \
                    download_queue[queue_index + 1], download_queue[queue_index]
                return jsonify({'success': True, 'message': 'Moved down in queue'})
            else:
                return jsonify({'success': False, 'error': 'Already at bottom of queue'}), 400
        else:
            return jsonify({'success': False, 'error': 'Invalid direction'}), 400


@app.route('/source_url/<job_id>')
def get_source_url(job_id):
    """Get the video source URL for a job"""
    with queue_lock:
        if job_id not in download_status:
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        source_url = download_status[job_id].get('source_url')
        original_url = download_status[job_id].get('url')
        
        if not source_url and not original_url:
            return jsonify({'success': False, 'error': 'Source URL not available yet'}), 404
        
        return jsonify({
            'success': True,
            'source_url': source_url or original_url,
            'original_url': original_url,
            'title': download_status[job_id].get('title', 'Unknown')
        })


@app.route('/queue')
def get_queue():
    """Get all download jobs status"""
    with queue_lock:
        # Get client IP from request to filter jobs (handles proxy headers)
        client_ip = get_client_ip()
        
        jobs = []
        # Add queued jobs (only for this client)
        for i, item in enumerate(download_queue):
            job_id = item[0]
            if job_id in download_status:
                # Only show jobs for this client
                job_client_ip = download_status[job_id].get('client_ip')
                if job_client_ip == client_ip:
                    job_status = download_status[job_id].copy()
                    job_status['queue_position'] = i + 1
                    job_status['job_id'] = job_id
                    jobs.append(job_status)
        
        # Add active/processing/paused jobs (only for this client)
        for job_id, status in download_status.items():
            job_client_ip = status.get('client_ip')
            if job_client_ip == client_ip and status['status'] in ['downloading', 'completed', 'failed', 'paused']:
                job_status = status.copy()
                job_status['job_id'] = job_id
                # Add queue position for paused jobs that are still in queue
                if status['status'] == 'paused' and job_id not in [j[0] for j in download_queue]:
                    job_status['queue_position'] = 0
                jobs.append(job_status)
        
        return jsonify({'jobs': jobs})


@app.route('/download_file/<filename>')
def download_file(filename):
    """Serve downloaded file from client's folder with security validation"""
    client_ip = get_client_ip()
    downloads_dir = get_client_downloads_folder(client_ip)
    
    # Sanitize filename to prevent directory traversal
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        log_security_event('INVALID_FILENAME', f'Filename: {filename}', client_ip)
        return jsonify({'error': 'Invalid filename'}), 400
    
    file_path = os.path.join(downloads_dir, safe_filename)
    
    # Prevent directory traversal
    file_path = os.path.abspath(file_path)
    downloads_dir_abs = os.path.abspath(downloads_dir)
    if not file_path.startswith(downloads_dir_abs):
        log_security_event('DIRECTORY_TRAVERSAL', f'Path: {file_path}, Expected: {downloads_dir_abs}', client_ip)
        return jsonify({'error': 'Invalid file path'}), 403
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Validate file type
    if not validate_file_type(file_path):
        log_security_event('INVALID_FILE_TYPE', f'Filename: {filename}', client_ip)
        return jsonify({'error': 'Invalid file type'}), 403
    
    try:
        # If auto-delete is enabled, use Flask's after_this_request to delete file after response completes
        # This ensures the file is only deleted after the response is fully sent
        if AUTO_DELETE_AFTER_DOWNLOAD:
            from flask import after_this_request
            
            @after_this_request
            def delete_file_after_response(response):
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Auto-deleted file after download: {filename}")
                except Exception as e:
                    logger.warning(f"Could not auto-delete file {file_path}: {e}")
                return response
        
        # Send file to user with secure headers
        response = send_file(file_path, as_attachment=True)
        # Add security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Disposition'] = f'attachment; filename="{safe_filename}"'
        return response
    except Exception as e:
        logger.error(f"Error serving file {file_path}: {e}")
        return jsonify({'error': f'Error serving file: {str(e)}'}), 500


@app.route('/list_downloads')
def list_downloads():
    """List all downloaded files for the requesting client (excluding .part files - incomplete downloads)"""
    client_ip = get_client_ip()
    downloads_dir = get_client_downloads_folder(client_ip)
    files = []
    if os.path.exists(downloads_dir):
        for file in os.listdir(downloads_dir):
            file_path = os.path.join(downloads_dir, file)
            # Only show completed files, exclude .part files (incomplete downloads)
            if os.path.isfile(file_path) and not file.endswith('.part'):
                try:
                    file_stat = os.stat(file_path)
                    files.append({
                        'name': file,
                        'size': file_stat.st_size,
                        'modified': file_stat.st_mtime  # Add modification time for sorting
                    })
                except OSError:
                    # Skip files that can't be accessed
                    continue
        # Sort by modification time (newest first)
        files.sort(key=lambda x: x.get('modified', 0), reverse=True)
    return jsonify({'files': files})


@app.route('/open_folder')
def open_folder():
    """Open the downloads folder for the requesting client in the file explorer"""
    import platform
    import subprocess
    
    client_ip = get_client_ip()
    downloads_dir = get_client_downloads_folder(client_ip)
    
    try:
        if platform.system() == 'Windows':
            os.startfile(downloads_dir)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.Popen(['open', downloads_dir])
        else:  # Linux
            subprocess.Popen(['xdg-open', downloads_dir])
        return jsonify({'success': True, 'message': 'Folder opened'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/open_file_in_folder/<path:filename>')
def open_file_in_folder(filename):
    """Open a specific file's location in the file explorer for the requesting client"""
    import platform
    import subprocess
    from urllib.parse import unquote
    
    client_ip = get_client_ip()
    downloads_dir = get_client_downloads_folder(client_ip)
    
    try:
        # Decode URL-encoded filename
        decoded_filename = unquote(filename)
        # Use secure_filename to sanitize, but preserve the original for lookup
        file_path = os.path.join(downloads_dir, decoded_filename)
        
        # Check if file exists with decoded name
        if not os.path.exists(file_path):
            # Try with secure_filename as fallback
            safe_filename = secure_filename(decoded_filename)
            file_path = os.path.join(downloads_dir, safe_filename)
            if not os.path.exists(file_path):
                # List files to find a match (case-insensitive)
                if os.path.exists(downloads_dir):
                    for file in os.listdir(downloads_dir):
                        if file.lower() == decoded_filename.lower() or file.lower() == safe_filename.lower():
                            file_path = os.path.join(downloads_dir, file)
                            break
                    else:
                        return jsonify({'success': False, 'error': f'File not found: {decoded_filename}'}), 404
                else:
                    return jsonify({'success': False, 'error': 'Downloads directory not found'}), 404
        
        if platform.system() == 'Windows':
            # Windows: open folder and select the file
            # Use absolute path and ensure proper escaping
            abs_path = os.path.abspath(file_path)
            subprocess.Popen(['explorer', '/select,', abs_path])
        elif platform.system() == 'Darwin':  # macOS
            # macOS: reveal file in Finder
            subprocess.Popen(['open', '-R', file_path])
        else:  # Linux
            # Linux: open folder (file selection varies by file manager)
            subprocess.Popen(['xdg-open', downloads_dir])
        return jsonify({'success': True, 'message': 'File location opened'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error opening file: {str(e)}'}), 500


@app.route('/history')
def get_download_history():
    """Get download history/analytics for the requesting client"""
    client_ip = get_client_ip()
    
    with history_lock:
        # Filter history by client IP
        client_history = [
            entry for entry in download_history 
            if entry.get('client_ip') == client_ip
        ]
        
        # Calculate analytics
        total_downloads = len([e for e in client_history if e.get('status') == 'completed'])
        total_failed = len([e for e in client_history if e.get('status') == 'failed'])
        total_size = sum(e.get('file_size', 0) for e in client_history if e.get('status') == 'completed')
        
        # Get recent downloads (last 50)
        recent_history = sorted(
            client_history,
            key=lambda x: x.get('completed_at') or x.get('failed_at') or '',
            reverse=True
        )[:50]
        
        return jsonify({
            'success': True,
            'history': recent_history,
            'analytics': {
                'total_downloads': total_downloads,
                'total_failed': total_failed,
                'success_rate': round((total_downloads / len(client_history) * 100) if client_history else 0, 2),
                'total_size_gb': round(total_size / (1024 ** 3), 2),
                'total_size_mb': round(total_size / (1024 ** 2), 2)
            }
        })


@app.route('/cleanup', methods=['POST'])
def trigger_cleanup():
    """Manually trigger file cleanup"""
    try:
        cleanup_old_files()
        return jsonify({'success': True, 'message': 'Cleanup completed'})
    except Exception as e:
        logger.error(f"Error triggering cleanup: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    try:
        # Check if downloads directory is accessible
        downloads_dir = get_downloads_folder()
        os.makedirs(downloads_dir, exist_ok=True)
        
        # Check disk space if available
        disk_info = {}
        try:
            stat = os.statvfs(downloads_dir)
            free_space_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            total_space_gb = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
            disk_info = {
                'free_gb': round(free_space_gb, 2),
                'total_gb': round(total_space_gb, 2),
                'used_percent': round((1 - free_space_gb / total_space_gb) * 100, 2) if total_space_gb > 0 else 0
            }
        except (OSError, AttributeError):
            pass
        
        return jsonify({
            'status': 'healthy',
            'downloads_dir': downloads_dir,
            'disk_info': disk_info,
            'cleanup_enabled': FILE_CLEANUP_ENABLED,
            'auto_delete_after_download': AUTO_DELETE_AFTER_DOWNLOAD,
            'ephemeral_storage': USE_EPHEMERAL_STORAGE,
            'max_age_hours': FILE_MAX_AGE_HOURS,
            'max_storage_gb': MAX_STORAGE_GB
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


if __name__ == '__main__':
    # Use environment variables for production deployment
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    host = os.environ.get('HOST', '0.0.0.0')
    # Default to 5001 on macOS to avoid AirPlay Receiver conflict on port 5000
    # Railway will set PORT automatically, so this only affects local development
    port = int(os.environ.get('PORT', 5001))
    
    logger.info(f"Starting Flask app on {host}:{port} (debug={debug_mode})")
    logger.info(f"Downloads directory: {BASE_DOWNLOADS_DIR}")
    
    app.run(debug=debug_mode, host=host, port=port)
