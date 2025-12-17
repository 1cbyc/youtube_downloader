using YoutubeDownloader.Services;

namespace YoutubeDownloader.Services;

public class BackgroundDownloadProcessor : BackgroundService
{
    private readonly DownloadQueueService _queueService;
    private readonly YoutubeDownloadService _downloadService;
    private readonly ILogger<BackgroundDownloadProcessor> _logger;

    public BackgroundDownloadProcessor(
        DownloadQueueService queueService,
        YoutubeDownloadService downloadService,
        ILogger<BackgroundDownloadProcessor> logger)
    {
        _queueService = queueService;
        _downloadService = downloadService;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var job = _queueService.GetNextJob();
                if (job.HasValue)
                {
                    var (jobId, url, quality) = job.Value;
                    
                    // Check if paused before starting
                    if (_queueService.IsPaused(jobId))
                    {
                        var status = _queueService.GetStatusDictionary();
                        if (status.TryGetValue(jobId, out var jobStatus))
                        {
                            jobStatus.Status = "paused";
                        }
                        _queueService.MarkJobComplete();
                        continue;
                    }

                    var cancellationTokenSource = new CancellationTokenSource();
                    
                    // Check for pause during download
                    _ = Task.Run(async () =>
                    {
                        while (!cancellationTokenSource.Token.IsCancellationRequested)
                        {
                            await Task.Delay(500, cancellationTokenSource.Token);
                            if (_queueService.IsPaused(jobId))
                            {
                                cancellationTokenSource.Cancel();
                            }
                        }
                    });

                    try
                    {
                        await _downloadService.DownloadVideoAsync(
                            jobId, url, quality, _queueService, cancellationTokenSource.Token);
                    }
                    catch (OperationCanceledException)
                    {
                        _logger.LogInformation("Download {JobId} was paused", jobId);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError(ex, "Error downloading {JobId}", jobId);
                        var status = _queueService.GetStatusDictionary();
                        if (status.TryGetValue(jobId, out var jobStatus))
                        {
                            jobStatus.Status = "failed";
                            jobStatus.Error = ex.Message;
                        }
                    }
                    finally
                    {
                        _queueService.MarkJobComplete();
                    }
                }
                else
                {
                    await Task.Delay(500, stoppingToken);
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error in download processor");
                await Task.Delay(1000, stoppingToken);
            }
        }
    }
}

