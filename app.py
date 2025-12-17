from flask import Flask, render_template, request, jsonify, send_file
import os
import yt_dlp
from werkzeug.utils import secure_filename
import tempfile
import shutil

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create downloads directory if it doesn't exist
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), 'downloads')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def download_video(url, quality='best'):
    """Download video from YouTube URL"""
    try:
        # Configure yt-dlp options
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s'),
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best' if quality == 'best' else 'worst',
            'quiet': False,
            'no_warnings': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first to get video title
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'video')
            
            # Download the video
            ydl.download([url])
            
            # Find the downloaded file
            filename = None
            for file in os.listdir(DOWNLOADS_DIR):
                if file.startswith(secure_filename(video_title[:50])):
                    filename = file
                    break
            
            if filename:
                return {
                    'success': True,
                    'filename': filename,
                    'title': video_title,
                    'path': os.path.join(DOWNLOADS_DIR, filename)
                }
            else:
                return {
                    'success': False,
                    'error': 'Could not find downloaded file'
                }
                
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def download():
    """Handle video download request"""
    data = request.get_json()
    url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    
    if not url:
        return jsonify({'success': False, 'error': 'Please provide a YouTube URL'}), 400
    
    # Validate YouTube URL
    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'success': False, 'error': 'Please provide a valid YouTube URL'}), 400
    
    result = download_video(url, quality)
    
    if result['success']:
        return jsonify({
            'success': True,
            'message': f'Successfully downloaded: {result["title"]}',
            'filename': result['filename']
        })
    else:
        return jsonify({
            'success': False,
            'error': result.get('error', 'Download failed')
        }), 500


@app.route('/download_file/<filename>')
def download_file(filename):
    """Serve downloaded file"""
    file_path = os.path.join(DOWNLOADS_DIR, secure_filename(filename))
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404


@app.route('/list_downloads')
def list_downloads():
    """List all downloaded files"""
    files = []
    if os.path.exists(DOWNLOADS_DIR):
        for file in os.listdir(DOWNLOADS_DIR):
            file_path = os.path.join(DOWNLOADS_DIR, file)
            if os.path.isfile(file_path):
                files.append({
                    'name': file,
                    'size': os.path.getsize(file_path)
                })
    return jsonify({'files': files})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

