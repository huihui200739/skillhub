import { Routes, Route } from 'react-router-dom'
import { Suspense } from 'react'
import LoginPage from '@/pages/LoginPage'
import MyPluginDetailPage from '@/pages/MyPluginDetailPage'
import MyProfilePage from '@/pages/MyProfilePage'
import PluginMarketPage from '@/pages/PluginMarketPage'
import PublishPluginPage from '@/pages/PublishPluginPage'
import { PublishDrawerProvider, usePublishDrawer } from '@/contexts/PublishDrawer'
import { PublishDrawer } from '@/components/Publish/PublishDrawer'

/** 消费 context 并把抽屉挂在全局，避免 context 文件持有业务组件引用。 */
function GlobalPublishDrawer() {
  const { open, closePublish } = usePublishDrawer()
  return <PublishDrawer open={open} onClose={closePublish} />
}

function App() {
  return (
    <Suspense fallback={<div className="flex min-h-dvh items-center justify-center">Loading...</div>}>
      <PublishDrawerProvider>
        <div className="flex h-dvh min-h-0 flex-col overflow-hidden">
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/profile/plugins/:assetId" element={<MyPluginDetailPage />} />
            <Route path="/profile/publish" element={<PublishPluginPage />} />
            <Route path="/profile" element={<MyProfilePage />} />
            <Route path="/" element={<PluginMarketPage />} />
          </Routes>
        </div>
        <GlobalPublishDrawer />
      </PublishDrawerProvider>
    </Suspense>
  )
}

export default App
