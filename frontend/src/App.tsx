import { Routes, Route, Outlet } from 'react-router-dom'
import { Suspense } from 'react'
import LoginPage from '@/pages/LoginPage'
import MyPluginDetailPage from '@/pages/MyPluginDetailPage'
import MyProfilePage from '@/pages/MyProfilePage'
import PluginMarketPage from '@/pages/PluginMarketPage'
import SkillDetailPage from '@/pages/SkillDetailPage'
import PublishPluginPage from '@/pages/PublishPluginPage'
import { PublishDrawerProvider, usePublishDrawer } from '@/contexts/PublishDrawer'
import { PublishDrawer } from '@/components/Publish/PublishDrawer'
import { PrivacyStatementCorner } from '@/components/Common/PrivacyStatementCorner'
import PrivacyStatementPage from '@/pages/PrivacyStatementPage'

/** 消费 context 并把抽屉挂在全局，避免 context 文件持有业务组件引用。 */
function GlobalPublishDrawer() {
  const { open, closePublish } = usePublishDrawer()
  return <PublishDrawer open={open} onClose={closePublish} />
}

/** 主应用视口（固定高度 + 裁剪溢出）；隐私声明独立全页滚动，不套在此容器内。 */
function MainAppShell() {
  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden">
      <Outlet />
    </div>
  )
}

function App() {
  return (
    <Suspense fallback={<div className="flex min-h-dvh items-center justify-center">Loading...</div>}>
      <PublishDrawerProvider>
        <>
          <Routes>
            <Route path="/privacy-statement" element={<PrivacyStatementPage />} />
            <Route element={<MainAppShell />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/profile/plugins/:assetId" element={<MyPluginDetailPage />} />
              <Route path="/profile/publish" element={<PublishPluginPage />} />
              <Route path="/profile" element={<MyProfilePage />} />
              <Route path="/skills/:assetId" element={<SkillDetailPage />} />
              <Route path="/" element={<PluginMarketPage />} />
            </Route>
          </Routes>
          <PrivacyStatementCorner />
          <GlobalPublishDrawer />
        </>
      </PublishDrawerProvider>
    </Suspense>
  )
}

export default App
