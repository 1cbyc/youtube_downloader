import os
from app.video_downloader import VideoDownloader

def main():
    # to prompt the user for the video URL and download location
    video_url = input("Enter the YouTube video URL: ")
    download_path = input("Enter the download path (leave blank for current directory): ")

    # Use current directory if no path is provided
    if not download_path:
        download_path = os.getcwd()

    # Create an instance of VideoDownloader
    downloader = VideoDownloader(video_url, download_path)
    downloader.get_video()
    downloader.download_video()

if __name__ == "__main__":
    main()
