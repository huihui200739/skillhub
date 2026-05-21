// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Link } from 'react-router-dom'
import {
  ClipboardList,
  ExternalLink,
  GitBranch,
  Heart,
  History,
  LogOut,
  Menu as MenuIcon,
  Puzzle,
  Plus,
  Search,
  Star,
  X,
} from 'lucide-react'
import { Typography } from '@mui/material'
import { AppHeader } from '@/components/Common/AppHeader'
import { GitSourcesPanel } from '@/components/Profile/GitSourcesPanel'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { Pagination } from '@/components/Common/common-table'
import { useQuery, useQueryClient } from 'react-query'
import { deletePluginAllVersions, getMyLikes, getMyStars, getPlugins, type MarketplacePluginItem, getSkillModerationAuditHistory, type SkillModerationAuditItem } from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'
import { formatSkillVersionLabel } from '@/utils/formatSkillVersionLabel'
import { SKILL_LIKE_QUERY_VALUE } from '@/utils/pluginType'
import emptyDataIllustration from '@/assets/empty-data.svg'

const PROFILE_PAGE_SIZE_OPTIONS = [10, 20, 50] as const

function compareVersionStrings(left: string, right: string): number {
  const leftParts = left.split(/[.+_-]/).filter(Boolean)
  const rightParts = right.split(/[.+_-]/).filter(Boolean)
  const length = Math.max(leftParts.length, rightParts.length)
  for (let i = 0; i < length; i += 1) {
    const leftPart = leftParts[i] ?? '0'
    const rightPart = rightParts[i] ?? '0'
    const leftNumber = /^\d+$/.test(leftPart) ? Number(leftPart) : null
    const rightNumber = /^\d+$/.test(rightPart) ? Number(rightPart) : null
    if (leftNumber !== null && rightNumber !== null && leftNumber !== rightNumber) return leftNumber - rightNumber
    const compared = leftPart.localeCompare(rightPart, undefined, { numeric: true, sensitivity: 'base' })
    if (compared !== 0) return compared
  }
  return left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' })
}

function resolveSkillReviewVersion(item: MarketplacePluginItem): string {
  const versions = Array.isArray(item.all_versions) ? item.all_versions.map(v => v.trim()).filter(Boolean) : []
  const orderedVersions = [...versions].sort(compareVersionStrings)
  const publishResultMap = item.skill_version_publish_result || {}
  const moderationMap = item.skill_version_moderation || {}
  const pendingVersion = [...orderedVersions].reverse().find(version => {
    const publishResult = String(publishResultMap[version] || '').trim().toLowerCase()
    const moderationStatus = String(moderationMap[version] || '').trim().toUpperCase()
    return publishResult === 'reviewing' || publishResult === 'pending_moderation' || moderationStatus === 'PENDING'
  })
  if (pendingVersion) return pendingVersion
  const latestVersion = item.latest_version?.trim()
  if (latestVersion) return latestVersion
  return orderedVersions[orderedVersions.length - 1] || ''
}

