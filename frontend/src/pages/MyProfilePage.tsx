import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { Link } from 'react-router-dom'
import {
  ExternalLink,
  Home,
  LogOut,
  Menu as MenuIcon,
  Puzzle,
  Plus,
  Search,
  X,
} from 'lucide-react'
import { Typography } from '@mui/material'
import { AppHeader } from '@/components/Common/AppHeader'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { Pagination } from '@/components/Common/common-table'
import { useQuery, useQueryClient } from 'react-query'
import { deletePluginAllVersions, getPlugins, type MarketplacePluginItem } from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'
import emptyDataIllustration from '@/assets/empty-data.svg'

const PROFILE_PAGE_SIZE_OPTIONS = [10, 20, 50] as const

export default function MyProfilePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const { user, isAuthenticated, logout } = useGitCodeAuth()
  const queryClient = useQueryClient()

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [search, setSearch] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<MarketplacePluginItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/profile${location.search || '?tab=skill'}`)
    navigate('/login', { replace: true })
  }, [isAuthenticated, location.search, navigate])

  const publisherId = user?.id

  const { data, isLoading, error } = useQuery(
    ['my-published-skills', publisherId, page, pageSize],
    () =>
      getPlugins({
        page,
        page_size: pageSize,
        publisher_id: publisherId,
        order_by: 'update_time',
        desc: true,
        plugin_type: 'skill',
      }),
    {
      enabled: Boolean(publisherId),
      keepPreviousData: true,
    },
  )

  const items = data?.data.items ?? []
  const total = data?.data.total ?? 0

  useEffect(() => {
    if (total <= 0) return
    const totalPages = Math.max(1, Math.ceil(total / pageSize))
    if (page > totalPages) setPage(totalPages)
  }, [total, pageSize, page])

  /** 客户端过滤当前页结果，保证搜索体验与服务端分页兼容 */
  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return items
    return items.filter(it => {
      const a = (it.display_name || '').toLowerCase()
      const b = (it.name || '').toLowerCase()
      return a.includes(q) || b.includes(q)
    })
  }, [items, search])

  const errMsg = useMemo(() => {
    if (!error) return ''
    return error instanceof Error ? error.message : String(error)
  }, [error])

  const handlePagerChange = (nextPage: number, nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(nextPage)
  }

  const openDetail = (row: MarketplacePluginItem) => {
    const v = row.latest_version?.trim()
    const versions = Array.isArray(row.all_versions) ? row.all_versions : []
    const fallback = versions.length ? versions[versions.length - 1] : ''
    const hint = v || fallback
    if (!hint) {
      window.alert(t('profile.missingVersion'))
      return
    }
    navigate(`/profile/plugins/${encodeURIComponent(row.asset_id)}`, {
      state: { latestVersion: hint },
    })
  }

  const handleLogout = () => {
    logout()
    navigate('/', { replace: true })
  }

  const { openPublish } = usePublishDrawer()
  const handleGoPublish = () => openPublish()

  const handleConfirmDelete = async () => {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    try {
      await deletePluginAllVersions(deleteTarget.asset_id)
      setDeleteTarget(null)
      await queryClient.invalidateQueries({ queryKey: ['my-published-skills'] })
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('profile.deleteFailed')
      window.alert(msg)
    } finally {
      setDeleting(false)
    }
  }

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-white">
        <Typography variant="body2" color="text.secondary">
          {t('profile.redirecting')}
        </Typography>
      </div>
    )
  }

  const avatarUrl = user.avatar_url?.trim() || ''
  const primaryName = user.name || user.login
  const initial = (primaryName || 'U').charAt(0).toUpperCase()

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-white">
      <AppHeader showPublish={false} />

      <div className="relative z-10 flex min-h-0 flex-1 gap-4 px-4 py-4 md:gap-6 md:px-[8.33%]">
        {sidebarOpen ? (
          <button
            type="button"
            aria-label={t('profile.card.closeSidebar')}
            onClick={() => setSidebarOpen(false)}
            className="fixed inset-0 z-30 bg-slate-900/40 md:hidden"
          />
        ) : null}

        <aside
          className={`${
            sidebarOpen
              ? 'fixed inset-y-0 left-0 z-40 flex w-[248px] translate-x-0'
              : 'fixed inset-y-0 left-0 z-40 hidden -translate-x-full'
          } flex-col rounded-r-2xl bg-[#FAFAFA] px-6 pb-6 pt-6 shadow-lg transition-transform md:static md:z-auto md:flex md:w-[248px] md:translate-x-0 md:rounded-2xl md:shadow-none`}
        >
          <div className="flex items-center justify-end md:hidden">
            <button
              type="button"
              aria-label={t('profile.card.closeSidebar')}
              onClick={() => setSidebarOpen(false)}
              className="-mr-1 rounded-md p-1 text-[#6B7280] hover:bg-slate-100 hover:text-[#111827]"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
          <div className="flex items-start gap-3">
            <SidebarAvatar avatarUrl={avatarUrl} initial={initial} />
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="truncate text-sm font-semibold text-[#111827]" title={primaryName}>
                {primaryName}
              </div>
              <div className="truncate text-xs text-[#9CA3AF]">@{user.login}</div>
            </div>
          </div>
          <div
            className="mt-3 line-clamp-2 text-xs leading-5 text-[#6B7280]"
            title={t('profile.sidebar.bioPlaceholder')}
          >
            {t('profile.sidebar.bioPlaceholder')}
          </div>

          <div className="mt-5 h-px bg-[#EEEEEE]" />

          <nav className="mt-4 flex flex-col gap-1" aria-label={t('profile.title')}>
            <Link
              to="/"
              className="flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]"
            >
              <Home className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.backToHome')}</span>
            </Link>
            <button
              type="button"
              aria-current="page"
              className="flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]"
            >
              <Puzzle className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.mySkills')}</span>
            </button>
          </nav>

          <div className="flex-1" />

          <button
            type="button"
            onClick={handleLogout}
            className="flex items-center gap-2 rounded-md px-1 py-2 text-sm text-[#6B7280] transition-colors hover:text-[#111827]"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            <span>{t('profile.sidebar.logout')}</span>
          </button>
        </aside>

        <main className="relative flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden bg-white">
          <div className="flex min-h-0 flex-1 flex-col overflow-auto px-4 py-4 md:pl-8 md:pr-0 md:py-8">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2">
                <button
                  type="button"
                  aria-label={t('profile.card.openSidebar')}
                  onClick={() => setSidebarOpen(true)}
                  className="mt-0.5 rounded-md p-1 text-[#6B7280] hover:bg-slate-100 hover:text-[#111827] md:hidden"
                >
                  <MenuIcon className="h-5 w-5" aria-hidden />
                </button>
                <div className="min-w-0">
                  <h2 className="text-[16px] font-semibold leading-6 text-[#191919]">
                    {t('profile.skillsTitle')}
                  </h2>
                  <p className="mt-1 text-xs text-[#6B7280]">{t('profile.skillsSubtitle')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleGoPublish}
                className="inline-flex h-8 w-24 shrink-0 items-center justify-center gap-1 rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-90"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                <span>{t('profile.publish')}</span>
              </button>
            </div>

            {errMsg ? (
              <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                {errMsg}
              </div>
            ) : null}

            <div className="relative mt-4">
              <Search
                className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9CA3AF]"
                aria-hidden
              />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t('profile.searchPlaceholder')}
                className="h-10 w-full rounded-lg border border-[#E5E7EB] bg-white pl-10 pr-3 text-sm text-[#111827] placeholder:text-[#9CA3AF] focus:border-[#4F46E5] focus:outline-none focus:ring-2 focus:ring-[#E0E7FF]"
              />
            </div>

            <div className="mt-6 flex min-h-0 flex-1 flex-col">
              {isLoading && !data ? (
                <Typography variant="body2" className="text-slate-500">
                  {t('plugins.loading')}
                </Typography>
              ) : filteredItems.length === 0 ? (
                <div className="flex flex-1 flex-col items-center justify-center py-12">
                  <img
                    src={emptyDataIllustration}
                    alt=""
                    aria-hidden
                    className="h-32 w-32 select-none"
                    draggable={false}
                  />
                  <div className="mt-4 text-sm text-[#6B7280]">
                    {t('profile.emptySkillsTitle')}
                  </div>
                  <button
                    type="button"
                    onClick={handleGoPublish}
                    className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[#0950DE] hover:underline"
                  >
                    <ExternalLink className="h-4 w-4" aria-hidden />
                    <span>{t('profile.goPublish')}</span>
                  </button>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  {filteredItems.map((row: MarketplacePluginItem) => (
                    <SkillCard
                      key={row.asset_id}
                      item={row}
                      onOpen={() => openDetail(row)}
                      onDelete={() => setDeleteTarget(row)}
                    />
                  ))}
                </div>
              )}
            </div>

            {total > 0 && data ? (
              <div className="mt-4 shrink-0 border-t border-[#e5e7eb] pt-4">
                <Pagination
                  pager={{
                    total,
                    currentPage: page,
                    pageSize,
                    pageSizeOptions: [...PROFILE_PAGE_SIZE_OPTIONS],
                  }}
                  loading={false}
                  onPagerChange={handlePagerChange}
                />
              </div>
            ) : null}
          </div>
        </main>
      </div>

      {deleteTarget ? (
        <DeleteSkillDialog
          name={deleteTarget.display_name || deleteTarget.name || ''}
          loading={deleting}
          onCancel={() => {
            if (!deleting) setDeleteTarget(null)
          }}
          onConfirm={handleConfirmDelete}
        />
      ) : null}
    </div>
  )
}

