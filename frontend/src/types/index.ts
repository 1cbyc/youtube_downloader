export interface VideoInfo {
  title: string;
  thumbnail: string;
  duration: number;
  formats: Format[];
  is_playlist: boolean;
  playlist_videos?: string[];
}

export interface Format {
  format_id: string;
  ext: string;
  resolution?: string;
  filesize?: number;
}

export interface DownloadJob {
  job_id: string;
  status: 'queued' | 'downloading' | 'paused' | 'completed' | 'failed';
  progress: number;
  title: string;
  error?: string;
  filename?: string;
  url: string;
  quality: string;
  format_id?: string;
  thumbnail?: string;
  estimated_size?: string;
  speed?: string;
  queue_position?: number;
  completed_at?: string;
  failed_at?: string;
}

export interface DownloadHistory {
  job_id: string;
  title: string;
  url: string;
  filename?: string;
  file_size?: number;
  status: 'completed' | 'failed';
  completed_at?: string;
  failed_at?: string;
  error?: string;
  client_ip: string;
  quality: string;
  format_id?: string;
}

export interface HistoryAnalytics {
  total_downloads: number;
  total_failed: number;
  success_rate: number;
  total_size_gb: number;
  total_size_mb: number;
}
