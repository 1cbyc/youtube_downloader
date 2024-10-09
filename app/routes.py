import re
from flask import render_template, request, redirect, url_for, send_file
from app import app
from pytube import YouTube
import os

# i decided that i need to make the code automatically remove unecessary params in the yt video url
DOWNLOAD_FOLDER = "downloads/"

# the function to clean up the YouTube URL
def clean_youtube_url(url):
    # regex to match yt video id patterns
    match = re.match(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+", url)
    if match:
        # will remove query parameters, only keep the base video link
        cleaned_url = url.split('?')[0]
        return cleaned_url
    return None


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
