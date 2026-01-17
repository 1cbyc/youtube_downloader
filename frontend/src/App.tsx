import { useState, useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTheme } from './hooks/useTheme'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Card, CardHeader, CardTitle, CardContent } from './components/ui/card'
import { Progress } from './components/ui/progress'
import { videoApi } from '@/lib/api'
import type { VideoInfo, DownloadJob, DownloadHistory, HistoryAnalytics, DownloadedFile } from './types'
import { Moon, Sun, Play, Pause, Download, Eye, History, RefreshCw, FolderOpen, Gauge, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 2000, // Poll every 2 seconds
      refetchOnWindowFocus: true,
    },
  },
})

function App() {
  const { theme, toggleTheme } = useTheme()
  const [url, setUrl] = useState('')
  const [quality, setQuality] = useState('best')
  const [formatId, setFormatId] = useState<string>('')
  const [throttleSpeed, setThrottleSpeed] = useState<number>(0)
  const [showThrottle, setShowThrottle] = useState(false)
  const [videoInfo, setVideoInfo] = useState<VideoInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [downloads, setDownloads] = useState<DownloadedFile[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<DownloadHistory[]>([])
  const [analytics, setAnalytics] = useState<HistoryAnalytics | null>(null)

  // Load saved progress from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('downloadProgress')
      if (saved) {
        // Progress is loaded and used by the download status polling
        // No need to restore state here as polling will update it
      }
    } catch (e) {
      console.error('Failed to load saved progress:', e)
    }
  }, [])

  // Fetch queue
  useEffect(() => {
    const fetchQueue = async () => {
      try {
        const data = await videoApi.getQueue()
        setJobs(data.jobs)
      } catch (e) {
        console.error('Failed to fetch queue:', e)
      }
    }
    fetchQueue()
    const interval = setInterval(fetchQueue, 2000)
    return () => clearInterval(interval)
  }, [])

  // Fetch downloads
  useEffect(() => {
    const fetchDownloads = async () => {
      try {
        const data = await videoApi.getDownloads()
        setDownloads(data.files)
      } catch (e) {
        console.error('Failed to fetch downloads:', e)
      }
    }
    fetchDownloads()
    const interval = setInterval(fetchDownloads, 5000)
    return () => clearInterval(interval)
  }, [])

  const normalizeUrl = (url: string): string => {
    const trimmed = url.trim()
    // If URL doesn't start with http:// or https://, add https://
    if (trimmed && !trimmed.match(/^https?:\/\//i)) {
      // Check if it looks like a YouTube URL
      if (trimmed.includes('youtube.com') || trimmed.includes('youtu.be')) {
        return `https://${trimmed}`
      }
    }
    return trimmed
  }

  const handlePreview = async () => {
    if (!url.trim()) {
      setError('Please enter a YouTube URL')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const normalizedUrl = normalizeUrl(url)
      const info = await videoApi.getVideoInfo(normalizedUrl)
      setVideoInfo(info)
    } catch (e: any) {
      setError(e.message || 'Failed to load video info')
      setVideoInfo(null)
    } finally {
      setLoading(false)
    }
  }

  const handleClearPreview = () => {
    setVideoInfo(null)
    setFormatId('')
    setError(null)
  }

  const handleDownload = async () => {
    if (!url.trim()) {
      setError('Please enter a YouTube URL')
      return
    }
    if (quality === 'custom') {
      if (!videoInfo) {
        setError('Please preview the video first to see available formats')
        return
      }
      if (!formatId) {
        setError('Please select a format from the dropdown')
        return
      }
    }
    setLoading(true)
    setError(null)
    try {
      const normalizedUrl = normalizeUrl(url)
      await videoApi.addToQueue({
        url: normalizedUrl,
        quality: quality === 'custom' ? 'best' : quality,
        format_id: formatId || undefined,
        throttle_speed: throttleSpeed > 0 ? throttleSpeed : undefined,
        is_playlist: videoInfo?.is_playlist,
        playlist_videos: videoInfo?.playlist_videos,
      })
      setUrl('')
      setVideoInfo(null)
      setFormatId('')
      setThrottleSpeed(0)
      setError(null)
    } catch (e: any) {
      setError(e.message || 'Failed to add to queue')
    } finally {
      setLoading(false)
    }
  }

  const handlePause = async (jobId: string) => {
    try {
      await videoApi.pauseDownload(jobId)
    } catch (e: any) {
      setError(e.message || 'Failed to pause')
    }
  }

  const handleResume = async (jobId: string) => {
    try {
      await videoApi.resumeDownload(jobId)
    } catch (e: any) {
      setError(e.message || 'Failed to resume')
    }
  }

  const handlePauseAll = async () => {
    try {
      await videoApi.pauseAll()
    } catch (e: any) {
      setError(e.message || 'Failed to pause all')
    }
  }

  const handleResumeAll = async () => {
    try {
      await videoApi.resumeAll()
    } catch (e: any) {
      setError(e.message || 'Failed to resume all')
    }
  }

  const handleHistory = async () => {
    if (!showHistory) {
      try {
        const data = await videoApi.getHistory()
        setHistory(data.history)
        setAnalytics(data.analytics)
      } catch (e: any) {
        setError(e.message || 'Failed to load history')
      }
    }
    setShowHistory(!showHistory)
  }

  const formatDuration = (seconds: number) => {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = Math.floor(seconds % 60)
    if (hours > 0) {
      return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-background text-foreground font-sans">
        {/* Header */}
        <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-50">
          <div className="container mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center justify-between">
            <div className="min-w-0 flex-1">
              <h1 className="text-xl sm:text-2xl md:text-3xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent truncate">
                1cbyc - YouTube Downloader
              </h1>
              <p className="text-xs sm:text-sm text-muted-foreground mt-0.5 hidden sm:block">Download your favorite videos</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              className="rounded-full flex-shrink-0 ml-2"
            >
              {theme === 'dark' ? <Sun className="h-4 w-4 sm:h-5 sm:w-5" /> : <Moon className="h-4 w-4 sm:h-5 sm:w-5" />}
            </Button>
          </div>
        </header>

        <main className="container mx-auto px-3 sm:px-4 py-4 sm:py-6 md:py-8 max-w-6xl">
          {/* Download Form */}
          <Card className="mb-4 sm:mb-6 md:mb-8">
            <CardHeader className="pb-3 sm:pb-4">
              <CardTitle className="flex items-center gap-2 text-lg sm:text-xl">
                <Download className="h-4 w-4 sm:h-5 sm:w-5" />
                Download Video
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 sm:space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">YouTube URL</label>
                <div className="flex gap-2">
                  <Input
                    type="text"
                    placeholder="Paste YouTube URL here..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="flex-1 text-sm sm:text-base"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !loading) {
                        handleDownload()
                      }
                    }}
                  />
                  <Button onClick={handlePreview} variant="outline" size="icon" className="flex-shrink-0">
                    <Eye className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {videoInfo && (
                <Card className="bg-muted/50 p-3 sm:p-4 relative">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleClearPreview}
                    className="absolute top-2 right-2 h-6 w-6 sm:h-7 sm:w-7 opacity-70 hover:opacity-100"
                  >
                    <X className="h-3 w-3 sm:h-4 sm:w-4" />
                  </Button>
                  <div className="flex gap-3 sm:gap-4 flex-col sm:flex-row pr-6 sm:pr-8">
                    {videoInfo.thumbnail && (
                      <img
                        src={videoInfo.thumbnail}
                        alt={videoInfo.title}
                        className="w-full sm:w-40 md:w-48 h-auto rounded-md object-cover"
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold mb-1 sm:mb-2 text-sm sm:text-base break-words">{videoInfo.title}</h3>
                      {videoInfo.duration && (
                        <p className="text-xs sm:text-sm text-muted-foreground">
                          Duration: {formatDuration(videoInfo.duration)}
                        </p>
                      )}
                    </div>
                  </div>
                </Card>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Quality</label>
                  <Select
                    value={quality}
                    onChange={(e) => {
                      const newQuality = e.target.value
                      setQuality(newQuality)
                      setShowThrottle(newQuality === 'custom')
                      // Clear format selection when switching away from custom
                      if (newQuality !== 'custom') {
                        setFormatId('')
                      }
                    }}
                    className="text-sm sm:text-base"
                  >
                    <option value="best">Best Quality</option>
                    <option value="worst">Smaller File Size</option>
                    <option value="custom">Custom Format</option>
                  </Select>
                </div>

                {quality === 'custom' && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Select Format</label>
                    {!videoInfo ? (
                      <div className="p-3 rounded-md bg-muted/50 border border-border text-sm text-muted-foreground">
                        Click the <Eye className="h-3 w-3 inline mx-1" /> icon to preview and see available formats
                      </div>
                    ) : !videoInfo.formats || videoInfo.formats.length === 0 ? (
                      <div className="p-3 rounded-md bg-muted/50 border border-border text-sm text-muted-foreground">
                        No formats available. Try previewing again.
                      </div>
                    ) : (
                      <Select value={formatId} onChange={(e) => setFormatId(e.target.value)}>
                        <option value="">Select format...</option>
                        {videoInfo.formats.map((fmt) => (
                          <option key={fmt.format_id} value={fmt.format_id}>
                            {fmt.ext.toUpperCase()} - {fmt.resolution || 'Unknown'} -{' '}
                            {fmt.filesize ? `${(fmt.filesize / (1024 * 1024)).toFixed(2)} MB` : 'Unknown size'}
                          </option>
                        ))}
                      </Select>
                    )}
                  </div>
                )}

                {showThrottle && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium flex items-center gap-2">
                      <Gauge className="h-4 w-4" />
                      Download Speed Limit (MB/s)
                    </label>
                    <Input
                      type="number"
                      min="0"
                      max="100"
                      step="0.1"
                      value={throttleSpeed}
                      onChange={(e) => setThrottleSpeed(parseFloat(e.target.value) || 0)}
                      placeholder="0 = unlimited"
                    />
                    <p className="text-xs text-muted-foreground">Set to 0 for unlimited speed</p>
                  </div>
                )}
              </div>

              {error && (
                <div className="p-3 rounded-md bg-destructive/10 border border-destructive text-destructive text-sm break-words">
                  {error}
                </div>
              )}

              <Button
                onClick={handleDownload}
                disabled={loading || !url.trim()}
                className="w-full text-base sm:text-lg font-semibold py-6 sm:py-7"
                size="lg"
              >
                {loading ? 'Starting...' : 'Start Downloading'}
              </Button>
            </CardContent>
          </Card>

          {/* Queue */}
          <Card className="mb-4 sm:mb-6 md:mb-8">
            <CardHeader className="pb-3 sm:pb-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <CardTitle className="flex items-center gap-2 text-lg sm:text-xl">
                  <Play className="h-4 w-4 sm:h-5 sm:w-5" />
                  Download Queue
                </CardTitle>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handlePauseAll} className="text-xs sm:text-sm">
                    <Pause className="h-3 w-3 sm:h-4 sm:w-4 mr-1 sm:mr-2" />
                    <span className="hidden sm:inline">Pause All</span>
                    <span className="sm:hidden">Pause</span>
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleResumeAll} className="text-xs sm:text-sm">
                    <Play className="h-3 w-3 sm:h-4 sm:w-4 mr-1 sm:mr-2" />
                    <span className="hidden sm:inline">Resume All</span>
                    <span className="sm:hidden">Resume</span>
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-3 sm:p-4">
              {jobs.length === 0 ? (
                <p className="text-muted-foreground text-center py-6 sm:py-8 text-sm sm:text-base">No downloads in queue</p>
              ) : (
                <div className="space-y-3 sm:space-y-4">
                  {jobs.map((job) => (
                    <Card key={job.job_id} className="bg-muted/50">
                      <CardContent className="p-3 sm:p-4">
                        <div className="flex items-start justify-between gap-3 sm:gap-4">
                          <div className="flex-1 min-w-0">
                            <h4 className="font-semibold truncate text-sm sm:text-base">{job.title}</h4>
                            <div className="mt-2 space-y-1">
                              <Progress value={job.progress} className="h-1.5 sm:h-2" />
                              <div className="flex items-center justify-between text-xs text-muted-foreground">
                                <span>{job.progress}%</span>
                                {job.speed && <span className="truncate ml-2">{job.speed}</span>}
                              </div>
                            </div>
                            {job.error && (
                              <p className="text-xs sm:text-sm text-destructive mt-2 break-words">{job.error}</p>
                            )}
                            {job.status === 'completed' && job.filename && (
                              <a
                                href={videoApi.downloadFile(job.filename)}
                                className="text-xs sm:text-sm text-primary hover:underline mt-2 inline-block break-all"
                              >
                                Download: {job.filename}
                              </a>
                            )}
                          </div>
                          <div className="flex gap-2 flex-shrink-0">
                            {job.status === 'paused' ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleResume(job.job_id)}
                                className="h-8 w-8 sm:h-9 sm:w-9 p-0"
                              >
                                <Play className="h-3 w-3 sm:h-4 sm:w-4" />
                              </Button>
                            ) : job.status !== 'completed' && job.status !== 'failed' ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handlePause(job.job_id)}
                                className="h-8 w-8 sm:h-9 sm:w-9 p-0"
                              >
                                <Pause className="h-3 w-3 sm:h-4 sm:w-4" />
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Downloads & History */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 md:gap-8">
            <Card>
              <CardHeader className="pb-3 sm:pb-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                    <FolderOpen className="h-4 w-4 sm:h-5 sm:w-5" />
                    Downloaded Files
                  </CardTitle>
                  <Button variant="ghost" size="icon" onClick={() => window.location.reload()} className="h-8 w-8 sm:h-9 sm:w-9">
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="p-3 sm:p-4">
                {downloads.length === 0 ? (
                  <p className="text-muted-foreground text-center py-6 sm:py-8 text-sm sm:text-base">No downloads yet</p>
                ) : (
                  <div className="space-y-2">
                    {downloads.map((file) => (
                      <a
                        key={file.name}
                        href={videoApi.downloadFile(file.name)}
                        className="block p-2.5 sm:p-3 rounded-lg hover:bg-muted/80 transition-colors border border-border/50 active:bg-muted"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs sm:text-sm font-medium truncate flex-1 min-w-0">{file.name}</span>
                          <span className="text-xs text-muted-foreground whitespace-nowrap flex-shrink-0">
                            {(file.size / (1024 * 1024)).toFixed(1)} MB
                          </span>
                        </div>
                      </a>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3 sm:pb-4">
                <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                  <History className="h-4 w-4 sm:h-5 sm:w-5" />
                  Download History
                </CardTitle>
              </CardHeader>
              <CardContent className="p-3 sm:p-4">
                <Button
                  variant="outline"
                  className="w-full text-sm sm:text-base"
                  onClick={handleHistory}
                >
                  {showHistory ? 'Hide' : 'Show'} History & Analytics
                </Button>
                {showHistory && analytics && (
                  <div className="mt-3 sm:mt-4 space-y-3 sm:space-y-4">
                    <div className="grid grid-cols-2 gap-2 sm:gap-4">
                      <div className="p-2.5 sm:p-3 rounded-md bg-muted">
                        <p className="text-xs text-muted-foreground">Total Downloads</p>
                        <p className="text-xl sm:text-2xl font-bold">{analytics.total_downloads}</p>
                      </div>
                      <div className="p-2.5 sm:p-3 rounded-md bg-muted">
                        <p className="text-xs text-muted-foreground">Success Rate</p>
                        <p className="text-xl sm:text-2xl font-bold">{analytics.success_rate}%</p>
                      </div>
                    </div>
                    <div className="space-y-2 max-h-48 sm:max-h-64 overflow-y-auto">
                      {history.map((entry) => (
                        <div
                          key={entry.job_id}
                          className="p-2 sm:p-2.5 rounded-md bg-muted/50 text-xs sm:text-sm"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className={cn(
                              entry.status === 'completed' ? 'text-primary' : 'text-destructive',
                              "truncate flex-1"
                            )}>
                              {entry.status === 'completed' ? '✓' : '✗'} {entry.title}
                            </span>
                            {entry.file_size && (
                              <span className="text-xs text-muted-foreground whitespace-nowrap flex-shrink-0">
                                {(entry.file_size / (1024 * 1024)).toFixed(1)} MB
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </main>
      </div>
    </QueryClientProvider>
  )
}

export default App