export default function MyProfilePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const { user, isAuthenticated, logout, isMarketModerationAdmin } = useGitCodeAuth()
  const [searchParams, setSearchParams] = useSearchParams()
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

  const tabParam = searchParams.get('tab')

  const activeTab = useMemo<'skill' | 'stars' | 'likes' | 'git' | 'pending' | 'audit'>(() => {
    if (tabParam === 'stars') return 'stars'
    if (tabParam === 'likes') return 'likes'
    if (tabParam === 'git') return 'git'
    if (!isMarketModerationAdmin) return 'skill'
    if (tabParam === 'audit') return 'audit'
    if (tabParam === 'pending') return 'pending'
    return 'skill'
  }, [isMarketModerationAdmin, tabParam])

  useEffect(() => {
    if (isMarketModerationAdmin) return
    if (tabParam === 'pending' || tabParam === 'audit') {
      setSearchParams({ tab: 'skill' }, { replace: true })
    }
  }, [isMarketModerationAdmin, setSearchParams, tabParam])

  useEffect(() => {
    setPage(1)
  }, [activeTab])

  const publisherId = user?.id
  const isSkillTab = activeTab === 'skill'
  const isStarsTab = activeTab === 'stars'
  const isLikesTab = activeTab === 'likes'
  const isGitTab = activeTab === 'git'
  const isPendingTab = activeTab === 'pending'
  const isAuditTab = activeTab === 'audit'

  const mySkillsQuery = useQuery(
    ['my-published-skills', publisherId, page, pageSize],
    () =>
      getPlugins({
        page,
        page_size: pageSize,
        publisher_id: publisherId,
        order_by: 'update_time',
        desc: true,
        plugin_type: SKILL_LIKE_QUERY_VALUE,
      }),
    {
      enabled: Boolean(publisherId) && isSkillTab,
      keepPreviousData: true,
      // 每次挂载（进入个人中心）都重新拉取：用户常从详情页回来，缓存数据可能已过期；
      // 同时 staleTime 置 0，保证 react-query 不把刚进入视为「仍新鲜」而跳过请求。
      refetchOnMount: 'always',
      staleTime: 0,
    },
  )

  const pendingSkillsQuery = useQuery(
    ['admin-pending-skills', page, pageSize],
    () =>
      getPlugins({
        page,
        page_size: pageSize,
        plugin_type: SKILL_LIKE_QUERY_VALUE,
        moderation_status: 'PENDING',
        order_by: 'update_time',
        desc: true,
      }),
    {
      enabled: isMarketModerationAdmin && isPendingTab,
      keepPreviousData: true,
    },
  )

  const myStarsQuery = useQuery(
    ['my-starred-skills', page, pageSize],
    () => getMyStars({ page, page_size: pageSize }),
    {
      enabled: isStarsTab,
      keepPreviousData: true,
      refetchOnMount: 'always',
      staleTime: 0,
    },
  )

  const myLikesQuery = useQuery(
    ['my-liked-skills', page, pageSize],
    () => getMyLikes({ page, page_size: pageSize }),
    {
      enabled: isLikesTab,
      keepPreviousData: true,
      refetchOnMount: 'always',
      staleTime: 0,
    },
  )

  const auditHistoryQuery = useQuery(
    ['skill-moderation-audit-history', page, pageSize],
    () => getSkillModerationAuditHistory({ page, page_size: pageSize }),
    {
      enabled: isMarketModerationAdmin && isAuditTab,
      keepPreviousData: true,
      refetchOnMount: 'always',
      staleTime: 0,
    },
  )

  const data = isGitTab
    ? undefined
    : isSkillTab
      ? mySkillsQuery.data
      : isStarsTab
        ? myStarsQuery.data
        : isLikesTab
          ? myLikesQuery.data
          : isPendingTab
            ? pendingSkillsQuery.data
            : auditHistoryQuery.data
  const isLoading = isGitTab
    ? false
    : isSkillTab
      ? mySkillsQuery.isLoading
      : isStarsTab
        ? myStarsQuery.isLoading
        : isLikesTab
          ? myLikesQuery.isLoading
          : isPendingTab
            ? pendingSkillsQuery.isLoading
            : auditHistoryQuery.isLoading
  const error = isGitTab
    ? undefined
    : isSkillTab
      ? mySkillsQuery.error
      : isStarsTab
        ? myStarsQuery.error
        : isLikesTab
          ? myLikesQuery.error
          : isPendingTab
            ? pendingSkillsQuery.error
            : auditHistoryQuery.error

  const items = data?.data.items ?? []
  const total = data?.data.total ?? 0
  const auditItems = (items as SkillModerationAuditItem[]) ?? []

  useEffect(() => {
    if (total <= 0) return
    const totalPages = Math.max(1, Math.ceil(total / pageSize))
    if (page > totalPages) setPage(totalPages)
  }, [total, pageSize, page])

  /** 客户端过滤当前页结果，保证搜索体验与服务端分页兼容（审核历史走服务端分页，不参与本地过滤） */
  const filteredItems = useMemo(() => {
    if (isAuditTab) return []
    if (isGitTab) return []
    const list = items as MarketplacePluginItem[]
    const q = search.trim().toLowerCase()
    if (!q) return list
    return list.filter(it => {
      const a = (it.display_name || '').toLowerCase()
      const b = (it.name || '').toLowerCase()
      return a.includes(q) || b.includes(q)
    })
  }, [items, search, isAuditTab])

  const errMsg = useMemo(() => {
    if (!error) return ''
    const base = error instanceof Error ? error.message : String(error)
    if (isAuditTab) {
      return base ? `${t('profile.auditHistoryError')} ${base}` : t('profile.auditHistoryError')
    }
    return base
  }, [error, isAuditTab, t])

  const handlePagerChange = (nextPage: number, nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(nextPage)
  }

  const openReviewDetail = (row: MarketplacePluginItem) => {
    const version = resolveSkillReviewVersion(row)
    if (!version) {
      window.alert(t('profile.missingVersion'))
      return
    }
    navigate(`/profile/plugins/${encodeURIComponent(row.asset_id)}/versions/${encodeURIComponent(version)}/review`, {
      state: { fromProfile: true },
    })
  }

  const openAuditReviewDetail = (row: SkillModerationAuditItem) => {
    const version = row.version?.trim()
    if (!version) {
      window.alert(t('profile.missingVersion'))
      return
    }
    navigate(`/profile/plugins/${encodeURIComponent(row.asset_id)}/versions/${encodeURIComponent(version)}/review`, {
      state: { fromProfile: true },
    })
  }

  const openDetail = (row: MarketplacePluginItem) => {
    if (isPendingTab) {
      const version = resolveSkillReviewVersion(row)
      const query = version ? `?version=${encodeURIComponent(version)}&moderation_status=PENDING` : '?moderation_status=PENDING'
      navigate(`/skills/${encodeURIComponent(row.asset_id)}${query}`, {
        state: { fromProfile: true, moderationContext: 'pending' },
      })
      return
    }
    if (isStarsTab || isLikesTab) {
      const version = row.latest_version?.trim()
      const query = version ? `?version=${encodeURIComponent(version)}` : ''
      navigate(`/skills/${encodeURIComponent(row.asset_id)}${query}`, { state: { fromProfile: true } })
      return
    }
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
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-white">
      <AppHeader showPublish={false} />

      <div className="px-4 pt-4 md:px-[8.33%]">
        <Breadcrumbs
          items={[
            { label: t('common.breadcrumb.home'), to: '/' },
            { label: t('common.breadcrumb.profile') },
          ]}
        />
      </div>

      <div className="relative z-10 flex flex-1 flex-col gap-4 px-4 pb-10 pt-3 md:flex-row md:gap-6 md:px-[8.33%]">
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
              to="/profile?tab=skill"
              onClick={() => setSidebarOpen(false)}
              aria-current={isSkillTab ? 'page' : undefined}
              className={
                isSkillTab
                  ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
              }
            >
              <Puzzle className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.mySkills')}</span>
            </Link>
            <Link
              to="/profile?tab=stars"
              onClick={() => setSidebarOpen(false)}
              aria-current={isStarsTab ? 'page' : undefined}
              className={
                isStarsTab
                  ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
              }
            >
              <Star className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.myStars')}</span>
            </Link>
            <Link
              to="/profile?tab=likes"
              onClick={() => setSidebarOpen(false)}
              aria-current={isLikesTab ? 'page' : undefined}
              className={
                isLikesTab
                  ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
              }
            >
              <Heart className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.myLikes')}</span>
            </Link>
            <Link
              to="/profile?tab=git"
              onClick={() => setSidebarOpen(false)}
              aria-current={isGitTab ? 'page' : undefined}
              className={
                isGitTab
                  ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
              }
            >
              <GitBranch className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
              <span>{t('profile.sidebar.gitSources')}</span>
            </Link>
            {isMarketModerationAdmin ? (
              <>
                <Link
                  to="/profile?tab=pending"
                  onClick={() => setSidebarOpen(false)}
                  aria-current={isPendingTab ? 'page' : undefined}
                  className={
                    isPendingTab
                      ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                      : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  }
                >
                  <ClipboardList className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
                  <span>{t('profile.sidebar.pendingReview')}</span>
                </Link>
                <Link
                  to="/profile?tab=audit"
                  onClick={() => setSidebarOpen(false)}
                  aria-current={isAuditTab ? 'page' : undefined}
                  className={
                    isAuditTab
                      ? 'flex h-10 w-[200px] items-center gap-2 rounded-lg bg-white px-3 text-[13px] font-normal leading-5 text-[#191919] shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                      : 'flex h-10 w-[200px] items-center gap-2 rounded-lg px-3 text-[13px] font-normal leading-5 text-[#191919] transition-colors hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'
                  }
                >
                  <History className="h-[14px] w-[14px] text-[#191919]" aria-hidden />
                  <span>{t('profile.sidebar.auditHistory')}</span>
                </Link>
              </>
            ) : null}
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

        <main className="relative flex min-w-0 flex-1 flex-col bg-white">
          <div className="flex flex-col px-4 py-4 md:min-h-0 md:flex-1 md:pl-8 md:pr-0 md:py-8">
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
                    {isSkillTab
                      ? t('profile.skillsTitle')
                      : isStarsTab
                        ? t('profile.starsTitle')
                        : isLikesTab
                          ? t('profile.likesTitle')
                          : isGitTab
                            ? t('profile.gitSourcesTitle')
                            : isPendingTab
                              ? t('profile.pendingReviewTitle')
                              : t('profile.auditHistoryTitle')}
                  </h2>
                  <p className="mt-1 text-xs text-[#6B7280]">
                    {isSkillTab
                      ? t('profile.skillsSubtitle')
                      : isStarsTab
                        ? t('profile.starsSubtitle')
                        : isLikesTab
                          ? t('profile.likesSubtitle')
                          : isGitTab
                            ? t('profile.gitSourcesSubtitle')
                            : isPendingTab
                              ? t('profile.pendingReviewSubtitle')
                              : t('profile.auditHistorySubtitle')}
                  </p>
                </div>
              </div>
              {isSkillTab ? (
                <button
                  type="button"
                  onClick={handleGoPublish}
                  className="inline-flex h-8 w-24 shrink-0 items-center justify-center gap-1 rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-90"
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden />
                  <span>{t('profile.publish')}</span>
                </button>
              ) : null}
            </div>

            {errMsg ? (
              <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                {errMsg}
              </div>
            ) : null}

            {(isSkillTab || isPendingTab || isStarsTab || isLikesTab) ? (
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
            ) : null}

            <div className="mt-6 flex flex-col">
              {isGitTab ? (
                <GitSourcesPanel userId={publisherId} />
              ) : isLoading && !data ? (
                <Typography variant="body2" className="text-slate-500">
                  {t('plugins.loading')}
                </Typography>
              ) : isAuditTab ? (
                auditItems.length === 0 ? (
                  <div className="flex flex-1 flex-col items-center justify-center py-12">
                    <img
                      src={emptyDataIllustration}
                      alt=""
                      aria-hidden
                      className="h-32 w-32 select-none"
                      draggable={false}
                    />
                    <div className="mt-4 text-sm text-[#6B7280]">{t('profile.emptyAuditHistory')}</div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {auditItems.map(row => (
                      <AuditHistoryCard
                        key={row.event_id}
                        item={row}
                        onOpenDetail={() => openAuditReviewDetail(row)}
                      />
                    ))}
                  </div>
                )
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
                    {isSkillTab
                      ? t('profile.emptySkillsTitle')
                      : isStarsTab
                        ? t('profile.emptyStarsTitle')
                        : isLikesTab
                          ? t('profile.emptyLikesTitle')
                          : t('profile.emptyPendingSkillsTitle')}
                  </div>
                  {isSkillTab ? (
                    <button
                      type="button"
                      onClick={handleGoPublish}
                      className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[#0950DE] hover:underline"
                    >
                      <ExternalLink className="h-4 w-4" aria-hidden />
                      <span>{t('profile.goPublish')}</span>
                    </button>
                  ) : null}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  {filteredItems.map((row: MarketplacePluginItem) => (
                    <SkillCard
                      key={row.asset_id}
                      item={row}
                      showDelete={isSkillTab}
                      statusMode={isSkillTab ? 'publish' : 'moderation'}
                      showModerationStatus={isSkillTab || isPendingTab}
                      onOpen={() => openDetail(row)}
                      onOpenReview={isPendingTab ? () => openReviewDetail(row) : undefined}
                      onDelete={() => setDeleteTarget(row)}
                    />
                  ))}
                </div>
              )}
            </div>

            {total > 0 && data && !isGitTab ? (
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

type AuditHistoryCardProps = {
  item: SkillModerationAuditItem
  onOpenDetail: () => void
}

/** 审核历史：展示 Skill 标识、版本、结果、原因、时间；点击跳转详情。 */
function AuditHistoryCard({ item, onOpenDetail }: AuditHistoryCardProps) {
  const { t, i18n } = useTranslation()
  const headline =
    item.skill_display_name?.trim() ||
    item.skill_name ||
    item.asset_id
  const slug = item.skill_display_name?.trim() ? item.skill_name : null
  const locale = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'
  const timeLabel = new Date(item.created_at_ms).toLocaleString(locale, {
    dateStyle: 'short',
    timeStyle: 'short',
  })
  const approved = item.moderation_action === 'APPROVE'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenDetail}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpenDetail()
        }
      }}
      className="group relative flex cursor-pointer flex-col gap-2 rounded-xl border border-[#E5E7EB] bg-white px-4 py-3 text-left transition-all hover:border-[#CBD5E1] hover:shadow-[0_4px_12px_rgba(16,24,40,0.06)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-[#111827]" title={headline}>
            {headline}
          </div>
          {slug ? (
            <div className="truncate text-xs text-[#9CA3AF]" title={slug}>
              {slug}
            </div>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#6B7280]">
            <span className="tabular-nums">{item.version ? formatSkillVersionLabel(item.version) : '—'}</span>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${
                approved ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'
              }`}
            >
              {approved ? t('profile.auditHistoryResultApprove') : t('profile.auditHistoryResultReject')}
            </span>
            <span>{timeLabel}</span>
          </div>
        </div>
        <span className="shrink-0 pt-0.5 text-xs font-medium text-[#0950DE] group-hover:underline">
          {t('profile.auditHistoryViewDetail')}
        </span>
      </div>
      {!approved && item.reject_reason ? (
        <p className="line-clamp-3 text-xs leading-5 text-[#6B7280]">
          <span className="font-medium text-[#374151]">{t('profile.auditReasonLabel')} </span>
          {item.reject_reason}
        </p>
      ) : null}
    </div>
  )
}

type SkillCardProps = {
  item: MarketplacePluginItem
  /** 待审核队列中为 false，不展示删除 */
  showDelete?: boolean
  statusMode?: 'publish' | 'moderation'
  /** 收藏/点赞页不展示审核状态标签 */
  showModerationStatus?: boolean
  onOpen: () => void
  onOpenReview?: () => void
  onDelete: () => void
}

/** 卡片视图：一个技能一张卡，右侧常驻显示删除操作。 */
function skillModerationUi(
  item: MarketplacePluginItem,
  t: (k: string) => string,
  mode: 'publish' | 'moderation',
): { text: string; dot: string } {
  if (mode === 'publish') {
    const latestVersion = item.latest_version?.trim() || ''
    const latestVersionPublishResult = latestVersion
      ? String(item.skill_version_publish_result?.[latestVersion] || '').trim().toLowerCase()
      : ''
    const pr = latestVersionPublishResult || String(item.publish_result || '').trim().toLowerCase()
    const latestModerationStatus = latestVersion
      ? (item.skill_version_moderation?.[latestVersion] || '').toString().toUpperCase()
      : (item.moderation_status || '').toString().toUpperCase()
    if (pr === 'reviewing') {
      return { text: t('profile.publishResultReviewing'), dot: 'bg-sky-500' }
    }
    if (pr === 'pending_moderation') {
      return { text: t('profile.publishResultPendingModeration'), dot: 'bg-amber-500' }
    }
    if (pr === 'publish_failed') {
      return {
        text:
          latestModerationStatus === 'REJECTED'
            ? t('profile.publishResultFailedModeration')
            : t('profile.publishResultFailedSystem'),
        dot: 'bg-rose-500',
      }
    }
    return { text: t('profile.publishResultSuccess'), dot: 'bg-emerald-500' }
  }
  if (item.has_pending_skill_version === true) {
    return { text: t('profile.card.newVersionPendingReview'), dot: 'bg-amber-500' }
  }
  const raw = (item.moderation_status || 'APPROVED').toString().toUpperCase()
  if (raw === 'PENDING') {
    return { text: t('profile.card.moderationPending'), dot: 'bg-amber-500' }
  }
  if (raw === 'REJECTED') {
    return { text: t('profile.card.moderationRejected'), dot: 'bg-rose-500' }
  }
  return { text: t('profile.card.moderationApproved'), dot: 'bg-emerald-500' }
}

function SkillCard({
  item,
  showDelete = true,
  statusMode = 'publish',
  showModerationStatus = true,
  onOpen,
  onOpenReview,
  onDelete,
}: SkillCardProps) {
  const { t } = useTranslation()
  const title = item.display_name || item.name || '—'
  const letter = (title || 'S').trim().charAt(0).toUpperCase()
  const color = pickIconColor(item.asset_id || title)
  const iconUrl = resolvePluginIconUrl(item.icon_uri || '')
  const [iconFailed, setIconFailed] = useState(false)
  const showUserIcon = Boolean(iconUrl) && !iconFailed
  const version = item.latest_version?.trim()
  const { text: statusText, dot: statusDot } = skillModerationUi(item, t, statusMode)

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
          <span className="tabular-nums">
            {version
              ? formatSkillVersionLabel(version, {
                  gitVersionDisplayAsCommit: item.git_version_display_as_commit,
                  resolvedCommitSha: item.resolved_commit_sha,
                  declaredSkillVersion: item.declared_skill_version,
                  storageMode: item.storage_mode,
                })
              : '—'}
          </span>
          {showModerationStatus ? (
            <>
              <span className="text-[#D1D5DB]">·</span>
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusDot}`} aria-hidden />
              <span>{statusText}</span>
            </>
          ) : null}
        </div>
      </div>
      {showDelete || onOpenReview ? (
        <div className="flex shrink-0 items-center gap-3">
          {onOpenReview ? (
            <button
              type="button"
              onClick={e => {
                e.stopPropagation()
                onOpenReview()
              }}
              className="text-xs font-medium text-[#0950DE] transition-colors hover:text-[#0741B8]"
            >
              {t('profile.viewReviewDetail')}
            </button>
          ) : null}
          {showDelete ? (
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
          ) : null}
        </div>
      ) : null}
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
