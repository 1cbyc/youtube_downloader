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
    """Get the user's Downloads folder path"""
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
    
    # Create kids subfolder in Downloads
    kids_folder = os.path.join(downloads, 'kids')
    os.makedirs(kids_folder, exist_ok=True)
    return kids_folder

# Create downloads directory in user's Downloads folder
DOWNLOADS_DIR = get_downloads_folder()

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


def _find_downloaded_file(video_title):
    """Find the downloaded file by matching title"""
    safe_title = secure_filename(video_title[:50])
    
    if not os.path.exists(DOWNLOADS_DIR):
        return None
    
    files = os.listdir(DOWNLOADS_DIR)
    
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
        files_with_paths = [(f, os.path.getmtime(os.path.join(DOWNLOADS_DIR, f))) 
                           for f in files if os.path.isfile(os.path.join(DOWNLOADS_DIR, f))]
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


def download_video(job_id, url, quality='best'):
    """
    Download video from YouTube URL using multiple fallback strategies.
    Updates download_status dict with progress.
    Supports pause/resume and network interruption recovery.
    """
    # Check if paused before starting
    with queue_lock:
        if job_id in paused_jobs:
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
                'source_url': None
            }
        else:
            download_status[job_id]['status'] = 'downloading'
    
    client_priority = ['ios', 'android', 'tv', 'web']
    last_error = None
    
    for client in client_priority:
        # Check if paused before each client attempt
        with queue_lock:
            if job_id in paused_jobs:
                download_status[job_id]['status'] = 'paused'
                return False
        
        try:
            progress_hook = ProgressHook(job_id)
            
            # Check if partial file exists for resume
            normalized_url = normalize_youtube_url(url)
            expected_filename = None
            
            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s'),
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
                    download_status[job_id]['title'] = video_title
                    download_status[job_id]['progress'] = 10
                    download_status[job_id]['source_url'] = source_url
                
                # Check if paused before downloading
                with queue_lock:
                    if job_id in paused_jobs:
                        download_status[job_id]['status'] = 'paused'
                        return False
                
                # Download the video
                ydl.download([normalized_url])
                
                # Find the downloaded file
                filename = _find_downloaded_file(video_title)
                
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
            
            if 'HTTP Error 403' in error_msg or 'HTTP Error 400' in error_msg or 'Precondition check failed' in error_msg:
                continue
            else:
                continue
                
        except Exception as e:
            error_msg = str(e)
            last_error = error_msg
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
                processing = True
                job = download_queue.pop(0)
                job_id = job[0] if job else None
                # Skip if job is paused
                if job_id and job_id in paused_jobs:
                    processing = False
                    # Put job back at front of queue
                    download_queue.insert(0, job)
                    job = None
            else:
                job = None
        
        if job:
            job_id, url, quality = job
            try:
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
        
        time.sleep(0.5)  # Check queue every 500ms


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
    """Extract video title in background thread - optimized for speed"""
    try:
        # Normalize URL first
        normalized_url = normalize_youtube_url(url)
        
        # Optimized options for fast title extraction
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Faster - only get basic info, don't process formats
            'skip_download': True,
            'socket_timeout': 10,  # Shorter timeout for faster failure
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios'],  # iOS client is fastest for metadata
                    'player_skip': ['webpage', 'configs', 'js'],  # Skip unnecessary processing
                }
            },
            'http_headers': _get_client_headers('ios'),  # Use iOS headers for speed
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
    except Exception as e:
        # If fast extraction fails, try with web client as fallback
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
                'socket_timeout': 10,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
                title = info.get('title', url)
                with queue_lock:
                    if job_id in download_status:
                        download_status[job_id]['title'] = title
        except:
            with queue_lock:
                if job_id in download_status:
                    # Show URL if title extraction fails
                    video_id = normalized_url.split('v=')[-1].split('&')[0] if 'v=' in normalized_url else 'Unknown'
                    download_status[job_id]['title'] = f'Video {video_id[:11]}...'


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
        download_queue.append((job_id, normalized_url, quality))
        download_status[job_id] = {
            'status': 'queued',
            'progress': 0,
            'title': 'Extracting video info...',
            'error': None,
            'filename': None,
            'url': url,  # Keep original URL for display
            'quality': quality,
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
                files.append({
                    'name': file,
                    'size': os.path.getsize(file_path)
                })
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


@app.route('/open_file_in_folder/<filename>')
def open_file_in_folder(filename):
    """Open a specific file's location in the file explorer"""
    import platform
    import subprocess
    
    try:
        file_path = os.path.join(DOWNLOADS_DIR, secure_filename(filename))
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        if platform.system() == 'Windows':
            # Windows: open folder and select the file
            subprocess.Popen(['explorer', '/select,', file_path])
        elif platform.system() == 'Darwin':  # macOS
            # macOS: reveal file in Finder
            subprocess.Popen(['open', '-R', file_path])
        else:  # Linux
            # Linux: open folder (file selection varies by file manager)
            subprocess.Popen(['xdg-open', DOWNLOADS_DIR])
        return jsonify({'success': True, 'message': 'File location opened'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
