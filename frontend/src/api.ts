export type Episode = {
  title: string
  part: number
  aid: number
  bvid: string
  cid: number
  duration: number
  url: string
  cover: string
  uploader: string
  pubtime: number
}

export type ParsedResult = {
  type: string
  title: string
  bvid: string
  aid: number
  cover: string
  uploader: string
  description: string
  episodes: Episode[]
  pagination?: {
    current_page: number
    page_size: number
    total_items: number
    total_pages: number
  }
}

export type Task = {
  id: string
  title: string
  url: string
  bvid: string
  cid: number
  status: string
  progress: number
  speed: number
  downloaded_size: number
  total_size: number
  error: string
  output_dir: string
  output_file: string
}

export type BatchJob = {
  id: string
  source_url: string
  status: string
  total: number
  created: number
  current_page: number
  page_size: number
  total_pages: number
  total_items: number
  completed_pages: number
  error: string
}

const tokenKey = 'bili23_token'

export function getToken() {
  return localStorage.getItem(tokenKey) || ''
}

export function setToken(token: string) {
  localStorage.setItem(tokenKey, token)
}

export function clearToken() {
  localStorage.removeItem(tokenKey)
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(path, { ...options, headers })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return response.json()
}

export function formatBytes(value: number) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[index]}`
}
