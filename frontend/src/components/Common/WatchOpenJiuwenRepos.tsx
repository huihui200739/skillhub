// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Star, Check, Loader2 } from 'lucide-react'
import { starAllRepos } from '@/api/githubWatch'
import { getSiteConfig } from '@/api/playground'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'

const STAR_CLICKED_PREFIX = 'marketplace_github_star_clicked_'

/** 顶栏一键标星按钮：点击后短暂显示"标星中…"过渡态，再变为"已标星"。 */
export function WatchOpenJiuwenRepos() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { isAuthenticated, provider, user } = useGitCodeAuth()

  // 功能开关：从 /site/config 读取，默认 true（fail-open，配置接口异常时不隐藏功能）
  const [enabled, setEnabled] = useState(true)

  // 标星记录按用户隔离：key 含 provider + login，切换/退出账号后自动重置
  const starKey = user ? `${STAR_CLICKED_PREFIX}${provider}:${user.login}` : null
  const [clicked, setClicked] = useState(false)
  // 点击瞬间的过渡态：短暂显示"标星中…"让用户感知操作已触发
  const [flashing, setFlashing] = useState(false)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 拉取功能开关
  useEffect(() => {
    getSiteConfig()
      .then(cfg => setEnabled(cfg.github_star_enabled))
      .catch(() => {})
  }, [])

  // 用户变化（登录/退出/切换）时重新读取该用户的标星记录
  useEffect(() => {
    if (!starKey) {
      setClicked(false)
      return
    }
    try {
      setClicked(localStorage.getItem(starKey) === '1')
    } catch {
      setClicked(false)
    }
  }, [starKey])

  useEffect(() => {
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current)
    }
  }, [])

  const handleClick = useCallback(() => {
    if (!isAuthenticated) {
      setPostLoginRedirect('/')
      // 用 URL 参数传递来源标记，LoginPage 据此只显示 GitHub 登录按钮
      navigate('/login?from=star')
      return
    }
    // 乐观更新：立即记录并显示"已标星"，不等 GitHub 返回
    if (starKey) {
      try {
        localStorage.setItem(starKey, '1')
      } catch {
        /* ignore */
      }
    }
    setClicked(true)
    // 短暂过渡态，让用户感知到点击生效（800ms 后切回"已标星"）
    setFlashing(true)
    if (flashTimer.current) clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setFlashing(false), 800)

    // 后台异步标星（fire-and-forget）；PUT 幂等，失败不影响 UI。
    // 失败时回滚乐观更新：清除 localStorage + 重置 clicked，让用户可以重试。
    starAllRepos().catch(err => {
      console.warn('github star failed (background):', err)
      if (starKey) {
        try { localStorage.removeItem(starKey) } catch { /* ignore */ }
      }
      setClicked(false)
    })
  }, [isAuthenticated, navigate, starKey])

  // 功能关闭或 gitcode 登录用户不显示（只面向 GitHub 用户）；未登录也显示
  if (!enabled || (isAuthenticated && provider !== 'github')) return null

  const labelKey = flashing
    ? 'plugins.githubWatch.starAllLoading'
    : clicked
      ? 'plugins.githubWatch.alreadyStarred'
      : 'plugins.githubWatch.starAll'

  return (
    <button
      type="button"
      onClick={handleClick}
      title={t('plugins.githubWatch.starAllTip')}
      className={`inline-flex h-8 items-center gap-1 rounded-full px-2 text-xs font-medium shadow-sm transition-all sm:gap-1.5 sm:px-3 sm:text-sm ${
        clicked
          ? 'border border-amber-200 bg-amber-50 text-amber-600'
          : 'border border-slate-200 bg-white/80 text-slate-600 hover:border-amber-300 hover:bg-amber-50 hover:text-amber-600'
      } focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]`}
    >
      {flashing ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
      ) : clicked ? (
        <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />
      ) : (
        <Star className="h-3.5 w-3.5 shrink-0 hover:fill-amber-400" aria-hidden />
      )}
      <span className="hidden sm:inline truncate max-w-[160px]">
        {t(labelKey)}
      </span>
    </button>
  )
}
