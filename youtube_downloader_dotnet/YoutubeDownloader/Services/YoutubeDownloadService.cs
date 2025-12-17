using System.Diagnostics;
using System.Text.RegularExpressions;
using YoutubeDownloader.Models;

namespace YoutubeDownloader.Services;

public class YoutubeDownloadService
{
    private readonly ILogger<YoutubeDownloadService> _logger;
    private readonly string _downloadsDir;
    private readonly string? _ytDlpPath;

    public YoutubeDownloadService(ILogger<YoutubeDownloadService> logger, IWebHostEnvironment env)
    {
        _logger = logger;
        
        // Get Downloads folder
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var downloads = Path.Combine(home, "Downloads", "kids");
        Directory.CreateDirectory(downloads);
        _downloadsDir = downloads;

        // Find yt-dlp executable
        // Try common locations: venv Scripts, system PATH, or Python installation
        _ytDlpPath = FindYtDlpPath();
        
        if (string.IsNullOrEmpty(_ytDlpPath))
        {
            _logger.LogWarning("yt-dlp not found. Please install yt-dlp or ensure it's in PATH.");
        }
    }

    private string? FindYtDlpPath()
    {
        // Check if yt-dlp is in PATH
        var pathDirs = Environment.GetEnvironmentVariable("PATH")?.Split(Path.PathSeparator) ?? Array.Empty<string>();
        foreach (var dir in pathDirs)
        {
            var ytDlpExe = Path.Combine(dir, "yt-dlp.exe");
            if (File.Exists(ytDlpExe))
                return ytDlpExe;
        }

        // Check common Python venv locations
        var currentDir = Directory.GetCurrentDirectory();
        var venvPath = Path.Combine(currentDir, "..", "..", "venv", "Scripts", "yt-dlp.exe");
        if (File.Exists(venvPath))
            return Path.GetFullPath(venvPath);

        // Try Python -m yt_dlp
        try
        {
            var pythonProcess = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = "python",
                    Arguments = "-m yt_dlp --version",
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }
            };
            pythonProcess.Start();
            pythonProcess.WaitForExit(2000);
            if (pythonProcess.ExitCode == 0)
            {
                return "python"; // Use Python module
            }
        }
        catch { }

        return null;
    }

    public string NormalizeYoutubeUrl(string url)
    {
        // Extract video ID from various YouTube URL formats
        var patterns = new[]
        {
            @"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})",
            @"youtube\.com\/.*[?&]v=([a-zA-Z0-9_-]{11})"
        };

        foreach (var pattern in patterns)
        {
            var match = Regex.Match(url, pattern);
            if (match.Success && match.Groups.Count > 1)
            {
                var videoId = match.Groups[1].Value;
                return $"https://www.youtube.com/watch?v={videoId}";
            }
        }

        return url;
    }

    public async Task<string?> ExtractVideoTitleAsync(string url)
    {
        var normalizedUrl = NormalizeYoutubeUrl(url);
        
        try
        {
            var process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = _ytDlpPath ?? "yt-dlp",
                    Arguments = $"--flat-playlist --skip-download --print title \"{normalizedUrl}\"",
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }
            };

            process.Start();
            var output = await process.StandardOutput.ReadToEndAsync();
            await process.WaitForExitAsync();

            if (process.ExitCode == 0 && !string.IsNullOrWhiteSpace(output))
            {
                return output.Trim();
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error extracting video title");
        }

        return null;
    }

    public async Task<bool> DownloadVideoAsync(string jobId, string url, string quality, 
        DownloadQueueService queueService, CancellationToken cancellationToken)
    {
        var normalizedUrl = NormalizeYoutubeUrl(url);
        var formatSelector = quality == "best" 
            ? "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            : "worst[ext=mp4]/worst";

        try
        {
            var status = queueService.GetStatusDictionary();
            if (status.TryGetValue(jobId, out var job))
            {
                job.Status = "downloading";
                job.Progress = 0;
            }

            var outputTemplate = Path.Combine(_downloadsDir, "%(title)s.%(ext)s");
            var arguments = $"--no-warnings --progress --newline --output \"{outputTemplate}\" --format \"{formatSelector}\" \"{normalizedUrl}\"";

            var process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = _ytDlpPath ?? "yt-dlp",
                    Arguments = arguments,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }
            };

            process.Start();

            // Read progress output
            string? line;
            while ((line = await process.StandardOutput.ReadLineAsync()) != null)
            {
                if (cancellationToken.IsCancellationRequested)
                {
                    process.Kill();
                    if (status.TryGetValue(jobId, out var job2))
                    {
                        job2.Status = "paused";
                    }
                    return false;
                }

                // Parse progress (yt-dlp format: [download] X.X% of Y.YMiB at Z.ZMiB/s ETA HH:MM:SS)
                var progressMatch = Regex.Match(line, @"\[download\]\s+(\d+\.?\d*)%");
                if (progressMatch.Success && status.TryGetValue(jobId, out var job3))
                {
                    if (int.TryParse(progressMatch.Groups[1].Value.Split('.')[0], out var progress))
                    {
                        job3.Progress = Math.Min(99, progress);
                    }

                    // Extract speed
                    var speedMatch = Regex.Match(line, @"at\s+(\d+\.?\d*\w+)/s");
                    if (speedMatch.Success)
                    {
                        job3.Speed = speedMatch.Groups[1].Value + "/s";
                    }
                }
            }

            await process.WaitForExitAsync();

            if (process.ExitCode == 0)
            {
                // Find downloaded file
                var filename = FindDownloadedFile(normalizedUrl);
                if (!string.IsNullOrEmpty(filename) && status.TryGetValue(jobId, out var job4))
                {
                    job4.Status = "completed";
                    job4.Progress = 100;
                    job4.Filename = filename;
                    job4.CompletedAt = DateTime.Now;
                    return true;
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error downloading video");
            var status = queueService.GetStatusDictionary();
            if (status.TryGetValue(jobId, out var job))
            {
                job.Status = "failed";
                job.Error = ex.Message;
            }
        }

        return false;
    }

    private string? FindDownloadedFile(string url)
    {
        // This is a simplified version - in production, you'd track the expected filename
        // For now, return the most recently modified file
        if (!Directory.Exists(_downloadsDir))
            return null;

        var files = Directory.GetFiles(_downloadsDir)
            .Where(f => !f.EndsWith(".part"))
            .OrderByDescending(f => File.GetLastWriteTime(f))
            .FirstOrDefault();

        return files != null ? Path.GetFileName(files) : null;
    }

    public string GetDownloadsDirectory() => _downloadsDir;
}

