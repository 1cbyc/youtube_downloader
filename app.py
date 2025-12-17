from flask import Flask, render_template, request, jsonify, send_file
import os
import yt_dlp
from werkzeug.utils import secure_filename
import tempfile
import shutil
import threading
import time
import uuid
import re
from datetime import datetime

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Get the user's Downloads folder (works on Windows, macOS, and Linux)
def get_downloads_folder():
    """Get the base Downloads folder path"""
    import platform
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

def get_client_downloads_folder(client_ip):
    """Get downloads folder for a specific client IP address"""
    base_downloads = get_downloads_folder()
    # Create folder structure: Downloads/kids/{client_ip}/
    # Replace dots and colons in IP for folder name safety
    safe_ip = client_ip.replace('.', '_').replace(':', '_')
    client_folder = os.path.join(base_downloads, 'kids', safe_ip)
    os.makedirs(client_folder, exist_ok=True)
    return client_folder

# Base downloads directory (for backward compatibility)
BASE_DOWNLOADS_DIR = get_downloads_folder()

# Download queue and status tracking
download_queue = []
download_status = {}  # {job_id: {'status': 'queued'|'downloading'|'paused'|'completed'|'failed', 'progress': 0-100, 'title': '', 'error': '', 'source_url': ''}}
paused_jobs = set()  # Set of job_ids that are paused
active_downloads = {}  # {job_id: thread} - track active download threads for cancellation
queue_lock = threading.Lock()
processing = False


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


