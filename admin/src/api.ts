import axios, { AxiosInstance, AxiosError } from 'axios'

// 后端 API 基础路径（开发走 vite proxy，生产由 FastAPI 同域提供）
export const API_BASE = '/api/sample'

const TOKEN_KEY = 'fast_admin_token'
const USER_KEY = 'fast_admin_user'

export interface AdminUser {
  id: number
  username: string
  role: string
  is_active: boolean
  created_at?: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: AdminUser
}

export interface Paged<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

// ---------- token 存取 ----------
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
export function getSavedUser(): AdminUser | null {
  const raw = localStorage.getItem(USER_KEY)
  return raw ? JSON.parse(raw) : null
}
export function setSavedUser(u: AdminUser) {
  localStorage.setItem(USER_KEY, JSON.stringify(u))
}

// ---------- axios 实例 ----------
export const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const t = getToken()
  if (t) config.headers.Authorization = `Bearer ${t}`
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      clearToken()
      // 避免在登录页循环跳转
      if (!location.pathname.endsWith('/admin/login') && !location.pathname.endsWith('/admin/')) {
        const redirect = encodeURIComponent(location.pathname + location.search)
        location.href = `/admin/login?redirect=${redirect}`
      }
    }
    return Promise.reject(error)
  },
)

// ---------- API ----------
export const authApi = {
  async login(username: string, password: string): Promise<LoginResponse> {
    const { data } = await http.post('/user/login', { username, password })
    // 登录接口只返回 token，需再调 /user/me 拿用户信息
    setToken(data.access_token)
    try {
      const me = await userApi.me()
      setSavedUser(me)
      return { ...data, user: me }
    } catch {
      clearToken()
      throw new Error('登录成功但获取用户信息失败')
    }
  },
  logout() {
    clearToken()
  },
}

export const userApi = {
  async me(): Promise<AdminUser> {
    const { data } = await http.get('/user/me')
    return data
  },
  async list(params: { page?: number; page_size?: number } = {}): Promise<Paged<AdminUser>> {
    const { data } = await http.get('/admin/users', { params })
    return data
  },
  async update(id: number, patch: { role?: string; is_active?: boolean }): Promise<AdminUser> {
    const { data } = await http.put(`/admin/users/${id}`, patch)
    return data
  },
}

export interface Beam {
  id: number
  name: string
  type: number
  is_active: boolean
  owner_id: number
  created_at: string
  modified_at: string
}

export const resourceApi = {
  async listBeams(params: {
    page?: number
    page_size?: number
    beam_type?: number
    owner_id?: number
    name_like?: string
  } = {}): Promise<Paged<Beam>> {
    const { data } = await http.get('/resource/admin/beams', { params })
    return data
  },
  async deleteBeam(id: number) {
    await http.delete(`/resource/beams/${id}`)
  },
}

export interface RagDoc {
  id: number
  title: string
  summary: string
  source: string
  chunk_count: number
  owner_id: number
  is_active: boolean
  created_at: string
  modified_at: string
}

export const ragApi = {
  async listDocs(params: { page?: number; page_size?: number; owner_id?: number } = {}): Promise<Paged<RagDoc>> {
    const { data } = await http.get('/rag/admin/docs', { params })
    return data
  },
  async stats(): Promise<{
    total_docs: number
    total_chunks: number
    index_size: number
    has_llm: boolean
    fallback_embedding: boolean
  }> {
    const { data } = await http.get('/rag/stats')
    return data
  },
  async deleteDoc(id: number) {
    await http.delete(`/rag/docs/${id}`)
  },
}

export interface LlmStats {
  call_count: number
  cost_total_usd: number
  providers: string[]
  default_provider: string
  enabled_providers: Record<string, { type: string; model: string; priority: number }>
  metrics: Record<string, unknown>
}

export const llmApi = {
  async stats(): Promise<LlmStats> {
    const { data } = await http.get('/llm/stats')
    return data
  },
  async health(): Promise<Record<string, unknown>> {
    const { data } = await http.get('/llm/health')
    return data
  },
}

export const monitorApi = {
  async readyz() {
    const { data } = await http.get('/monitor/readyz')
    return data
  },
  async healthz() {
    const { data } = await http.get('/monitor/healthz')
    return data
  },
  async metrics(): Promise<string> {
    const { data } = await http.get('/monitor/metrics', { responseType: 'text', transformResponse: [(d) => d] })
    return data
  },
}
