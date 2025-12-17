using System.Collections.Concurrent;
using YoutubeDownloader.Models;

namespace YoutubeDownloader.Services;

public class DownloadQueueService
{
    private readonly ConcurrentQueue<(string jobId, string url, string quality)> _downloadQueue = new();
    private readonly ConcurrentDictionary<string, DownloadJob> _downloadStatus = new();
    private readonly HashSet<string> _pausedJobs = new();
    private readonly object _queueLock = new object();
    private bool _processing = false;

    public void AddToQueue(string jobId, string url, string quality)
    {
        lock (_queueLock)
        {
            _downloadQueue.Enqueue((jobId, url, quality));
            _downloadStatus[jobId] = new DownloadJob
            {
                JobId = jobId,
                Status = "queued",
                Progress = 0,
                Title = "Extracting video info...",
                Url = url,
                Quality = quality,
                AddedAt = DateTime.Now
            };
        }
    }

    public (string jobId, string url, string quality)? GetNextJob()
    {
        lock (_queueLock)
        {
            if (_downloadQueue.IsEmpty || _processing)
                return null;

            // Filter out paused jobs
            var activeJobs = new List<(string, string, string)>();
            while (_downloadQueue.TryDequeue(out var job))
            {
                if (!_pausedJobs.Contains(job.jobId))
                {
                    activeJobs.Add(job);
                }
            }

            // Re-add paused jobs back to queue
            foreach (var job in activeJobs.Skip(1))
            {
                _downloadQueue.Enqueue(job);
            }

            if (activeJobs.Count > 0)
            {
                _processing = true;
                return activeJobs[0];
            }

            return null;
        }
    }

    public void MarkJobComplete()
    {
        lock (_queueLock)
        {
            _processing = false;
        }
    }

    public void PauseJob(string jobId)
    {
        lock (_queueLock)
        {
            _pausedJobs.Add(jobId);
            if (_downloadStatus.TryGetValue(jobId, out var job))
            {
                job.Status = "paused";
            }
        }
    }

    public void ResumeJob(string jobId)
    {
        lock (_queueLock)
        {
            _pausedJobs.Remove(jobId);
            if (_downloadStatus.TryGetValue(jobId, out var job))
            {
                job.Status = "queued";
                // Re-add to queue
                _downloadQueue.Enqueue((jobId, job.Url, job.Quality));
            }
        }
    }

    public void PauseAll()
    {
        lock (_queueLock)
        {
            foreach (var (jobId, job) in _downloadStatus)
            {
                if (job.Status == "queued" || job.Status == "downloading")
                {
                    _pausedJobs.Add(jobId);
                    job.Status = "paused";
                }
            }
        }
    }

    public int ResumeAll()
    {
        lock (_queueLock)
        {
            int resumed = 0;
            foreach (var jobId in _pausedJobs.ToList())
            {
                if (_downloadStatus.TryGetValue(jobId, out var job))
                {
                    _pausedJobs.Remove(jobId);
                    job.Status = "queued";
                    _downloadQueue.Enqueue((jobId, job.Url, job.Quality));
                    resumed++;
                }
            }
            return resumed;
        }
    }

    public void PrioritizeJob(string jobId, string direction)
    {
        lock (_queueLock)
        {
            // This is a simplified version - full implementation would reorder the queue
            // For now, we'll handle this in the controller
        }
    }

    public DownloadJob? GetJobStatus(string jobId)
    {
        _downloadStatus.TryGetValue(jobId, out var job);
        return job;
    }

    public List<DownloadJob> GetAllJobs()
    {
        lock (_queueLock)
        {
            var jobs = new List<DownloadJob>();
            int position = 1;

            // Add queued jobs with positions
            foreach (var (jobId, url, quality) in _downloadQueue)
            {
                if (_downloadStatus.TryGetValue(jobId, out var job))
                {
                    job.QueuePosition = position++;
                    jobs.Add(job);
                }
            }

            // Add other status jobs
            foreach (var job in _downloadStatus.Values)
            {
                if (job.Status != "queued" || !jobs.Any(j => j.JobId == job.JobId))
                {
                    jobs.Add(job);
                }
            }

            return jobs;
        }
    }

    public ConcurrentDictionary<string, DownloadJob> GetStatusDictionary() => _downloadStatus;
    public bool IsPaused(string jobId) => _pausedJobs.Contains(jobId);
}

