using Microsoft.AspNetCore.Mvc;
using YoutubeDownloader.Models;
using YoutubeDownloader.Services;
using System.Text.Json;

namespace YoutubeDownloader.Controllers;

[ApiController]
[Route("api/[controller]")]
public class DownloadController : ControllerBase
{
    private readonly DownloadQueueService _queueService;
    private readonly YoutubeDownloadService _downloadService;
    private readonly ILogger<DownloadController> _logger;
    private readonly BackgroundDownloadProcessor _processor;

    public DownloadController(
        DownloadQueueService queueService,
        YoutubeDownloadService downloadService,
        BackgroundDownloadProcessor processor,
        ILogger<DownloadController> logger)
    {
        _queueService = queueService;
        _downloadService = downloadService;
        _processor = processor;
        _logger = logger;
    }

    [HttpPost]
    public async Task<IActionResult> AddToQueue([FromBody] DownloadRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.Url))
        {
            return BadRequest(new { success = false, error = "Please provide a YouTube URL" });
        }

        if (!request.Url.Contains("youtube.com") && !request.Url.Contains("youtu.be"))
        {
            return BadRequest(new { success = false, error = "Please provide a valid YouTube URL" });
        }

        var normalizedUrl = _downloadService.NormalizeYoutubeUrl(request.Url);
        var jobId = Guid.NewGuid().ToString();

        _queueService.AddToQueue(jobId, normalizedUrl, request.Quality ?? "best");

        // Extract title in background
        _ = Task.Run(async () =>
        {
            var title = await _downloadService.ExtractVideoTitleAsync(normalizedUrl);
            if (!string.IsNullOrEmpty(title))
            {
                var job = _queueService.GetJobStatus(jobId);
                if (job != null)
                {
                    job.Title = title;
                }
            }
        });

        return Ok(new { success = true, job_id = jobId, message = "Video added to download queue" });
    }

    [HttpGet("queue")]
    public IActionResult GetQueue()
    {
        var jobs = _queueService.GetAllJobs();
        return Ok(new { jobs });
    }

    [HttpGet("status/{jobId}")]
    public IActionResult GetStatus(string jobId)
    {
        var job = _queueService.GetJobStatus(jobId);
        if (job == null)
        {
            return NotFound(new { error = "Job not found" });
        }
        return Ok(job);
    }

    [HttpPost("pause/{jobId}")]
    public IActionResult Pause(string jobId)
    {
        var job = _queueService.GetJobStatus(jobId);
        if (job == null)
        {
            return NotFound(new { success = false, error = "Job not found" });
        }

        if (job.Status == "completed")
        {
            return BadRequest(new { success = false, error = "Download already completed" });
        }

        if (job.Status == "paused")
        {
            return Ok(new { success = true, message = "Download already paused" });
        }

        _queueService.PauseJob(jobId);
        return Ok(new { success = true, message = "Download paused" });
    }

    [HttpPost("resume/{jobId}")]
    public IActionResult Resume(string jobId)
    {
        var job = _queueService.GetJobStatus(jobId);
        if (job == null)
        {
            return NotFound(new { success = false, error = "Job not found" });
        }

        if (!_queueService.IsPaused(jobId))
        {
            return BadRequest(new { success = false, error = "Download is not paused" });
        }

        _queueService.ResumeJob(jobId);
        return Ok(new { success = true, message = "Download resumed" });
    }

    [HttpPost("pause-all")]
    public IActionResult PauseAll()
    {
        _queueService.PauseAll();
        return Ok(new { success = true, message = "All downloads paused" });
    }

    [HttpPost("resume-all")]
    public IActionResult ResumeAll()
    {
        var count = _queueService.ResumeAll();
        return Ok(new { success = true, message = $"Resumed {count} download(s)" });
    }

    [HttpGet("source-url/{jobId}")]
    public IActionResult GetSourceUrl(string jobId)
    {
        var job = _queueService.GetJobStatus(jobId);
        if (job == null)
        {
            return NotFound(new { success = false, error = "Job not found" });
        }

        var sourceUrl = job.SourceUrl ?? job.Url;
        if (string.IsNullOrEmpty(sourceUrl))
        {
            return NotFound(new { success = false, error = "Source URL not available yet" });
        }

        return Ok(new
        {
            success = true,
            source_url = sourceUrl,
            original_url = job.Url,
            title = job.Title
        });
    }

    [HttpGet("list-downloads")]
    public IActionResult ListDownloads()
    {
        var downloadsDir = _downloadService.GetDownloadsDirectory();
        var files = new List<object>();

        if (Directory.Exists(downloadsDir))
        {
            foreach (var file in Directory.GetFiles(downloadsDir))
            {
                if (!file.EndsWith(".part"))
                {
                    var fileInfo = new FileInfo(file);
                    files.Add(new
                    {
                        name = Path.GetFileName(file),
                        size = fileInfo.Length,
                        modified = ((DateTimeOffset)fileInfo.LastWriteTime).ToUnixTimeSeconds()
                    });
                }
            }
        }

        // Sort by modification time (newest first)
        files = files.OrderByDescending(f => ((dynamic)f).modified).ToList();

        return Ok(new { files });
    }

    [HttpGet("download-file/{filename}")]
    public IActionResult DownloadFile(string filename)
    {
        var downloadsDir = _downloadService.GetDownloadsDirectory();
        var decodedFilename = Uri.UnescapeDataString(filename);
        var filePath = Path.Combine(downloadsDir, decodedFilename);

        if (!System.IO.File.Exists(filePath))
        {
            return NotFound(new { error = "File not found" });
        }

        var fileBytes = System.IO.File.ReadAllBytes(filePath);
        return File(fileBytes, "application/octet-stream", Path.GetFileName(filePath));
    }

    [HttpGet("open-folder")]
    public IActionResult OpenFolder()
    {
        try
        {
            var downloadsDir = _downloadService.GetDownloadsDirectory();
            
            if (OperatingSystem.IsWindows())
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = downloadsDir,
                    UseShellExecute = true
                });
            }
            else if (OperatingSystem.IsMacOS())
            {
                System.Diagnostics.Process.Start("open", downloadsDir);
            }
            else
            {
                System.Diagnostics.Process.Start("xdg-open", downloadsDir);
            }
            
            return Ok(new { success = true, message = "Folder opened" });
        }
        catch (Exception ex)
        {
            return StatusCode(500, new { success = false, error = ex.Message });
        }
    }

    [HttpGet("open-file-in-folder/{filename}")]
    public IActionResult OpenFileInFolder(string filename)
    {
        try
        {
            var downloadsDir = _downloadService.GetDownloadsDirectory();
            var decodedFilename = Uri.UnescapeDataString(filename);
            var filePath = Path.Combine(downloadsDir, decodedFilename);

            if (!System.IO.File.Exists(filePath))
            {
                // Try case-insensitive search
                var files = Directory.GetFiles(downloadsDir);
                var foundFile = files.FirstOrDefault(f => 
                    Path.GetFileName(f).Equals(decodedFilename, StringComparison.OrdinalIgnoreCase));
                
                if (foundFile == null)
                {
                    return NotFound(new { success = false, error = $"File not found: {decodedFilename}" });
                }
                filePath = foundFile;
            }

            if (OperatingSystem.IsWindows())
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "explorer.exe",
                    Arguments = $"/select,\"{filePath}\"",
                    UseShellExecute = true
                });
            }
            else if (OperatingSystem.IsMacOS())
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "open",
                    Arguments = $"-R \"{filePath}\"",
                    UseShellExecute = false
                });
            }
            else
            {
                var dirPath = Path.GetDirectoryName(filePath) ?? downloadsDir;
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "xdg-open",
                    Arguments = dirPath,
                    UseShellExecute = false
                });
            }

            return Ok(new { success = true, message = "File location opened" });
        }
        catch (Exception ex)
        {
            return StatusCode(500, new { success = false, error = $"Error opening file: {ex.Message}" });
        }
    }

    [HttpPost("prioritize/{jobId}/{direction}")]
    public IActionResult Prioritize(string jobId, string direction)
    {
        // Note: Full prioritize implementation would require queue reordering
        // For now, return a placeholder response
        return Ok(new { success = true, message = "Prioritize feature coming soon" });
    }
}

public class DownloadRequest
{
    public string Url { get; set; } = string.Empty;
    public string? Quality { get; set; }
}

