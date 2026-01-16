import { useState, useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTheme } from './hooks/useTheme'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Card, CardHeader, CardTitle, CardContent } from './components/ui/card'
import { Progress } from './components/ui/progress'
import { videoApi } from './lib/api'
import type { VideoInfo, DownloadJob, DownloadHistory, HistoryAnalytics } from './types'
import { Moon, Sun, Play, Pause, Download, Eye, History, RefreshCw, FolderOpen, Gauge } from 'lucide-react'
import { cn } from './lib/utils'

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
  const [downloads, setDownloads] = useState<string[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<DownloadHistory[]>([])
  const [analytics, setAnalytics] = useState<HistoryAnalytics | null>(null)

  // Load saved progress from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('downloadProgress')
      if (saved) {
        const progress = JSON.parse(saved)
        // Restore any relevant state
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

  const handlePreview = async () => {
    if (!url.trim()) {
      setError('Please enter a YouTube URL')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const info = await videoApi.getVideoInfo(url)
      setVideoInfo(info)
    } catch (e: any) {
      setError(e.message || 'Failed to load video info')
      setVideoInfo(null)
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = async () => {
    if (!url.trim()) {
      setError('Please enter a YouTube URL')
      return
    }
    if (quality === 'custom' && !formatId) {
      setError('Please select a format')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await videoApi.addToQueue({
        url,
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
          <div className="container mx-auto px-4 py-4 flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                YouTube Downloader
              </h1>
              <p className="text-sm text-muted-foreground mt-1">Download your favorite videos</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              className="rounded-full"
            >
              {theme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </Button>
          </div>
        </header>

        <main className="container mx-auto px-4 py-8 max-w-6xl">
          {/* Download Form */}
          <Card className="mb-8">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Download className="h-5 w-5" />
                Download Video
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">YouTube URL</label>
                <div className="flex gap-2">
                  <Input
                    type="text"
                    placeholder="https://www.youtube.com/watch?v=..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="flex-1"
                  />
                  <Button onClick={handlePreview} variant="outline" size="icon">
                    <Eye className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {videoInfo && (
                <Card className="bg-muted/50 p-4">
                  <div className="flex gap-4 flex-col sm:flex-row">
                    {videoInfo.thumbnail && (
                      <img
                        src={videoInfo.thumbnail}
                        alt={videoInfo.title}
                        className="w-full sm:w-48 h-auto rounded-md"
                      />
                    )}
                    <div className="flex-1">
                      <h3 className="font-semibold mb-2">{videoInfo.title}</h3>
                      {videoInfo.duration && (
                        <p className="text-sm text-muted-foreground">
                          Duration: {formatDuration(videoInfo.duration)}
                        </p>
                      )}
                    </div>
                  </div>
                </Card>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Quality</label>
                  <Select
                    value={quality}
                    onChange={(e) => {
                      setQuality(e.target.value)
                      setShowThrottle(e.target.value === 'custom')
                    }}
                  >
                    <option value="best">Best Quality</option>
                    <option value="worst">Lower Quality (Smaller File)</option>
                    <option value="custom">Custom Format</option>
                  </Select>
                </div>

                {quality === 'custom' && videoInfo?.formats && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Select Format</label>
                    <Select value={formatId} onChange={(e) => setFormatId(e.target.value)}>
                      <option value="">Select format...</option>
                      {videoInfo.formats.map((fmt) => (
                        <option key={fmt.format_id} value={fmt.format_id}>
                          {fmt.ext.toUpperCase()} - {fmt.resolution || 'Unknown'} -{' '}
                          {fmt.filesize ? `${(fmt.filesize / (1024 * 1024)).toFixed(2)} MB` : 'Unknown size'}
                        </option>
                      ))}
                    </Select>
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
                <div className="p-3 rounded-md bg-destructive/10 border border-destructive text-destructive text-sm">
                  {error}
                </div>
              )}

              <Button
                onClick={handleDownload}
                disabled={loading}
                className="w-full"
                size="lg"
              >
                {loading ? 'Adding...' : 'Add to Queue'}
              </Button>
            </CardContent>
          </Card>

          {/* Queue */}
          <Card className="mb-8">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  <Play className="h-5 w-5" />
                  Download Queue
                </CardTitle>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handlePauseAll}>
                    <Pause className="h-4 w-4 mr-2" />
                    Pause All
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleResumeAll}>
                    <Play className="h-4 w-4 mr-2" />
                    Resume All
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {jobs.length === 0 ? (
                <p className="text-muted-foreground text-center py-8">No downloads in queue</p>
              ) : (
                <div className="space-y-4">
                  {jobs.map((job) => (
                    <Card key={job.job_id} className="bg-muted/50">
                      <CardContent className="p-4">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex-1 min-w-0">
                            <h4 className="font-semibold truncate">{job.title}</h4>
                            <div className="mt-2 space-y-1">
                              <Progress value={job.progress} className="h-2" />
                              <div className="flex items-center justify-between text-xs text-muted-foreground">
                                <span>{job.progress}%</span>
                                {job.speed && <span>{job.speed}</span>}
                              </div>
                            </div>
                            {job.error && (
                              <p className="text-sm text-destructive mt-2">{job.error}</p>
                            )}
                            {job.status === 'completed' && job.filename && (
                              <a
                                href={videoApi.downloadFile(job.filename)}
                                className="text-sm text-primary hover:underline mt-2 inline-block"
                              >
                                Download: {job.filename}
                              </a>
                            )}
                          </div>
                          <div className="flex gap-2">
                            {job.status === 'paused' ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleResume(job.job_id)}
                              >
                                <Play className="h-4 w-4" />
                              </Button>
                            ) : job.status !== 'completed' && job.status !== 'failed' ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handlePause(job.job_id)}
                              >
                                <Pause className="h-4 w-4" />
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
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2">
                    <FolderOpen className="h-5 w-5" />
                    Downloaded Files
                  </CardTitle>
                  <Button variant="ghost" size="icon" onClick={() => window.location.reload()}>
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {downloads.length === 0 ? (
                  <p className="text-muted-foreground text-center py-8">No downloads yet</p>
                ) : (
                  <div className="space-y-2">
                    {downloads.map((file) => (
                      <a
                        key={file}
                        href={videoApi.downloadFile(file)}
                        className="block p-2 rounded-md hover:bg-muted text-sm"
                      >
                        {file}
                      </a>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <History className="h-5 w-5" />
                  Download History
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={handleHistory}
                >
                  {showHistory ? 'Hide' : 'Show'} History & Analytics
                </Button>
                {showHistory && analytics && (
                  <div className="mt-4 space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="p-3 rounded-md bg-muted">
                        <p className="text-xs text-muted-foreground">Total Downloads</p>
                        <p className="text-2xl font-bold">{analytics.total_downloads}</p>
                      </div>
                      <div className="p-3 rounded-md bg-muted">
                        <p className="text-xs text-muted-foreground">Success Rate</p>
                        <p className="text-2xl font-bold">{analytics.success_rate}%</p>
                      </div>
                    </div>
                    <div className="space-y-2 max-h-64 overflow-y-auto">
                      {history.map((entry) => (
                        <div
                          key={entry.job_id}
                          className="p-2 rounded-md bg-muted/50 text-sm"
                        >
                          <div className="flex items-center justify-between">
                            <span className={cn(
                              entry.status === 'completed' ? 'text-primary' : 'text-destructive'
                            )}>
                              {entry.status === 'completed' ? '✓' : '✗'} {entry.title}
                            </span>
                            {entry.file_size && (
                              <span className="text-xs text-muted-foreground">
                                {(entry.file_size / (1024 * 1024)).toFixed(2)} MB
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
