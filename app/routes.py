import re
from flask import render_template, request, redirect, url_for, send_file
from Flask import render_template, request, redirect, url_for, send_file
from app import app
from pytube import YouTube
import os

# i decided that i need to make the code automatically remove unecessary params in the yt video url
# app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads/"

# have to create downloads directory if it doesn't exist
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)
    
# the function to clean up the YouTube URL
def clean_youtube_url(url):
    # regex to match yt video id patterns
    match = re.match(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+", url)
    
    if match:
        # will extract video ID and form the standard YouTube URL
        video_id = match.group(6)
        cleaned_url = f"https://www.youtube.com/watch?v={video_id}"
        # will remove query parameters, to only keep the base video link
        # cleaned_url = url.split('?')[0]
        return cleaned_url
    return None


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        video_url = request.form['video_url']
        
        # decided to add a cleaned url process after receiving input now
        cleaned_url = clean_youtube_url(video_url)
        if not cleaned_url:
            return render_template('index.html', error="Invalid YouTube URL.")
        
        try:
            # yt = YouTube(video_url) # assuming i did not clean the url
            yt = YouTube(cleaned_url)  # will use the cleaned URL instead of video_url
            # video = yt.streams.get_highest_resolution()
            
            video = yt.streams.filter(progressive=True, file_extension='mp4').first()  # will select the first available stream instead
            if not video:
                raise Exception("No suitable video stream found.")
            
            video.download(output_path=DOWNLOAD_FOLDER)
            file_path = os.path.join(DOWNLOAD_FOLDER, yt.title + ".mp4")
            return send_file(file_path, as_attachment=True)
        except Exception as e:
            # return render_template('index.html', error=str(e))
            return render_template('index.html', error=f"Error downloading video: {str(e)}")
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
