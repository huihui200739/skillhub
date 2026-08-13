// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Star, Loader2, ChevronLeft, ChevronRight, Code2, ChevronUp } from 'lucide-react'
import { getStarStatus, starAllRepos } from '@/api/githubWatch'
import { getSiteConfig } from '@/api/playground'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'

// 我们的 GitHub 组织地址（"代码"按钮跳转目标）
const GITHUB_ORG_URL = 'https://github.com/openJiuwen-ai'

/**
 * 右侧浮窗：完全照搬 openjiuwen.com 浮窗结构与样式。
 * 按钮组：收起 / 标星(替换客服位) / 代码 / 回到顶部（去掉吐槽）。
 * 标星按钮点击弹确认弹窗，确认后标星，标星后图标变实心。
 */
export function WatchOpenJiuwenRepos() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { isAuthenticated, provider, user } = useGitCodeAuth()

  // 功能开关：从 /site/config 读取，默认 true（fail-open）
  const [enabled, setEnabled] = useState(true)

  // 标星状态：从后端 Redis 读取（按用户隔离，跨设备同步）
  const [clicked, setClicked] = useState(false)
  // flashing 跟随真实请求生命周期（开始->true，结束->false），而非固定 800ms 定时器。
  // 后端串行标星 10 个仓库 ≈13s，期间按钮 disabled 防止连点并发多个 batch（抵消串行改造）。
  const [flashing, setFlashing] = useState(false)
  // 标星请求序号：防止快速连点时旧请求的失败回调覆盖新请求的成功状态
  const starSeq = useRef(0)
  // in-flight 锁：与 flashing 同步，disabled 按钮防止并发发起新 batch
  const starringRef = useRef(false)

  // 确认弹窗
  const [confirmOpen, setConfirmOpen] = useState(false)

  // 收起/展开状态（照搬 openjiuwen 的 collapse 交互）
  const [collapsed, setCollapsed] = useState(false)
  // 容器 hover 状态（用 React 控制收起按钮显隐，比 CSS :hover 更可靠）
  const [hovered, setHovered] = useState(false)
  // 收起按钮自身 hover 状态（内联样式优先级高于 CSS，hover 反馈需由 React state 驱动）
  const [collapseHovered, setCollapseHovered] = useState(false)

  // 拉取功能开关
  useEffect(() => {
    getSiteConfig()
      .then(cfg => setEnabled(cfg.github_star_enabled))
      .catch(() => {})
  }, [])

  // 用户变化时从后端查询标星状态（Redis，跨设备同步）
  useEffect(() => {
    if (!isAuthenticated || provider !== 'github') {
      setClicked(false)
      return
    }
    let cancelled = false
    getStarStatus()
      .then(starred => {
        if (!cancelled) setClicked(starred)
      })
      .catch(() => {
        // 查询失败（如 Redis 不可用）降级为未标星，用户可重新点（PUT 幂等无害）
        if (!cancelled) setClicked(false)
      })
    return () => { cancelled = true }
  }, [isAuthenticated, provider, user?.login])

  const doStar = useCallback(() => {
    // in-flight 锁：标星进行中（≈13s）禁用重复触发，避免并发 batch 抵消后端串行改造。
    // starSeq 防回调覆盖，starringRef 防重复发请求，两者互补。
    if (starringRef.current) return
    starringRef.current = true
    // 乐观更新：立即显示已标星，后台异步调标星接口
    const seq = ++starSeq.current
    setClicked(true)
    setFlashing(true)

    starAllRepos()
      .then(results => {
        // 过期请求的回调直接忽略，避免覆盖更新请求的结果
        if (seq !== starSeq.current) return
        const okCount = results.filter(r => r.status === 'success').length
        if (okCount === 0) {
          console.warn('github star all failed:', results)
          // 全失败回滚为未标星态（后端未写 Redis，下次查状态会返回 false）
          setClicked(false)
          // 关闭通知卡片，避免卡片显示「正在标星」与浮窗空心星状态矛盾
          setConfirmOpen(false)
        } else if (okCount < results.length) {
          console.warn('github star partial failure:', results.length - okCount, 'failed')
          // 部分成功：后端已写 Redis（success>0），保留已标星态
        }
      })
      .catch(err => {
        // 过期请求的回调直接忽略
        if (seq !== starSeq.current) return
        console.warn('github star failed (background):', err)
        // 标星请求失败：回滚为未标星态，关闭通知卡片
        setClicked(false)
        setConfirmOpen(false)
      })
      .finally(() => {
        // 请求结束才停转：flashing 跟随真实生命周期，而非固定 800ms。
        // 解决「800ms 后停转但请求仍在进行」造成的「已完成」错觉 + 可连点并发。
        if (seq === starSeq.current) {
          setFlashing(false)
        }
        starringRef.current = false
      })
  }, [])

  const handleStarClick = useCallback(() => {
    if (!isAuthenticated) {
      setPostLoginRedirect('/')
      navigate('/login?from=star')
      return
    }
    // 标星进行中（flashing）时禁用点击，防止连点并发多个 batch
    if (flashing) return
    // 已标星：直接重新标星（PUT 幂等）
    // 未标星：立即标星 + 弹通知提示（不再先弹确认再执行）
    doStar()
    setConfirmOpen(true)
  }, [isAuthenticated, navigate, doStar, flashing])

  // 回到顶部
  const scrollToTop = useCallback(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  // 通知卡片为非阻塞提示（标星后台 fire-and-forget），不锁滚动、不监听 ESC：
  // 用户可在标星进行中继续浏览页面，卡片仅靠「知道了」按钮关闭。

  // 功能关闭或 gitcode 登录用户不显示
  if (!enabled || (isAuthenticated && provider !== 'github')) return null

  const starLabel = t('plugins.githubWatch.starAll')
  const codeLabel = t('plugins.githubWatch.code')
  const topLabel = t('plugins.githubWatch.backToTop')

  // ── 浮窗（完全照搬 openjiuwen.com 结构 + 样式）──────────────
  // 外层 contactContainer：fixed 定位壳，透明
  // 内层 buttonGroup：视觉壳（白底/阴影/毛玻璃/圆角/overflow:hidden）
  // 按钮：floatButton 52×52，flex-col center，gap:2px
  // 分隔线：::after 1px×28px rgba(0,0,0,0.06)（非末尾按钮）
  // 标星按钮（替换客服位）：用 contactButton 渐变激活样式
  // 收起按钮：hover 容器时显现，点击收起/展开

  // 收起按钮样式：按 collapsed / collapseHovered 组合预计算，避免内联嵌套三元
  const GRADIENT_ACTIVE = 'linear-gradient(135deg, rgb(10, 89, 247) 0%, rgb(115, 38, 255) 100%)'
  const GRADIENT_HOVER = 'linear-gradient(135deg, rgb(240, 241, 244) 0%, rgb(232, 233, 236) 100%)'
  const GRADIENT_IDLE = 'linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(248,249,252,0.95) 100%)'
  const collapseColor = collapsed ? '#ffffff' : collapseHovered ? 'rgb(10, 89, 247)' : '#666666'
  const collapseBg = collapsed ? GRADIENT_ACTIVE : collapseHovered ? GRADIENT_HOVER : GRADIENT_IDLE
  const collapseBorder = collapsed
    ? 'none'
    : collapseHovered
      ? '1px solid rgba(10, 89, 247, 0.3)'
      : '1px solid rgba(229,229,229,0.8)'
  const collapseShadow = collapseHovered && collapsed
    ? 'rgba(10,89,247,0.55) 0px 6px 20px'
    : collapseHovered && !collapsed
      ? 'rgba(10,89,247,0.2) 0px 4px 12px'
      : collapsed
        ? 'rgba(10,89,247,0.4) 0px 4px 16px'
        : 'rgba(0,0,0,0.1) 0px 2px 8px'

  const floatingWidget = (
    <div
      className="oj-contact-container"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'fixed',
        right: collapsed ? 0 : 20,
        bottom: 24,
        zIndex: 1000,
        alignItems: 'center',
        display: 'flex',
        flexDirection: 'row',
        // 容器始终保持展开态浮窗高度（3 按钮 × 52px = 156px），收起态不塌缩。
        // 这样 absolute 收起按钮的 top/bottom 锚点稳定，位置不会随收起/展开漂移。
        height: 156,
        transition: 'right 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* 收起/展开按钮（照搬 openjiuwen collapseButton）*/}
      <button
        type="button"
        onClick={() => setCollapsed(c => !c)}
        onMouseEnter={() => setCollapseHovered(true)}
        onMouseLeave={() => setCollapseHovered(false)}
        title={collapsed ? t('plugins.githubWatch.expand') : t('plugins.githubWatch.collapse')}
        aria-label={collapsed ? t('plugins.githubWatch.expand') : t('plugins.githubWatch.collapse')}
        style={{
          cursor: 'pointer',
          // 收起态始终显示；展开态 hover 容器时显示
          opacity: collapsed || hovered ? 1 : 0,
          // hover 反馈由 React state 驱动（内联优先级高于 CSS，无法用 :hover）
          color: collapseColor,
          background: collapseBg,
          border: collapseBorder,
          justifyContent: 'center',
          alignItems: 'center',
          width: collapsed ? 32 : 20,
          height: collapsed ? 56 : 36,
          transition: '0.3s',
          display: 'flex',
          position: 'absolute',
          // 照搬 openjiuwen：同时设 top/bottom 让按钮在 156px 容器里垂直居中。
          // 展开态：(156-36)/2=60；收起态：(156-56)/2=50。
          top: collapsed ? 50 : 60,
          bottom: collapsed ? 50 : 60,
          boxShadow: collapseShadow,
          borderRadius: collapsed ? '10px 0 0 10px' : '6px 0 0 6px',
          marginRight: collapsed ? 0 : 4,
          right: '100%',
          pointerEvents: 'auto',
        }}
        className="oj-contact-collapse"
      >
        {/* 展开态箭头朝右（收起右侧按钮组），收起态箭头朝左（展开拉出），照搬 openjiuwen */}
        {collapsed ? (
          <ChevronLeft style={{ width: 16, height: 16 }} />
        ) : (
          <ChevronRight style={{ width: 12, height: 12 }} />
        )}
      </button>

      {/* 按钮组（照搬 openjiuwen buttonGroup）*/}
      <div
        style={{
          backdropFilter: 'blur(10px)',
          WebkitBackdropFilter: 'blur(10px)',
          // openjiuwen 用 grab（可拖拽），我们未实现拖拽，用 default 避免误导
          cursor: 'default',
          userSelect: 'none',
          background: 'rgba(255, 255, 255, 0.95)',
          borderRadius: 12,
          flexDirection: 'column',
          display: collapsed ? 'none' : 'flex',
          overflow: 'hidden',
          boxShadow: 'rgba(0, 0, 0, 0.12) 0px 4px 20px',
          transition: 'opacity 0.3s',
        }}
      >
        {/* 标星按钮（浅灰底，和代码/顶部统一；未标星 #333，已标星金色）*/}
        <FloatButton
          onClick={handleStarClick}
          title={t('plugins.githubWatch.starAllTip')}
          ariaLabel={starLabel}
          variant="star"
        >
          {flashing ? (
            <Loader2
              className={`oj-btn-icon oj-star-icon${clicked ? ' oj-starred' : ''} animate-spin`}
              style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
              aria-hidden
            />
          ) : (
            <Star
              className={`oj-btn-icon oj-star-icon${clicked ? ' oj-starred' : ''}`}
              style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s, fill 0.2s' }}
              strokeWidth={2}
              aria-hidden
            />
          )}
          <FloatLabel className={`oj-btn-label oj-star-label${clicked ? ' oj-starred-label' : ''}`}>{starLabel}</FloatLabel>
        </FloatButton>

        {/* 代码按钮（链到我们的 GitHub org）*/}
        <a
          href={GITHUB_ORG_URL}
          target="_blank"
          rel="noopener noreferrer"
          draggable={false}
          title={t('plugins.githubWatch.codeTip')}
          className="oj-float-btn oj-code-btn"
          style={{
            cursor: 'pointer',
            border: 'none',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 2,
            width: 52,
            height: 52,
            textDecoration: 'none',
            transition: '0.2s',
            display: 'flex',
            position: 'relative',
          }}
        >
          <Code2
            style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
            className="oj-btn-icon"
            strokeWidth={2}
            aria-hidden
          />
          <span
            className="oj-btn-label"
            style={{ fontSize: 10, fontWeight: 600, lineHeight: 1, transition: 'color 0.2s' }}
          >
            {codeLabel}
          </span>
        </a>

        {/* 回到顶部按钮（照搬 openjiuwen topButton，底部圆角，无文字）*/}
        <FloatButton
          onClick={scrollToTop}
          title={t('plugins.githubWatch.backToTopTip')}
          ariaLabel={topLabel}
          variant="top"
        >
          <ChevronUp
            style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
            className="oj-btn-icon"
            strokeWidth={2}
            aria-hidden
          />
        </FloatButton>
      </div>
    </div>
  )

  // ── 通知弹窗（点击后立即标星，弹窗仅提示，不阻塞执行）──────────
  // 右下角通知卡片（不铺满遮罩，贴右下角浮出）
  const confirmDialog = confirmOpen ? (
    <div
      className="oj-notify-card fixed right-5 z-[1100] w-full max-w-[360px] rounded-2xl bg-white p-5 shadow-[0_8px_40px_rgba(0,0,0,0.16)]"
      style={{ bottom: 88 }}
      role="dialog"
      aria-modal="false"
      aria-hidden={!confirmOpen}
      aria-label={t('plugins.githubWatch.confirmTitle')}
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-amber-50">
          {flashing ? (
            <Loader2 className="h-5 w-5 animate-spin text-amber-500" aria-hidden />
          ) : (
            <Star className="h-5 w-5 fill-amber-400 text-amber-500" aria-hidden />
          )}
        </span>
        <h2 className="text-base font-semibold text-slate-900">
          {t('plugins.githubWatch.confirmTitle')}
        </h2>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-slate-600">
        {t('plugins.githubWatch.confirmBody')}
      </p>
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => setConfirmOpen(false)}
          className="rounded-lg bg-[linear-gradient(99.61deg,#1E54FA_0%,#842EFD_100%)] px-4 py-2 text-sm font-medium text-white shadow-[0_2px_8px_rgba(81,64,246,0.18)] transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
        >
          {t('plugins.githubWatch.confirmOk')}
        </button>
      </div>
    </div>
  ) : null

  return (
    <>
      {typeof document !== 'undefined' ? createPortal(floatingWidget, document.body) : null}
      {typeof document !== 'undefined' && confirmDialog ? createPortal(confirmDialog, document.body) : null}
    </>
  )
}