def download_video(job_id, url, quality='best', client_ip=None):
    """
    Download video from YouTube URL using multiple fallback strategies.
    Updates download_status dict with progress.
    Supports pause/resume and network interruption recovery.
    
    Args:
        job_id: Unique job identifier
        url: YouTube URL to download
        quality: Video quality ('best' or 'worst')
        client_ip: IP address of the client requesting the download
    """
    # Get client-specific downloads folder
    if client_ip:
        downloads_dir = get_client_downloads_folder(client_ip)
    else:
        # Fallback to default folder for backward compatibility
        downloads_dir = os.path.join(BASE_DOWNLOADS_DIR, 'kids')
        os.makedirs(downloads_dir, exist_ok=True)
    
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
    
    client_priority = ['ios', 'android', 'tv', 'web']
    last_error = None
    
    for client in client_priority:
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
            
            ydl_opts = {
                'outtmpl': os.path.join(downloads_dir, '%(title)s.%(ext)s'),
                'format': _get_format_selector(quality),
                'extractor_args': {
                    'youtube': {
                        'player_client': [client],
                        'player_skip': ['webpage', 'configs'],
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
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info first
                info = ydl.extract_info(normalized_url, download=False)
                video_title = info.get('title', 'video')
                
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
                    with queue_lock:
                        download_status[job_id]['status'] = 'completed'
                        download_status[job_id]['progress'] = 100
                        download_status[job_id]['filename'] = filename
                        download_status[job_id]['title'] = video_title
                        download_status[job_id]['completed_at'] = datetime.now().isoformat()
                    # Trigger a refresh of downloads list (will be picked up by frontend polling)
                    return True
                else:
                    raise Exception('Download completed but file not found')
                    
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            last_error = error_msg
            
            # Check for various YouTube blocking patterns
            error_lower = error_msg.lower()
            is_blocked = (
                '403' in error_msg or 
                'forbidden' in error_lower or
                'http error 400' in error_lower or
                'precondition check failed' in error_lower or
                'sign in to confirm your age' in error_lower or
                'video unavailable' in error_lower or
                'private video' in error_lower or
                'unable to extract' in error_lower and '403' in error_msg
            )
            
            if is_blocked:
                # Log which client failed for debugging
                print(f"[DEBUG] Client '{client}' failed for {normalized_url}: {error_msg[:200]}")
                continue  # Try next client
            else:
                # Other errors - still try next client as fallback
                print(f"[DEBUG] Client '{client}' error (non-blocking): {error_msg[:200]}")
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
    with queue_lock:
        download_status[job_id]['status'] = 'failed'
        download_status[job_id]['error'] = last_error or 'Unknown error'
    return False


def process_queue():
    """Process download queue in background"""
    global processing
    
    while True:
        with queue_lock:
            if download_queue and not processing:
                # Filter out paused jobs from queue
                active_jobs = [(jid, url, qual) for jid, url, qual in download_queue if jid not in paused_jobs]
                
                if active_jobs:
                    processing = True
                    job = active_jobs[0]
                    job_id = job[0]
                    # Remove from queue (will be re-added if needed)
                    download_queue[:] = [(jid, url, qual) for jid, url, qual in download_queue if jid != job_id]
                else:
                    job = None
            else:
                job = None
        
        if job:
            job_id, url, quality = job
            try:
                # Double-check not paused before starting download
                with queue_lock:
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                        processing = False
                        continue
                
                download_video(job_id, url, quality)
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
    """Main page"""
    return render_template('index.html')


def normalize_youtube_url(url):
    """
    Normalize YouTube URL to handle all formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtu.be/VIDEO_ID?si=...
    - https://www.youtube.com/embed/VIDEO_ID
    - etc.
    
    yt-dlp handles all these formats, but we normalize for consistency.
    """
    # Extract video ID from any YouTube URL format
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com\/.*[?&]v=([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            # Return normalized URL (yt-dlp will handle it fine, but this is cleaner)
            return f'https://www.youtube.com/watch?v={video_id}'
    
    # If no pattern matches, return original URL (yt-dlp might still handle it)
    return url


def extract_video_title(job_id, url):
    """Extract video title in background thread - optimized for speed with fallback"""
    normalized_url = normalize_youtube_url(url)
    
    # Try multiple clients for title extraction (fallback if one fails)
    clients = ['ios', 'android', 'web']
    
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
    # Get client IP from request
    client_ip = request.remote_addr
    
    data = request.get_json()
    url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    
    if not url:
        return jsonify({'success': False, 'error': 'Please provide a YouTube URL'}), 400
    
    # Check for YouTube URLs (supports all formats)
    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'success': False, 'error': 'Please provide a valid YouTube URL (youtube.com or youtu.be)'}), 400
    
    # Normalize URL for consistent handling
    normalized_url = normalize_youtube_url(url)
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
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
            'client_ip': client_ip,
            'added_at': datetime.now().isoformat()
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
        
        # Remove all paused jobs from queue
        download_queue[:] = [(jid, url, qual) for jid, url, qual in download_queue if jid not in paused_jobs]
        
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
                    already_in_queue = any(jid == job_id for jid, _, _ in download_queue)
                    if not already_in_queue:
                        url = download_status[job_id].get('url') or download_status[job_id].get('source_url')
                        quality = download_status[job_id].get('quality', 'best')
                        
                        if url:
                            normalized_url = normalize_youtube_url(url)
                            download_queue.append((job_id, normalized_url, quality))
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
        
        # Find job in queue
        queue_index = None
        for i, (jid, _, _) in enumerate(download_queue):
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
        jobs = []
        # Add queued jobs
        for i, (job_id, url, quality) in enumerate(download_queue):
            if job_id in download_status:
                job_status = download_status[job_id].copy()
                job_status['queue_position'] = i + 1
                job_status['job_id'] = job_id
                jobs.append(job_status)
        
        # Add active/processing/paused jobs
        for job_id, status in download_status.items():
            if status['status'] in ['downloading', 'completed', 'failed', 'paused']:
                job_status = status.copy()
                job_status['job_id'] = job_id
                # Add queue position for paused jobs that are still in queue
                if status['status'] == 'paused' and job_id not in [j[0] for j in download_queue]:
                    job_status['queue_position'] = 0
                jobs.append(job_status)
        
        return jsonify({'jobs': jobs})


@app.route('/download_file/<filename>')
def download_file(filename):
    """Serve downloaded file"""
    file_path = os.path.join(DOWNLOADS_DIR, secure_filename(filename))
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404


@app.route('/list_downloads')
def list_downloads():
    """List all downloaded files (excluding .part files - incomplete downloads)"""
    files = []
    if os.path.exists(DOWNLOADS_DIR):
        for file in os.listdir(DOWNLOADS_DIR):
            file_path = os.path.join(DOWNLOADS_DIR, file)
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
    """Open the downloads folder in the file explorer"""
    import platform
    import subprocess
    
    try:
        if platform.system() == 'Windows':
            os.startfile(DOWNLOADS_DIR)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.Popen(['open', DOWNLOADS_DIR])
        else:  # Linux
            subprocess.Popen(['xdg-open', DOWNLOADS_DIR])
        return jsonify({'success': True, 'message': 'Folder opened'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/open_file_in_folder/<path:filename>')
def open_file_in_folder(filename):
    """Open a specific file's location in the file explorer"""
    import platform
    import subprocess
    from urllib.parse import unquote
    
    try:
        # Decode URL-encoded filename
        decoded_filename = unquote(filename)
        # Use secure_filename to sanitize, but preserve the original for lookup
        file_path = os.path.join(DOWNLOADS_DIR, decoded_filename)
        
        # Check if file exists with decoded name
        if not os.path.exists(file_path):
            # Try with secure_filename as fallback
            safe_filename = secure_filename(decoded_filename)
            file_path = os.path.join(DOWNLOADS_DIR, safe_filename)
            if not os.path.exists(file_path):
                # List files to find a match (case-insensitive)
                if os.path.exists(DOWNLOADS_DIR):
                    for file in os.listdir(DOWNLOADS_DIR):
                        if file.lower() == decoded_filename.lower() or file.lower() == safe_filename.lower():
                            file_path = os.path.join(DOWNLOADS_DIR, file)
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
            subprocess.Popen(['xdg-open', DOWNLOADS_DIR])
        return jsonify({'success': True, 'message': 'File location opened'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error opening file: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
