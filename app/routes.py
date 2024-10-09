from flask import render_template, request, redirect, url_for, send_file
from app import app
from pytube import YouTube
import os

DOWNLOAD_FOLDER = "downloads/"

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        video_url = request.form['video_url']
        try:
            yt = YouTube(video_url)
            video = yt.streams.get_highest_resolution()
            video.download(output_path=DOWNLOAD_FOLDER)
            file_path = os.path.join(DOWNLOAD_FOLDER, yt.title + ".mp4")
            return send_file(file_path, as_attachment=True)
        except Exception as e:
            # return render_template('index.html', error=str(e))
            return render_template('index.html', error=f"Error downloading video: {str(e)}")
    return render_template('index.html')