type SidebarAvatarProps = {
  avatarUrl: string
  initial: string
}

/** 头像：`avatar_url` 不存在、为空或图片加载失败时回落为姓名首字母色块。 */
function SidebarAvatar({ avatarUrl, initial }: SidebarAvatarProps) {
  const [loadFailed, setLoadFailed] = useState(false)
  const showImg = Boolean(avatarUrl) && !loadFailed
  if (showImg) {
    return (
      <img
        src={avatarUrl}
        alt=""
        onError={() => setLoadFailed(true)}
        className="h-10 w-10 shrink-0 rounded-full object-cover"
      />
    )
  }
  return (
    <div
      aria-hidden
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#E0E7FF] text-sm font-semibold text-[#4338CA]"
    >
      {initial}
    </div>
  )
}

/** 卡片图标颜色候选，按名字哈希挑选，保证同一条数据颜色稳定。 */
const SKILL_ICON_PALETTE = [
  { bg: '#EDE9FE', fg: '#6D28D9' },
  { bg: '#FCE7F3', fg: '#BE185D' },
  { bg: '#DBEAFE', fg: '#1D4ED8' },
  { bg: '#DCFCE7', fg: '#15803D' },
  { bg: '#FEF3C7', fg: '#B45309' },
  { bg: '#FEE2E2', fg: '#B91C1C' },
  { bg: '#CFFAFE', fg: '#0E7490' },
  { bg: '#FFE4E6', fg: '#BE123C' },
]

