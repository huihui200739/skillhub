import axios from 'axios'
import type { AxiosInstance } from 'axios'
import { getStoredGitCodeToken } from '@/auth/gitcodeStorage'
import { API_CONFIG } from './config'

let apiClient: AxiosInstance | null = null

export function getApiClient(): AxiosInstance {
  if (!apiClient) {
    apiClient = axios.create({
      baseURL: API_CONFIG.BASE_URL,
      timeout: API_CONFIG.TIMEOUT,
      headers: API_CONFIG.HEADERS,
    })
    apiClient.interceptors.request.use(config => {
      const t = getStoredGitCodeToken()
      if (t) {
        config.headers.Authorization = `Bearer ${t}`
      } else {
        delete config.headers.Authorization
      }
      return config
    })
  }
  return apiClient
}
