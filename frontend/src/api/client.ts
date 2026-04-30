// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import type { AxiosInstance } from 'axios'
import { getStoredOAuthProvider, getStoredOAuthToken } from '@/auth/gitcodeStorage'
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
      const t = getStoredOAuthToken()
      if (t) {
        config.headers.Authorization = `Bearer ${t}`
        config.headers['X-OAuth-Provider'] = getStoredOAuthProvider()
      } else {
        delete config.headers.Authorization
        delete config.headers['X-OAuth-Provider']
      }
      return config
    })
  }
  return apiClient
}
