namespace YoutubeDownloader.Models;

public class DownloadJob
{
    public string JobId { get; set; } = string.Empty;
    public string Status { get; set; } = "queued"; // queued, downloading, paused, completed, failed
    public int Progress { get; set; } = 0;
    public string Title { get; set; } = "Extracting video info...";
    public string? Error { get; set; }
    public string? Filename { get; set; }
    public string? SourceUrl { get; set; }
    public string Url { get; set; } = string.Empty;
    public string Quality { get; set; } = "best";
    public string? Speed { get; set; }
    public int? QueuePosition { get; set; }
    public DateTime? AddedAt { get; set; }
    public DateTime? CompletedAt { get; set; }
}