function pickIconColor(seed: string) {
  let hash = 0
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  }
  return SKILL_ICON_PALETTE[hash % SKILL_ICON_PALETTE.length]
}

type SkillCardProps = {
  item: MarketplacePluginItem
  onOpen: () => void
  onDelete: () => void
}

/** 卡片视图：一个技能一张卡，右侧常驻显示删除操作。 */
function SkillCard({ item, onOpen, onDelete }: SkillCardProps) {
  const { t } = useTranslation()
  const title = item.display_name || item.name || '—'
  const letter = (title || 'S').trim().charAt(0).toUpperCase()
  const color = pickIconColor(item.asset_id || title)
  const iconUrl = resolvePluginIconUrl(item.icon_uri || '')
  const [iconFailed, setIconFailed] = useState(false)
  const showUserIcon = Boolean(iconUrl) && !iconFailed
  const version = item.latest_version?.trim()
  const isPublished = Boolean(version)
  const statusText = isPublished
    ? t('profile.card.statusPublished')
    : t('profile.card.statusReviewing')
  const statusDot = isPublished ? 'bg-[#10B981]' : 'bg-[#F59E0B]'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen()
        }
      }}
      className="group relative flex cursor-pointer items-center gap-3 rounded-xl border border-[#E5E7EB] bg-white px-4 py-3 transition-all hover:border-[#CBD5E1] hover:shadow-[0_4px_12px_rgba(16,24,40,0.06)]"
    >
      {showUserIcon ? (
        <img
          src={iconUrl}
          alt=""
          aria-hidden
          draggable={false}
          onError={() => setIconFailed(true)}
          className="h-10 w-10 shrink-0 rounded-lg border border-[#F3F4F6] bg-white object-cover"
        />
      ) : (
        <div
          aria-hidden
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-semibold"
          style={{ backgroundColor: color.bg, color: color.fg }}
        >
          {letter}
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-[#111827]" title={title}>
          {title}
        </div>
        <div className="mt-1 flex items-center gap-2 text-xs text-[#6B7280]">
          <span className="tabular-nums">v {version || '0.0.1'}</span>
          <span className="text-[#D1D5DB]">·</span>
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusDot}`} aria-hidden />
          <span>{statusText}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center">
        <button
          type="button"
          onClick={e => {
            e.stopPropagation()
            onDelete()
          }}
          className="text-xs font-medium text-[#0950DE] transition-colors hover:text-[#0741B8]"
        >
          {t('profile.card.delete')}
        </button>
      </div>
    </div>
  )
}

type DeleteSkillDialogProps = {
  name: string
  loading: boolean
  onCancel: () => void
  onConfirm: () => void
}

/** 删除确认弹窗：确认按钮使用与发布按钮一致的渐变色。 */
function DeleteSkillDialog({ name, loading, onCancel, onConfirm }: DeleteSkillDialogProps) {
  const { t } = useTranslation()
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        className="absolute inset-0 bg-slate-900/40"
        onClick={() => !loading && onCancel()}
      />
      <div className="relative z-10 w-full max-w-[420px] rounded-2xl bg-white px-6 py-6 shadow-xl">
        <div className="text-base font-semibold text-[#111827]">
          {t('profile.card.deleteDialogTitle')}
        </div>
        <div className="mt-3 text-sm leading-6 text-[#374151]">
          {t('profile.card.deleteDialogBody', { name })}
        </div>
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className="inline-flex h-9 min-w-[96px] items-center justify-center rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {t('profile.card.confirm')}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="inline-flex h-9 min-w-[96px] items-center justify-center rounded-full border border-[#D1D5DB] bg-white px-4 text-sm font-medium text-[#374151] transition-colors hover:border-[#9CA3AF] hover:text-[#111827] disabled:opacity-60"
          >
            {t('profile.card.cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}
