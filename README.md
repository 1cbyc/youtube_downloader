# YouTube Video Downloader

I needed a simple web app to download YouTube videos that would be perfect for my wife to downloading our kids' videos!

## What it has rn

- Simple web interface - just paste a YouTube URL and click download
- Download videos in best quality or smaller file size
- Real-time download queue with progress tracking
- View and download previously downloaded videos
- Beautiful, responsive UI that's easy to use

## Screenshots

### Dark Mode
![Dark Mode UI](screenshots/Screenshot%202026-01-17%20at%208.41.41%20AM.png)

### Light Mode  
![Light Mode UI](screenshots/Screenshot%202026-01-17%20at%208.41.57%20AM.png)

## Installation

1. Create a virtual environment:
```bash
python -m venv venv
```

2. Activate the virtual environment:
   - On Windows (PowerShell):
   ```bash
   .\venv\Scripts\Activate.ps1
   ```
   - On Windows (Command Prompt):
   ```bash
   venv\Scripts\activate
   ```
   - On macOS/Linux:
   ```bash
   source venv/bin/activate
   ```

3. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Make sure the virtual environment is activated, then start the application:
```bash
python app.py
```

2. Open your web browser and go to:
```
http://localhost:5000
```

3. Paste a YouTube URL and click "Download Video"

## How to Use

1. Copy a YouTube video URL (from youtube.com or youtu.be)
2. Paste it into the URL field
3. Choose your preferred video quality
4. Click "Download Video"
5. Once downloaded, you can download the file from the "Downloaded Videos" section

## Requirements

- Python 3.12+
- Node.js 20+
- Flask 3.0+
- yt-dlp
- React 19+ (for frontend)

## Notes

- Downloaded videos are saved in your **Downloads/kids** folder on your computer:
  - **Windows**: `C:\Users\YourName\Downloads\kids`
  - **macOS**: `/Users/YourName/Downloads/kids`
  - **Linux**: `/home/YourName/Downloads/kids`
- The application runs on port 5000 by default
- Make sure you have enough disk space for downloaded videos
- Use the "Open Folder" button in the app to quickly access your downloaded videos