// ── 子组件：浮窗按钮（照搬 openjiuwen floatButton 基础样式）──────────
// variant:
//   star -> 首位按钮（border-radius: 12px 12px 0 0，浅灰底，和代码/顶部统一）
//   top  -> 末位按钮（border-radius: 0 0 12px 12px，浅灰底）
// 非末尾按钮有底部分隔线（::after 用伪元素无法内联，改用绝对定位 div 实现）
function FloatButton({
  children,
  onClick,
  title,
  ariaLabel,
  variant,
}: {
  children: React.ReactNode
  onClick: () => void
  title: string
  ariaLabel: string
  variant: 'star' | 'top'
}) {
  const isStar = variant === 'star'
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      className="oj-float-btn"
      style={{
        cursor: 'pointer',
        border: 'none',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 2,
        width: 52,
        height: 52,
        transition: '0.2s',
        display: 'flex',
        position: 'relative',
        borderRadius: isStar ? '12px 12px 0 0' : '0 0 12px 12px',
      }}
    >
      {children}
      {/* 分隔线（非末尾按钮底部，照搬 ::after 1px×28px rgba(0,0,0,0.06)）*/}
      {isStar && (
        <span
          style={{
            background: 'rgba(0, 0, 0, 0.06)',
            width: 28,
            height: 1,
            position: 'absolute',
            bottom: 0,
            left: '50%',
            transform: 'translateX(-50%)',
            pointerEvents: 'none',
          }}
        />
      )}
    </button>
  )
}

// 文字标签（照搬 openjiuwen buttonLabel，颜色由 CSS class 控制以便 hover 生效）
function FloatLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={`oj-btn-label${className ? ` ${className}` : ''}`}
      style={{
        fontSize: 10,
        fontWeight: 600,
        lineHeight: 1,
        transition: 'color 0.2s',
      }}
    >
      {children}
    </span>
  )
}
