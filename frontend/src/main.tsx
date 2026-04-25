import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from 'react-query'
import { BrowserRouter } from 'react-router-dom'
import { setApiBaseUrl } from '@/api'
import { GitCodeAuthProvider } from '@/auth/GitCodeAuthContext'
import { ENV_CONFIG } from '@/config/environment'
import './i18n'
import App from './App'
import './index.css'

setApiBaseUrl(ENV_CONFIG.API_BASE_URL)

/** 默认不做后台自动重拉：避免列表/详情周期性或切窗时整页重绘跳动；需要最新数据请用各页「刷新」或会触发 invalidate 的写操作。 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Number.POSITIVE_INFINITY,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '') || undefined}>
        <GitCodeAuthProvider>
          <App />
        </GitCodeAuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
