# YouTube Video Downloader

I needed a simple web app to download YouTube videos that would be perfect for my wife to downloading our kids' videos!

## What it has rn

- Simple web interface - just paste a YouTube URL and click download
- Download videos in best quality or smaller file size
- Real-time download queue with progress tracking
- View and download previously downloaded videos
- Beautiful, responsive UI that's easy to use

## Screenshots

### Dark & Light Mode

<table>
<tr>
<td width="50%">

**Dark Mode**

<img src="screenshots/Screenshot-2026-01-17-at-8.41.41-AM.png" alt="Dark Mode UI" width="100%"/>

</td>
<td width="50%">

**Light Mode**

<img src="screenshots/Screenshot-2026-01-17-at-8.41.57-AM.png" alt="Light Mode UI" width="100%"/>

</td>
</tr>
</table>

### Download Queue & Progress

<table>
<tr>
<td width="50%">

**Download Queue with Progress**

<img src="screenshots/Screenshot-2026-01-17-at-9.32.44-AM.png" alt="Download Progress" width="100%"/>

</td>
<td width="50%">

**Download Details**

<img src="screenshots/Screenshot-2026-01-17-at-11.05.25-AM.png" alt="Download Details" width="100%"/>

</td>
</tr>
</table>

### File Management

<table>
<tr>
<td width="50%">

**Downloaded Files & History**

<img src="screenshots/Screenshot-2026-01-17-at-9.32.51-AM.png" alt="Downloaded Videos" width="100%"/>

</td>
<td width="50%">

**Downloaded Files List**

<img src="screenshots/Screenshot-2026-01-17-at-10.47.37-AM.png" alt="Downloaded Files" width="100%"/>

</td>
</tr>
</table>

### Format Selection & Custom Options

<table>
<tr>
<td width="50%">

**Format Selection List**

<img src="screenshots/Screenshot-2026-01-17-at-11.05.50-AM.png" alt="Format Selection" width="100%"/>

</td>
<td width="50%">

**Custom Format Options**

<img src="screenshots/Screenshot-2026-01-17-at-10.47.49-AM.png" alt="Custom Format" width="100%"/>

</td>
</tr>
</table>

### Additional Features

<table>
<tr>
<td width="50%">

**Video Preview**

<img src="screenshots/Screenshot-2026-01-17-at-10.47.58-AM.png" alt="Video Preview" width="100%"/>

</td>
<td width="50%">

**Download Settings**

<img src="screenshots/Screenshot-2026-01-17-at-10.48.04-AM.png" alt="Download Settings" width="100%"/>

</td>
</tr>
</table>

<table>
<tr>
<td width="50%">

**Mobile View**

<img src="screenshots/Screenshot-2026-01-17-at-10.57.34-AM.png" alt="Mobile View" width="100%"/>

</td>
<td width="50%">

**Error Handling**

<img src="screenshots/Screenshot-2026-01-17-at-11.01.00-AM.png" alt="Error Handling" width="100%"/>

</td>
</tr>
</table>

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

