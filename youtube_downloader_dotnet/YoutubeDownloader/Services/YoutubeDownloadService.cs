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
        
        // Try with iOS client first (fastest and most reliable)
        var clients = new[] { "ios", "android", "web" };
        
        foreach (var client in clients)
        {
            try
            {
                var arguments = $"--flat-playlist --skip-download --print title " +
                    $"--extractor-args \"youtube:player_client={client}\" " +
                    $"--user-agent \"{GetClientUserAgent(client)}\" " +
                    $"--socket-timeout 10 \"{normalizedUrl}\"";

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
                var output = await process.StandardOutput.ReadToEndAsync();
                await process.WaitForExitAsync();

                if (process.ExitCode == 0 && !string.IsNullOrWhiteSpace(output))
                {
                    return output.Trim();
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Error extracting video title with client {Client}, trying next", client);
                continue;
            }
        }

        return null;
    }

    public async Task<bool> DownloadVideoAsync(string jobId, string url, string quality, 
        DownloadQueueService queueService, CancellationToken cancellationToken)
    {
        // Check if paused before starting
        if (queueService.IsPaused(jobId))
        {
            var status = queueService.GetStatusDictionary();
            if (status.TryGetValue(jobId, out var job))
            {
                job.Status = "paused";
            }
            return false;
        }

        var normalizedUrl = NormalizeYoutubeUrl(url);
        var formatSelector = quality == "best" 
            ? "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            : "worst[ext=mp4]/worst";

        var jobStatus = queueService.GetStatusDictionary();
        if (!jobStatus.TryGetValue(jobId, out var initialJob))
        {
            jobStatus[jobId] = new DownloadJob
            {
                JobId = jobId,
                Status = "downloading",
                Progress = 0,
                Title = "Extracting video info...",
                Url = normalizedUrl,
                Quality = quality
            };
        }
        else
        {
            if (!queueService.IsPaused(jobId))
            {
                initialJob.Status = "downloading";
            }
            else
            {
                initialJob.Status = "paused";
                return false;
            }
        }

        // Try multiple clients in sequence (multi-client fallback strategy)
        var clientPriority = new[] { "ios", "android", "tv", "web" };
        string? lastError = null;

        foreach (var client in clientPriority)
        {
            // Check if paused before each client attempt
            if (queueService.IsPaused(jobId))
            {
                if (jobStatus.TryGetValue(jobId, out var pausedJob))
                {
                    pausedJob.Status = "paused";
                }
                return false;
            }

            try
            {
                var outputTemplate = Path.Combine(_downloadsDir, "%(title)s.%(ext)s");
                
                // Build yt-dlp arguments with client-specific options
                var arguments = $"--no-warnings --progress --newline " +
                    $"--output \"{outputTemplate}\" " +
                    $"--format \"{formatSelector}\" " +
                    $"--extractor-args \"youtube:player_client={client};player_skip=webpage,configs\" " +
                    $"--user-agent \"{GetClientUserAgent(client)}\" " +
                    $"--retries 5 --fragment-retries 5 --file-access-retries 3 " +
                    $"--socket-timeout 30 --hls-prefer-native " +
                    $"--continue \"{normalizedUrl}\"";

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

                // Read progress output and errors
                string? line;
                var errorOutputBuilder = new System.Text.StringBuilder();
                
                // Read stderr for error messages in background
                var errorTask = Task.Run(async () =>
                {
                    string? errorLine;
                    while ((errorLine = await process.StandardError.ReadLineAsync()) != null)
                    {
                        errorOutputBuilder.AppendLine(errorLine);
                    }
                });

                while ((line = await process.StandardOutput.ReadLineAsync()) != null)
                {
                if (cancellationToken.IsCancellationRequested || queueService.IsPaused(jobId))
                {
                    process.Kill();
                    if (jobStatus.TryGetValue(jobId, out var cancelledJob))
                    {
                        cancelledJob.Status = "paused";
                    }
                    return false;
                }

                    // Parse progress (yt-dlp format: [download] X.X% of Y.YMiB at Z.ZMiB/s ETA HH:MM:SS)
                    var progressMatch = Regex.Match(line, @"\[download\]\s+(\d+\.?\d*)%");
                    if (progressMatch.Success && jobStatus.TryGetValue(jobId, out var progressJob))
                    {
                        if (int.TryParse(progressMatch.Groups[1].Value.Split('.')[0], out var progress))
                        {
                            progressJob.Progress = Math.Min(99, progress);
                        }

                        // Extract speed
                        var speedMatch = Regex.Match(line, @"at\s+(\d+\.?\d*\w+)/s");
                        if (speedMatch.Success)
                        {
                            progressJob.Speed = speedMatch.Groups[1].Value + "/s";
                        }
                    }

                    // Extract title if available
                    if (line.Contains("title") && jobStatus.TryGetValue(jobId, out var titleJob))
                    {
                        if (string.IsNullOrEmpty(titleJob.Title) || titleJob.Title == "Extracting video info...")
                        {
                            var titleMatch = Regex.Match(line, @"\[info\]\s+(.+)");
                            if (titleMatch.Success)
                            {
                                titleJob.Title = titleMatch.Groups[1].Value.Trim();
                            }
                        }
                    }
                }

                // Wait for both stdout and stderr to finish
                await process.WaitForExitAsync();
                await errorTask; // Wait for error reading to complete
                
                var errorOutput = errorOutputBuilder.ToString();

                // Check if paused after download completes
                if (queueService.IsPaused(jobId))
                {
                    if (jobStatus.TryGetValue(jobId, out var pausedJob2))
                    {
                        pausedJob2.Status = "paused";
                    }
                    return false;
                }

                if (process.ExitCode == 0)
                {
                    // Find downloaded file
                    var filename = FindDownloadedFile(normalizedUrl);
                    if (!string.IsNullOrEmpty(filename) && jobStatus.TryGetValue(jobId, out var completedJob))
                    {
                        completedJob.Status = "completed";
                        completedJob.Progress = 100;
                        completedJob.Filename = filename;
                        completedJob.CompletedAt = DateTime.Now;
                        return true;
                    }
                }
                else
                {
                    // Check error output for specific error types that should trigger fallback
                    if (errorOutput.Contains("HTTP Error 403") || 
                        errorOutput.Contains("HTTP Error 400") || 
                        errorOutput.Contains("Precondition check failed") ||
                        errorOutput.Contains("403") ||
                        errorOutput.Contains("Forbidden"))
                    {
                        // This client failed, try next one
                        lastError = errorOutput;
                        _logger.LogWarning("Client {Client} failed for {Url}, trying next client. Error: {Error}", 
                            client, normalizedUrl, errorOutput.Length > 200 ? errorOutput.Substring(0, 200) : errorOutput);
                        continue;
                    }
                    else
                    {
                        // Other error, but still try next client
                        lastError = errorOutput;
                        continue;
                    }
                }
            }
            catch (Exception ex)
            {
                lastError = ex.Message;
                _logger.LogWarning(ex, "Error with client {Client} for {Url}, trying next client", client, normalizedUrl);
                continue;
            }
        }

        // All clients failed
        if (jobStatus.TryGetValue(jobId, out var failedJob))
        {
            failedJob.Status = "failed";
            failedJob.Error = lastError ?? "All client fallbacks failed";
        }
        return false;
    }

    private string GetClientUserAgent(string client)
    {
        return client switch
        {
            "ios" => "com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)",
            "android" => "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
            "tv" => "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version",
            "web" => "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            _ => "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        };
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

