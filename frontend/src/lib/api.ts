import axios from 'axios'
import type { VideoInfo, DownloadJob, DownloadHistory, HistoryAnalytics } from '../types'

const api = axios.create({
  baseURL: import.meta.env.DEV ? '' : '', // Use proxy in dev, empty in production (same origin)
  headers: {
    'Content-Type': 'application/json',
  },
})

export const videoApi = {
  getVideoInfo: async (url: string): Promise<VideoInfo> => {
    const { data } = await api.post<{ success: boolean; data?: VideoInfo; error?: string }>('/video_info', { url })
    if (!data.success) {
      throw new Error(data.error || 'Failed to get video info')
    }
    // Handle both data.data and direct data response
    if (data.data) {
      return data.data
    }
    // If data is directly the VideoInfo (fallback)
    return data as unknown as VideoInfo
  },

  addToQueue: async (params: {
    url: string
    quality: string
    format_id?: string
    throttle_speed?: number
    is_playlist?: boolean
    playlist_videos?: string[]
  }) => {
    const { data } = await api.post<{ success: boolean; job_id?: string; job_ids?: string[]; error?: string; error_type?: string }>('/download', params)
    if (!data.success) {
      throw new Error(data.error || 'Failed to add to queue')
    }
    return data
  },

  getQueue: async (): Promise<{ jobs: DownloadJob[] }> => {
    const { data } = await api.get<{ jobs: DownloadJob[] }>('/queue')
    return data
  },

  pauseDownload: async (jobId: string) => {
    const { data } = await api.post<{ success: boolean; error?: string }>(`/pause/${jobId}`)
    if (!data.success) {
      throw new Error(data.error || 'Failed to pause')
    }
    return data
  },

  resumeDownload: async (jobId: string) => {
    const { data } = await api.post<{ success: boolean; error?: string }>(`/resume/${jobId}`)
    if (!data.success) {
      throw new Error(data.error || 'Failed to resume')
    }
    return data
  },

  pauseAll: async () => {
    const { data } = await api.post<{ success: boolean; message?: string; error?: string }>('/pause_all')
    if (!data.success) {
      throw new Error(data.error || 'Failed to pause all')
    }
    return data
  },

  resumeAll: async () => {
    const { data } = await api.post<{ success: boolean; message?: string; error?: string }>('/resume_all')
    if (!data.success) {
      throw new Error(data.error || 'Failed to resume all')
    }
    return data
  },

  getDownloads: async (): Promise<{ files: string[] }> => {
    const { data } = await api.get<{ files: string[] }>('/list_downloads')
    return { files: data.files || [] }
  },

  downloadFile: (filename: string) => {
    return `/download_file/${encodeURIComponent(filename)}`
  },

  getHistory: async (): Promise<{ history: DownloadHistory[]; analytics: HistoryAnalytics }> => {
    const { data } = await api.get<{ success: boolean; history: DownloadHistory[]; analytics: HistoryAnalytics }>('/history')
    if (!data.success) {
      throw new Error('Failed to get history')
    }
    return { history: data.history, analytics: data.analytics }
  },
}
