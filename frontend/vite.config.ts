// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

function devApiProxyTarget(env: Record<string, string>): string {
  const host = (env.BACKEND_URL || 'localhost').trim() || 'localhost'
  const port = (env.BACKEND_PORT || '8100').trim() || '8100'
  return `http://${host}:${port}`
}

/** 仓库根 .env 中 FRONTEND_BASE_PATH：去掉首尾斜杠；空或未设置 = 站点根路径（无前缀）。例：`hub` → 应用挂在 `/hub`。 */
function vitePublicBase(env: Record<string, string>): string {
  const raw = (env.FRONTEND_BASE_PATH || '').trim().replace(/^\/+|\/+$/g, '')
  return raw ? `/${raw}` : '/'
}

export default defineConfig(({ mode }) => {
  const envDir = path.resolve(__dirname, '..')
  const env = loadEnv(mode, envDir, '')

  const publicBase = vitePublicBase(env)
  const apiBase = (env.VITE_API_BASE_URL || '/api/v1').trim() || '/api/v1'
  const apiBaseNorm = apiBase.replace(/\/$/, '') || '/api/v1'

  const apiProxy: Record<string, object> = {
    '/api': {
      target: devApiProxyTarget(env),
      changeOrigin: true,
    },
  }
  // 本地若 VITE_API_BASE_URL 与站点前缀一致（如 /hub/api/v1），需把 /hub/api 剥到 /api 再代理
  if (publicBase !== '/' && apiBaseNorm.startsWith(`${publicBase}/api`)) {
    const esc = publicBase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    apiProxy[`${publicBase}/api`] = {
      target: devApiProxyTarget(env),
      changeOrigin: true,
      rewrite: (p: string) => p.replace(new RegExp(`^${esc}`), ''),
    }
  }

  const minioPrefix = (env.VITE_MINIO_DEV_PROXY_PREFIX || '/__minio').replace(/\/$/, '') || '/__minio'
  const minioTarget = (env.VITE_MINIO_PROXY_TARGET || env.VITE_MINIO_BROWSER_ORIGIN || '').trim()

  const proxy: Record<string, object> = { ...apiProxy }
  if (minioTarget) {
    proxy[minioPrefix] = {
      target: minioTarget,
      changeOrigin: true,
      rewrite: (p: string) => p.replace(new RegExp(`^${minioPrefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), ''),
    }
  }

  return {
    envDir,
    base: publicBase === '/' ? '/' : publicBase,
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: parseInt(env.FRONTEND_PORT || '9002', 10),
      host: env.HOST || '0.0.0.0',
      proxy,
    },
  }
})
