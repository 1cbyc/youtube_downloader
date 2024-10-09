from pytube import YouTube
from tqdm import tqdm
import os

class VideoDownloader:
    def __init__(self, url, path):
        self.url = url
        self.path = path
        self.yt = None

    def get_video(self):
        try:
            self.yt = YouTube(self.url)
            print(f"Title: {self.yt.title}")
            print(f"Views: {self.yt.views}")
            print(f"Length: {self.yt.length // 60} minutes")
        except Exception as e:
            print(f"An error occurred while fetching video: {e}")
            return None

    def download_video(self):
        if not self.yt:
            print("You must call get_video() first.")
            return

        print("Downloading...")
        video_stream = self.yt.streams.get_highest_resolution()

        try:
            # will use tqdm to display progress bar
            video_stream.download(output_path=self.path, filename=self.yt.title)
            print("Download completed!")
        except Exception as e:
            print(f"An error occurred during download: {e}")
