import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  BarChart3,
  CalendarClock,
  CalendarPlus,
  Cpu,
  Info,
  Download,
  Eye,
  Heart,
  Star,
  MessageCircle,
  RefreshCw,
  ScrollText,
  Tag,
  Sparkles,
  Flame,
  LayoutGrid,
  Search,
  X,
  BookOpen,
  AlignLeft,
  Pin,
} from 'lucide-react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  CircularProgress,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Tooltip,
  Typography,
} from '@mui/material'
import { useQuery, useQueryClient } from 'react-query'
import { pluginCardTooltipProps, pluginDetailHeaderTooltipProps } from '@/components/Common/pluginCardTooltip'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { AppHeader } from '@/components/Common/AppHeader'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { Empty } from '@/components/Common/Empty'
import axios from 'axios'
import { pinyin } from 'pinyin-pro'
import {
  getPluginArtifactDownload,
  getPluginInteractionsBatch,
  getPluginVersionDetail,
  togglePluginInteract,
  type AssetInteractionState,
  type UserInteractionState,
} from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { usePluginMarketConfigs, type MarketPlugin } from '@/hooks/usePluginMarketConfigs'
import { useMediaQuery } from '@/hooks/useMediaQuery'

function isCanceledRequest(err: unknown): boolean {
  if (axios.isCancel(err)) return true
  if (!axios.isAxiosError(err)) return false
  return err.code === 'ERR_CANCELED' || err.name === 'CanceledError'
}

async function triggerPluginFileDownload(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url, { mode: 'cors', credentials: 'omit' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const objUrl = URL.createObjectURL(blob)
    try {
      const a = document.createElement('a')
      a.href = objUrl
      a.download = filename
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } finally {
      URL.revokeObjectURL(objUrl)
    }
    return
  } catch {
  }

  try {
    const iframe = document.createElement('iframe')
    iframe.setAttribute('aria-hidden', 'true')
    iframe.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0;border:0'
    iframe.src = url
    document.body.appendChild(iframe)
    window.setTimeout(() => {
      try {
        iframe.remove()
      } catch {
      }
    }, 120_000)
    return
  } catch {
  }

  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.rel = 'noopener noreferrer'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

const PLUGIN_INTRO_DISPLAY_MAX = 50

function truncatePluginIntro(
  text: string,
  maxChars: number,
): { display: string; full: string; truncated: boolean } {
  const full = text
  const chars = [...full]
  if (chars.length <= maxChars) {
    return { display: full, full, truncated: false }
  }
  return { display: `${chars.slice(0, maxChars).join('')}...`, full, truncated: true }
}

const AVATAR_PALETTE = [
  { bg: 'linear-gradient(135deg, #EBF7FF, #C2E4FF)', color: '#1E54F9', shadow: '0 2px 15px rgba(30,84,249,0.25)', gradFrom: '#EBF7FF', gradTo: '#C2E4FF' },
  { bg: 'linear-gradient(135deg, #FFF0F5, #FFD2E1)', color: '#E04379', shadow: '0 2px 15px rgba(224,67,121,0.25)', gradFrom: '#FFF0F5', gradTo: '#FFD2E1' },
  { bg: 'linear-gradient(135deg, #F5F0FF, #E6DAFF)', color: '#7C3AED', shadow: '0 2px 15px rgba(124,58,237,0.25)', gradFrom: '#F5F0FF', gradTo: '#E6DAFF' },
  { bg: 'linear-gradient(135deg, #F0FFD9, #D5FFB9)', color: '#2D8B4E', shadow: '0 2px 15px rgba(45,139,78,0.25)', gradFrom: '#F0FFD9', gradTo: '#D5FFB9' },
  { bg: 'linear-gradient(135deg, #FFF3E8, #FFE1C5)', color: '#D97706', shadow: '0 2px 15px rgba(217,119,6,0.25)', gradFrom: '#FFF3E8', gradTo: '#FFE1C5' },
  { bg: 'linear-gradient(135deg, #E5FFF6, #B1F5E0)', color: '#0891B2', shadow: '0 2px 15px rgba(8,145,178,0.25)', gradFrom: '#E5FFF6', gradTo: '#B1F5E0' },
]

/** 汉字（含扩展区）；用于选择「标准拼音声母 / 零声母音节首字母」路径。 */
const HAN_SCRIPT_RE = /\p{Script=Han}/u

const PINYIN_AVATAR_OPTS = { toneType: 'none' as const, type: 'string' as const }

/**
 * 标准汉语拼音：有则返回整段声母（如 zh、ch、sh、b）；零声母字返回音节首字母（如 啊→A、安→A）。
 */
function getStandardPinyinInitial(ch: string): string {
  const initial = pinyin(ch, { ...PINYIN_AVATAR_OPTS, pattern: 'initial' }).trim()
  if (initial) return initial.toUpperCase()
  const head = pinyin(ch, { ...PINYIN_AVATAR_OPTS, pattern: 'first' }).trim()
  if (head) return head.charAt(0).toUpperCase()
  return ''
}

function getPluginAvatarChar(displayName: string): string {
  const trimmed = displayName.trim()
  if (!trimmed) return ''
  const first = [...trimmed][0]
  if (!first) return ''
  if (/^[a-z]$/i.test(first)) return first.toUpperCase()
  if (HAN_SCRIPT_RE.test(first)) return getStandardPinyinInitial(first)
  return first
}

function paletteIndexForChar(ch: string): number {
  let h = 0
  for (let i = 0; i < ch.length; i += 1) h = (h * 31 + ch.charCodeAt(i)) | 0
  return Math.abs(h) % AVATAR_PALETTE.length
}

/** 接口 `icon_uri` 非空且像可请求的地址时才去加载（缺字段/空串不请求）。 */
function hasPluginIconSrc(icon: string | undefined): boolean {
  if (typeof icon !== 'string' || !icon.trim()) return false
  const t = icon.trim()
  if (t.startsWith('http://') || t.startsWith('https://')) return true
  if (t.startsWith('/') && !t.startsWith('//')) return true
  if (t.startsWith('data:image/')) return true
  if (t.startsWith('blob:')) return true
  return t.includes('.')
}

/** 自然尺寸过小（如 1×1 占位）视为无效，走字母回落。 */
function isPluginIconNaturalSizeOk(img: HTMLImageElement): boolean {
  const w = img.naturalWidth
  const h = img.naturalHeight
  return w >= 2 && h >= 2
}

function PluginAvatar({ iconUri, displayName }: { iconUri?: string; displayName: string }) {
  const ch = getPluginAvatarChar(displayName)
  const palette = ch === '' ? AVATAR_PALETTE[0] : AVATAR_PALETTE[paletteIndexForChar(ch)] ?? AVATAR_PALETTE[0]
  const [iconShown, setIconShown] = useState(false)
  const shouldTryIcon = hasPluginIconSrc(iconUri)

  useEffect(() => {
    setIconShown(false)
  }, [iconUri])

  const handleImgLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    setIconShown(isPluginIconNaturalSizeOk(e.currentTarget))
  }, [])

  const handleImgError = useCallback(() => {
    setIconShown(false)
  }, [])

  return (
    <div
      className={`relative w-12 h-12 min-w-[48px] rounded-xl flex items-center justify-center font-semibold select-none overflow-hidden leading-none px-0.5 ${ch.length > 1 ? 'text-lg' : 'text-2xl'}`}
      style={{ background: palette.bg, color: palette.color, boxShadow: palette.shadow }}
    >
      <span style={{ opacity: iconShown ? 0 : 1 }}>{ch || '?'}</span>
      {shouldTryIcon && (
        <img
          key={iconUri}
          src={iconUri}
          alt=""
          className="absolute inset-0 w-full h-full object-cover"
          style={{ display: iconShown ? 'block' : 'none' }}
          onLoad={handleImgLoad}
          onError={handleImgError}
        />
      )}
    </div>
  )
}

type CategoryKey = 'hot' | 'newest' | 'all' | 'software-development' | 'office-productivity' | 'content-creation' | 'multimodal-media' | 'data-science-research' | 'compliance-legal' | 'lifestyle-health' | 'finance-wealth'

const CATEGORY_KEYS: CategoryKey[] = ['hot', 'newest', 'all', 'software-development', 'office-productivity', 'content-creation', 'multimodal-media', 'data-science-research', 'compliance-legal', 'lifestyle-health', 'finance-wealth']

const CATEGORY_ICONS: Record<CategoryKey, React.ReactNode> = {
  hot: <Flame className="w-5 h-5" />,
  newest: <Sparkles className="w-5 h-5" />,
  all: <LayoutGrid className="w-5 h-5" />,
  'software-development': <Cpu className="w-5 h-5" />,
  'office-productivity': <AlignLeft className="w-5 h-5" />,
  'content-creation': <BookOpen className="w-5 h-5" />,
  'multimodal-media': <Search className="w-5 h-5" />,
  'data-science-research': <BarChart3 className="w-5 h-5" />,
  'compliance-legal': <Heart className="w-5 h-5" />,
  'lifestyle-health': <MessageCircle className="w-5 h-5" />,
  'finance-wealth': <ScrollText className="w-5 h-5" />,
}

function formatPluginDateTime(ts: number | null | undefined, locale: string): string {
  if (ts == null || ts === 0) return '-'
  const ms = ts > 1_000_000_000_000 ? ts : ts * 1000
  return new Date(ms).toLocaleString(locale.startsWith('zh') ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function SkeletonCard() {
  return (
    <div className="rounded-2xl border border-[#e6e6e6] bg-white/95 p-5 animate-pulse">
      <div className="flex items-start gap-3 mb-3">
        <div className="h-10 w-10 shrink-0 rounded-xl bg-gray-200" />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-4 w-3/4 rounded bg-gray-200" />
          <div className="h-3 w-1/2 rounded bg-gray-200" />
        </div>
      </div>
      <div className="mb-4 space-y-2">
        <div className="h-3 rounded bg-gray-200" />
        <div className="h-3 w-5/6 rounded bg-gray-200" />
      </div>
      <div className="flex items-center justify-between">
        <div className="h-3 w-1/4 rounded bg-gray-200" />
        <div className="h-3 w-1/6 rounded bg-gray-200" />
      </div>
    </div>
  )
}

function DetailPluginTags({ tags }: { tags: string[] }) {
  const list = tags ?? []
  const MAX = 3
  const tagBg = ['#FEF3C7', '#EDE9FE', '#FCE7F3'] as const
  const tagFg = ['#B45309', '#5B21B6', '#BE185D'] as const
  if (list.length === 0) return null
  const visible = list.slice(0, MAX)
  const hidden = list.slice(MAX)
  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      {visible.map((tag, i) => (
        <span
          key={`${tag}-${i}`}
          className="shrink-0 px-2 py-0.5 rounded-md text-[11px] font-medium border border-black/5"
          style={{ backgroundColor: tagBg[i % tagBg.length], color: tagFg[i % tagFg.length] }}
        >
          {tag}
        </span>
      ))}
      {hidden.length > 0 && (
        <Tooltip {...pluginCardTooltipProps} title={hidden.join(' · ')}>
          <span className="shrink-0 px-2 py-0.5 rounded-md text-[11px] font-medium bg-gray-200 text-gray-700 border border-gray-300/80 cursor-default">
            +{hidden.length}
          </span>
        </Tooltip>
      )}
    </div>
  )
}

const PAGE_SIZE_OPTIONS = [12, 24, 48]

const MODEL_ACCESS_NOTICE_DISMISSED_KEY = 'marketplace_model_access_notice_dismissed_version1'

export default function PluginMarketPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const { user, isAuthenticated } = useGitCodeAuth()
  const [searchInput, setSearchInput] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [noticeDismissed, setNoticeDismissed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    try {
      return window.localStorage.getItem(MODEL_ACCESS_NOTICE_DISMISSED_KEY) === '1'
    } catch {
      return false
    }
  })
  const dismissModelAccessNotice = useCallback(() => {
    setNoticeDismissed(true)
    try {
      window.localStorage.setItem(MODEL_ACCESS_NOTICE_DISMISSED_KEY, '1')
    } catch {
    }
  }, [])
  const [activeCategory, setActiveCategory] = useState<CategoryKey>(() => {
    const cat = searchParams.get('category')
    return (cat && (CATEGORY_KEYS as string[]).includes(cat)) ? cat as CategoryKey : 'all'
  })
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(12)
  const [selectedPlugin, setSelectedPlugin] = useState<MarketPlugin | null>(null)
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [downloadingAssetId, setDownloadingAssetId] = useState<string | null>(null)
  const downloadLockRef = useRef(false)
  const [detailDownloadVersion, setDetailDownloadVersion] = useState('')
  const [detailChangelog, setDetailChangelog] = useState<string | null>(null)
  const [detailChangelogLoading, setDetailChangelogLoading] = useState(false)
  const [detailChangelogError, setDetailChangelogError] = useState<string | null>(null)

  const detailDialogFullScreen = useMediaQuery('(max-width: 639px)')

  const handleSearch = useCallback(() => {
    setSearchKeyword(searchInput.trim())
    setCurrentPage(1)
  }, [searchInput])

  useEffect(() => {
    setCurrentPage(1)
  }, [activeCategory])

  const handleSetCategory = useCallback((key: CategoryKey) => {
    setActiveCategory(key)
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('category', key)
      return next
    }, { replace: true })
  }, [setSearchParams])

  const isHotCategory = activeCategory === 'hot'
  const isNewestCategory = activeCategory === 'newest'
  const activeCategoryId = activeCategory === 'hot' || activeCategory === 'newest' || activeCategory === 'all' ? undefined : activeCategory

  const { marketPlugins, total, page, pageSize: serverPageSize, loading, fetching, error, refreshMarketPlugins } =
    usePluginMarketConfigs({
      page: currentPage,
      pageSize,
      searchKeyword,
      catalogKind: 'skill',
      categoryId: activeCategoryId,
      orderBy: isHotCategory ? 'install_count' : isNewestCategory ? 'create_time' : undefined,
      desc: isHotCategory || isNewestCategory ? true : undefined,
    })

  const marketAssetIds = useMemo(
    () => marketPlugins.map(plugin => plugin.assetId).filter(Boolean),
    [marketPlugins],
  )
  const interactionViewerKey = isAuthenticated
    ? `user:${user?.id ? String(user.id) : 'token'}`
    : 'guest'

  const interactionStatesQuery = useQuery(
    ['market-interactions', interactionViewerKey, ...marketAssetIds],
    () => getPluginInteractionsBatch(marketAssetIds),
    { enabled: marketAssetIds.length > 0, keepPreviousData: true },
  )

  const interactionStateMap = useMemo(() => {
    const map: Record<string, AssetInteractionState> = {}
    // 先用列表接口的计数填默认值，保证 batch 未返回前页面展示稳定。
    for (const plugin of marketPlugins) {
      if (!plugin.assetId) continue
      map[plugin.assetId] = {
        asset_id: plugin.assetId,
        liked: false,
        starred: false,
        like_count: plugin.likeCount,
        star_count: plugin.starCount,
      }
    }
    for (const item of interactionStatesQuery.data ?? []) {
      if (item.asset_id) map[item.asset_id] = item
    }
    return map
  }, [interactionStatesQuery.data, marketPlugins])
  const [interactingKey, setInteractingKey] = useState<string | null>(null)

  /** 本页仅拉取 skill 目录；与列表 `catalogKind` 一致。 */
  const marketCatalogTab = 'skill' as const

  const defaultDownloadVersion = useCallback((plugin: MarketPlugin) => {
    const versions = plugin.allVersions
    const latest = plugin.latestVersion?.trim()
    if (latest && versions.includes(latest)) return latest
    if (versions.length) return versions[versions.length - 1]
    return latest || ''
  }, [])

  const effectiveDetailVersion = useMemo(() => {
    if (!selectedPlugin) return ''
    return (detailDownloadVersion || defaultDownloadVersion(selectedPlugin)).trim()
  }, [selectedPlugin, detailDownloadVersion, defaultDownloadVersion])

  const selectedAssetId = selectedPlugin?.assetId ?? ''

  useEffect(() => {
    if (!detailDialogOpen || !selectedAssetId || !effectiveDetailVersion) {
      setDetailChangelog(null)
      setDetailChangelogLoading(false)
      setDetailChangelogError(null)
      return
    }
    const ac = new AbortController()
    setDetailChangelogLoading(true)
    setDetailChangelogError(null)
    setDetailChangelog(null)
    void getPluginVersionDetail(selectedAssetId, effectiveDetailVersion, { signal: ac.signal })
      .then(data => {
        const raw = data.changelog?.trim()
        setDetailChangelog(raw && raw.length > 0 ? raw : null)
        setDetailChangelogLoading(false)
        if (data.view_count != null && Number.isFinite(Number(data.view_count))) {
          const vc = Number(data.view_count)
          const aid = data.asset_id
          setSelectedPlugin(prev => (prev && prev.assetId === aid ? { ...prev, viewCount: vc } : prev))
        }
        void queryClient.invalidateQueries({ queryKey: ['plugins'] })
      })
      .catch((err: unknown) => {
        if (isCanceledRequest(err)) {
          return
        }
        setDetailChangelogError(err instanceof Error ? err.message : t('plugins.detail.changelogLoadFailed'))
        setDetailChangelogLoading(false)
      })
    return () => ac.abort()
  }, [detailDialogOpen, selectedAssetId, effectiveDetailVersion, t, queryClient])

  const handleViewPlugin = (plugin: MarketPlugin) => {
    if (marketCatalogTab === 'skill') {
      navigate(`/skills/${encodeURIComponent(plugin.assetId)}`)
      return
    }
    setSelectedPlugin(plugin)
    setDetailDownloadVersion(defaultDownloadVersion(plugin))
    setDetailDialogOpen(true)
  }

  const handleRefresh = async () => {
    await refreshMarketPlugins()
  }

  const { openPublish } = usePublishDrawer()
  const handlePublishClick = useCallback(() => {
    if (isAuthenticated) {
      openPublish()
      return
    }
    setPostLoginRedirect('/profile/publish?kind=skill')
    navigate('/login')
  }, [isAuthenticated, navigate, openPublish])

  const handleFavoriteComingSoon = () => {
    window.alert(t('plugins.actions.favoritePending'))
  }

  const isOwnSkill = useCallback(
    (plugin: MarketPlugin) => Boolean(user?.id && plugin.publisherId && String(user.id) === String(plugin.publisherId)),
    [user?.id],
  )

  const handleToggleInteract = useCallback(
    async (plugin: MarketPlugin, actionType: 'like' | 'star') => {
      const blockedByModeration = plugin.moderationStatus === 'PENDING' || plugin.moderationStatus === 'REJECTED'
      if (!isAuthenticated) {
        setPostLoginRedirect('/?category=all')
        navigate('/login')
        return
      }
      if (isOwnSkill(plugin)) return
      if (blockedByModeration) return
      const key = `${plugin.assetId}:${actionType}`
      if (interactingKey === key) return
      // 在 await 前 capture，防止异步期间列表刷新导致 setQueryData 写到过期 key
      const snapshotIds = marketAssetIds
      const snapshotViewerKey = interactionViewerKey
      setInteractingKey(key)
      try {
        const result = await togglePluginInteract(plugin.assetId, actionType)
        queryClient.setQueryData<AssetInteractionState[]>(
          ['market-interactions', snapshotViewerKey, ...snapshotIds],
          old => {
            const base =
              old?.length ?
                old
              : snapshotIds.map(aid => {
                  const p = marketPlugins.find(x => x.assetId === aid)
                  return {
                    asset_id: aid,
                    liked: false,
                    starred: false,
                    like_count: p?.likeCount ?? 0,
                    star_count: p?.starCount ?? 0,
                  }
                })
            return base.map(item =>
              item.asset_id !== plugin.assetId ?
                item
              : {
                  ...item,
                  liked: actionType === 'like' ? result.active : item.liked,
                  starred: actionType === 'star' ? result.active : item.starred,
                  like_count: actionType === 'like' ? (result.like_count ?? item.like_count) : item.like_count,
                  star_count: actionType === 'star' ? (result.star_count ?? item.star_count) : item.star_count,
                },
            )
          },
        )
        // 这里不立即重拉 market-interactions：在部分环境下会被短暂陈旧读覆盖，出现 1 -> 0 回跳。
        // 首页展示以 toggle 返回结果为准，后续自然进入页面/手动刷新时再同步。
        void queryClient.invalidateQueries(['skill-interactions', plugin.assetId])
      } catch (e) {
        const msg = e instanceof Error ? e.message : t('plugins.actions.favoritePending')
        window.alert(msg)
      } finally {
        setInteractingKey(null)
      }
    },
    [interactionViewerKey, interactingKey, isAuthenticated, isOwnSkill, marketAssetIds, marketPlugins, navigate, queryClient, t],
  )

  const handleDownloadPlugin = useCallback(
    async (plugin: MarketPlugin, version?: string) => {
      if (downloadLockRef.current) return
      downloadLockRef.current = true
      setDownloadingAssetId(plugin.assetId)
      try {
        const meta = await getPluginArtifactDownload(plugin.assetId, version)
        const baseName = meta.name.trim() || plugin.displayName.trim() || plugin.assetId || 'plugin'
        const safeName = baseName.replace(/\s+/g, '-')
        const filename = `${safeName}_${meta.version}.zip`
        await triggerPluginFileDownload(meta.download_url, filename)
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            void refreshMarketPlugins()
          })
        })
      } catch {
        window.alert(t('plugins.actions.downloadFailed'))
      } finally {
        downloadLockRef.current = false
        setDownloadingAssetId(null)
      }
    },
    [refreshMarketPlugins, t],
  )

  const gridView = useMemo(() => {
    if (marketPlugins.length === 0)
      return (
        <Empty
          searchTerm={searchKeyword}
          type="plugins"
          customTitle={t('plugins.noMatchingSkill')}
          customDescription={t('plugins.noMatchingSkillDescription')}
        />
      )

    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {marketPlugins.map(plugin => {
          const intro = truncatePluginIntro(plugin.shortDesc || t('plugins.noDescription'), PLUGIN_INTRO_DISPLAY_MAX)
          const ownSkill = isOwnSkill(plugin)
          const blockedByModeration = plugin.moderationStatus === 'PENDING' || plugin.moderationStatus === 'REJECTED'
          const interactDisabled = ownSkill || blockedByModeration
          const interactForbiddenTip = ownSkill
            ? t('plugins.actions.selfInteractForbidden')
            : blockedByModeration
              ? (
                plugin.moderationStatus === 'PENDING'
                  ? t('plugins.skillPage.moderationPending')
                  : t('plugins.skillPage.moderationRejected')
              )
              : ''

          return (
            <div
              key={plugin.assetId}
              className="group relative rounded-2xl border border-[#e6e6e6] bg-white/95 backdrop-blur-sm transition-all duration-300 hover:shadow-[0_4px_40px_rgba(0,0,0,0.1)] hover:border-[#d0d0d0] cursor-pointer"
              onClick={() => handleViewPlugin(plugin)}
            >
              {plugin.pinOrder != null && (
                <Tooltip {...pluginCardTooltipProps} title={t('plugins.pinnedBadge')}>
                  <span
                    className="pointer-events-none absolute right-3 top-3 z-10 inline-flex h-7 w-7 items-center justify-center rounded-lg bg-amber-50 text-amber-600 shadow-sm ring-1 ring-amber-200/80"
                    aria-label={t('plugins.pinnedBadgeAria')}
                  >
                    <Pin className="h-3.5 w-3.5" strokeWidth={2.25} aria-hidden />
                  </span>
                </Tooltip>
              )}
              <div className="p-5">
                <div className="flex items-start gap-3 mb-3">
                  <PluginAvatar iconUri={plugin.iconUri} displayName={plugin.displayName} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-[#191919] text-base font-semibold truncate">{plugin.displayName}</h3>
                    </div>
                    <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                      {plugin.tags && plugin.tags.length > 0 && plugin.tags.slice(0, 3).map((tag, i) => (
                        <span key={`${tag}-${i}`} className="inline-block px-2 py-0.5 rounded text-xs text-[#191919] bg-[#f5f5f5] truncate max-w-[100px]">
                          {tag}
                        </span>
                      ))}
                      {plugin.tags && plugin.tags.length > 3 && (
                        <Tooltip {...pluginCardTooltipProps} title={plugin.tags.slice(3).join(' · ')}>
                          <span className="inline-block px-2 py-0.5 rounded text-xs text-[#777777] bg-[#f5f5f5]">
                            +{plugin.tags.length - 3}
                          </span>
                        </Tooltip>
                      )}
                      {plugin.latestVersion && (
                        <span className="inline-block px-2 py-0.5 rounded text-xs text-[#191919] bg-[#f5f5f5]">
                          v{plugin.latestVersion}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <p className="text-sm text-[#808080] leading-relaxed line-clamp-2 mb-4 min-h-[44px]" title={intro.truncated ? intro.full : undefined}>
                  {intro.display}
                </p>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 text-xs text-[#777777]">
                    <Tooltip
                      {...pluginCardTooltipProps}
                      title={interactForbiddenTip}
                      disableHoverListener={!interactForbiddenTip}
                    >
                      <span className="inline-flex">
                        <button
                          type="button"
                          disabled={interactDisabled || interactingKey === `${plugin.assetId}:like`}
                          onClick={e => {
                            e.stopPropagation()
                            void handleToggleInteract(plugin, 'like')
                          }}
                          className={`inline-flex items-center gap-1 tabular-nums transition-colors ${
                            ownSkill
                              ? 'cursor-default text-[#777777]'
                              : blockedByModeration
                                ? 'cursor-default text-[#9ca3af]'
                                : (interactionStateMap[plugin.assetId]?.liked ?? false)
                                  ? 'text-rose-600'
                                  : 'text-[#777777] hover:text-rose-600'
                          }`}
                        >
                          <Heart
                            className={`w-3.5 h-3.5 shrink-0 ${
                              (interactionStateMap[plugin.assetId]?.liked ?? false)
                                ? 'fill-rose-600 text-rose-600'
                                : 'text-rose-500'
                            }`}
                          />
                          {interactionStateMap[plugin.assetId]?.like_count ?? plugin.likeCount}
                        </button>
                      </span>
                    </Tooltip>
                    <Tooltip
                      {...pluginCardTooltipProps}
                      title={interactForbiddenTip}
                      disableHoverListener={!interactForbiddenTip}
                    >
                      <span className="inline-flex">
                        <button
                          type="button"
                          disabled={interactDisabled || interactingKey === `${plugin.assetId}:star`}
                          onClick={e => {
                            e.stopPropagation()
                            void handleToggleInteract(plugin, 'star')
                          }}
                          className={`inline-flex items-center gap-1 tabular-nums transition-colors ${
                            ownSkill
                              ? 'cursor-default text-[#777777]'
                              : blockedByModeration
                                ? 'cursor-default text-[#9ca3af]'
                                : (interactionStateMap[plugin.assetId]?.starred ?? false)
                                  ? 'text-amber-500'
                                  : 'text-[#777777] hover:text-amber-500'
                          }`}
                        >
                          <Star
                            className={`w-3.5 h-3.5 shrink-0 ${
                              (interactionStateMap[plugin.assetId]?.starred ?? false)
                                ? 'fill-amber-500 text-amber-500'
                                : 'text-amber-500'
                            }`}
                          />
                          {interactionStateMap[plugin.assetId]?.star_count ?? plugin.starCount}
                        </button>
                      </span>
                    </Tooltip>
                    <span
                      className="inline-flex items-center gap-1 tabular-nums"
                      title={t('plugins.detail.installCount')}
                    >
                      <Download className="w-3.5 h-3.5 shrink-0 text-indigo-500" aria-hidden />
                      {plugin.installCount}
                    </span>
                    <span
                      className="inline-flex items-center gap-1 tabular-nums"
                      title={t('plugins.detail.viewCount')}
                    >
                      <Eye className="w-3.5 h-3.5 shrink-0 text-sky-600" aria-hidden />
                      {plugin.viewCount}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={downloadingAssetId === plugin.assetId}
                    onClick={e => {
                      e.stopPropagation()
                      const v = defaultDownloadVersion(plugin).trim()
                      void handleDownloadPlugin(plugin, v || undefined)
                    }}
                    className="text-sm font-medium bg-gradient-to-r from-[#1E54F9] to-[#852EFE] bg-clip-text text-transparent hover:opacity-80 transition-opacity disabled:opacity-50 disabled:pointer-events-none"
                  >
                    {downloadingAssetId === plugin.assetId ? t('plugins.actions.download') + '...' : t('plugins.actions.download')}
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
  }, [
    defaultDownloadVersion,
    downloadingAssetId,
    handleDownloadPlugin,
    handleToggleInteract,
    interactingKey,
    interactionStateMap,
    isOwnSkill,
    marketPlugins,
    searchKeyword,
    t,
  ])

  const sidebar = useMemo(() => (
    <aside className="hidden w-[248px] shrink-0 lg:block">
      <nav className="flex flex-col gap-1" aria-label={t('plugins.categoryNavAria')}>
        {CATEGORY_KEYS.map(key => {
          const isActive = activeCategory === key
          const isHot = key === 'hot'
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleSetCategory(key)}
              className={`flex h-9 w-full items-center gap-2.5 rounded-lg px-3 text-sm transition-colors ${
                isHot
                  ? isActive
                    ? 'bg-gradient-to-r from-[#FFF7ED] to-[#FFFBF1] font-medium text-[#191919]'
                    : 'text-[#191919] hover:bg-orange-50/50'
                  : isActive
                    ? 'bg-[#f0f5ff] font-medium text-[#191919]'
                    : 'text-[#191919] hover:bg-gray-50'
              }`}
            >
              <span className={isActive ? 'text-[#1E54F9]' : 'text-[#191919]'}>
                {CATEGORY_ICONS[key]}
              </span>
              <span>{t(`plugins.category.${key}`)}</span>
            </button>
          )
        })}
      </nav>
    </aside>
  ), [activeCategory, t, handleSetCategory])

  const categoryMobileNav = useMemo(
    () => (
      <nav
        className="-mx-1 flex gap-2 overflow-x-auto pb-2 pl-1 pr-2 lg:hidden"
        style={{ WebkitOverflowScrolling: 'touch' }}
        aria-label={t('plugins.categoryNavAria')}
      >
        {CATEGORY_KEYS.map(key => {
          const isActive = activeCategory === key
          const isHot = key === 'hot'
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleSetCategory(key)}
              className={`flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-2 text-xs font-medium shadow-sm transition-colors sm:text-sm ${
                isHot
                  ? isActive
                    ? 'border-orange-200/90 bg-gradient-to-r from-[#FFF7ED] to-[#FFFBF1] text-[#191919]'
                    : 'border-slate-200/80 bg-white/90 text-[#191919] hover:border-orange-100'
                  : isActive
                    ? 'border-[#bfdbfe] bg-[#f0f5ff] text-[#191919]'
                    : 'border-slate-200/80 bg-white/90 text-[#191919] hover:border-slate-300'
              }`}
            >
              <span className={isActive ? 'text-[#1E54F9]' : 'text-[#191919]'}>{CATEGORY_ICONS[key]}</span>
              <span className="whitespace-nowrap">{t(`plugins.category.${key}`)}</span>
            </button>
          )
        })}
      </nav>
    ),
    [activeCategory, t, handleSetCategory],
  )

  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
      <AppHeader onPublish={handlePublishClick} />

      <div className="w-full">
        <div className="mx-auto max-w-[1600px] px-4 sm:px-6 lg:px-8">
          <div className="py-4 text-center sm:py-6">
            <h1 className="text-[22px] font-semibold leading-tight tracking-tight text-[#191919] sm:text-[28px] md:text-[40px]">
              {t('plugins.marketTitle')} {t('plugins.marketHeroSuffix')}
            </h1>
            <p className="mx-auto mt-2 max-w-[600px] px-1 text-sm leading-relaxed text-[#595959] md:text-base">
              {t('plugins.marketSubtitle')}
            </p>
          </div>

          <div className="mb-5 flex items-center justify-center sm:mb-6">
            <div className="relative w-full max-w-[1000px] lg:translate-x-2">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-[#aeaeae] sm:left-4" />
              <input
                type="text"
                placeholder={t('plugins.searchPlaceholder')}
                value={searchInput}
                onChange={e => {
                  const val = e.target.value
                  setSearchInput(val)
                  if (!val.trim()) { setSearchKeyword(''); setCurrentPage(1) }
                }}
                onKeyDown={e => { if (e.key === 'Enter') handleSearch() }}
                className={`h-12 w-full rounded-full bg-white pl-10 pr-16 text-[15px] text-[#191919] shadow-[0_4px_45px_rgba(0,0,0,0.1)] transition-all placeholder:text-[#aeaeae] hover:shadow-[0_4px_50px_rgba(0,0,0,0.12)] focus:outline-none focus:shadow-[0_4px_50px_rgba(0,0,0,0.15)] sm:h-14 sm:pl-12 sm:pr-20 sm:text-base ${
                  searchKeyword ? 'border-2 border-[#1E54F9]/60' : 'border border-transparent'
                }`}
              />
              {(searchInput || searchKeyword) && (
                <button
                  type="button"
                  onClick={() => { setSearchInput(''); setSearchKeyword(''); setCurrentPage(1) }}
                  className="absolute right-12 top-1/2 -translate-y-1/2 text-[#aeaeae] transition-colors hover:text-[#777777] sm:right-14"
                >
                  <X className="h-5 w-5" />
                </button>
              )}
              <button
                type="button"
                onClick={handleSearch}
                disabled={fetching}
                className={`absolute right-2 top-1/2 -translate-y-1/2 flex h-8 w-8 items-center justify-center rounded-full text-white transition-all sm:h-10 sm:w-10 ${
                  searchInput.trim() && searchInput.trim() !== searchKeyword
                    ? 'bg-[#1E54F9] hover:opacity-80'
                    : searchKeyword
                      ? 'bg-[#1E54F9]/70'
                      : 'bg-[#cccccc]'
                }`}
              >
                {fetching && searchKeyword
                  ? <RefreshCw className="h-4 w-4 animate-spin" />
                  : <Search className="h-4 w-4" />
                }
              </button>
            </div>
          </div>

          <div className="mb-3 flex justify-end sm:items-center sm:justify-between">
            <div className="hidden sm:block" />
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="h-9 px-4 bg-white border border-[#e6e6e6] text-[#191919] rounded-lg text-sm font-medium hover:bg-[#f9f9f9] hover:border-[#d0d0d0] transition-colors flex items-center gap-2"
            >
              {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              <span>{t('plugins.actions.refresh')}</span>
            </button>
          </div>

          {!noticeDismissed && (
            <div
              role="note"
              className="animate-notice-in group relative mb-5 flex flex-col gap-2 overflow-hidden rounded-2xl border border-indigo-100/80 bg-gradient-to-r from-indigo-50/70 via-white/70 to-fuchsia-50/70 px-3 py-3 text-sm text-slate-700 shadow-[0_4px_24px_rgba(79,70,229,0.06)] backdrop-blur-sm sm:flex-row sm:items-center sm:gap-3 sm:py-2.5"
            >
              <span
                aria-hidden
                className="pointer-events-none absolute inset-y-0 left-0 w-[3px] bg-gradient-to-b from-[#1E54F9] to-[#852EFE] opacity-80"
              />

              <div className="hidden w-8 shrink-0 sm:block" aria-hidden />

              <div className="flex min-w-0 flex-1 justify-center sm:justify-center">
                <div className="flex max-w-full items-start gap-2.5 text-left sm:items-center">
                  <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#1E54F9] to-[#852EFE] text-white shadow-sm ring-1 ring-white/60 sm:mt-0">
                    <Info className="h-3.5 w-3.5" aria-hidden />
                  </span>
                  <span className="text-[13px] leading-relaxed tracking-[0.005em] text-slate-700 sm:text-[13.5px]">
                    {t('plugins.modelAccessNotice')}
                  </span>
                </div>
              </div>

              <div className="flex shrink-0 justify-end sm:w-8 sm:items-center sm:justify-end">
                <button
                  type="button"
                  onClick={dismissModelAccessNotice}
                  aria-label={t('common.buttons.close')}
                  title={t('common.buttons.close')}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 transition-all hover:bg-white/80 hover:text-slate-700 hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
            </div>
          )}

          {error && (
            <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3">
              <div className="flex items-center">
                <span className="text-red-800 text-sm">{error}</span>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-4 pb-4 lg:flex-row lg:gap-5">
            {categoryMobileNav}
            {sidebar}
            <div className="min-w-0 flex-1">
              {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
                </div>
              ) : (
                gridView
              )}
            </div>
          </div>
        </div>
      </div>

      {total > 0 && (
        <div className="shrink-0 border-t border-[#e5e7eb] bg-gradient-to-r from-[#E3F2FD] to-[#F3E9FF] px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom,0px))] sm:px-6 lg:px-8">
          <div className="mx-auto flex max-w-[1600px] flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
              <span>{t('common.pagination.pageSize')}</span>
              <select
                value={pageSize}
                onChange={e => { setPageSize(Number(e.target.value)); setCurrentPage(1) }}
                className="px-2 py-1 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {PAGE_SIZE_OPTIONS.map(opt => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
              <span>{t('common.pagination.items')}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700 sm:justify-end">
              <span>{t('common.pagination.total', { total })}</span>
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="p-2 text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                ‹
              </button>
              <span>{t('common.pagination.pagePrefix')} {page} {t('common.pagination.pageSuffix', { total: Math.ceil(total / pageSize) })}</span>
              <button
                onClick={() => setCurrentPage(p => Math.min(Math.ceil(total / pageSize), p + 1))}
                disabled={page >= Math.ceil(total / pageSize)}
                className="p-2 text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                ›
              </button>
            </div>
          </div>
        </div>
      )}

      <Dialog
        open={detailDialogOpen}
        onClose={() => setDetailDialogOpen(false)}
        maxWidth="md"
        fullWidth
        fullScreen={detailDialogFullScreen}
        slotProps={{
          paper: {
            sx: detailDialogFullScreen
              ? { borderRadius: 0, margin: 0, maxHeight: '100%' }
              : { borderRadius: 3 },
          },
        }}
      >
        {selectedPlugin && (
          <>
            <DialogTitle className="flex items-start justify-between gap-3">
              <div className="flex items-center space-x-3 min-w-0">
                <PluginAvatar iconUri={selectedPlugin.iconUri} displayName={selectedPlugin.displayName} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
                    <Typography variant="h6" className="truncate text-[#111827] font-black min-w-0">
                      {selectedPlugin.displayName}
                    </Typography>
                    {selectedPlugin.latestVersion ? (
                      <span className="shrink-0 px-2 py-0.5 rounded-md text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-100/80">
                        v{selectedPlugin.latestVersion}
                      </span>
                    ) : null}
                  </div>
                  <Typography variant="caption" color="text.secondary" className="block mt-0.5 truncate">
                    {t('plugins.detail.publisher')}: {selectedPlugin.publisherName || '-'}
                  </Typography>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Tooltip {...pluginDetailHeaderTooltipProps} title={t('plugins.detail.ratingTooltip')}>
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-100 px-2 py-1 text-amber-700 text-xs font-semibold cursor-default">
                    <BarChart3 className="w-3.5 h-3.5 text-amber-500" />
                    {selectedPlugin.averageRating}
                  </span>
                </Tooltip>
                <Tooltip {...pluginDetailHeaderTooltipProps} title={t('plugins.actions.favorite')}>
                  <IconButton
                    size="small"
                    onClick={handleFavoriteComingSoon}
                    sx={{ color: '#64748b' }}
                  >
                    <Star className="w-4 h-4" />
                  </IconButton>
                </Tooltip>
              </div>
            </DialogTitle>
            <DialogContent>
              <div className="space-y-5">
                <div>
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="flex items-center gap-1.5 shrink-0">
                      <AlignLeft className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" component="span" className="font-bold text-gray-900">
                        {t('plugins.detail.summary')}:
                      </Typography>
                    </div>
                    <div className="flex min-w-0 flex-1 items-center">
                      {selectedPlugin.shortDesc ? (
                        <PluginMarkdown
                          source={selectedPlugin.shortDesc}
                          className="prose prose-sm prose-neutral max-w-none flex-1 min-w-0 text-gray-900 prose-p:my-0 prose-headings:scroll-mt-2 [&_p]:leading-snug [&_p]:text-[0.9375rem]"
                        />
                      ) : (
                        <Typography variant="body2">{t('plugins.noDescription')}</Typography>
                      )}
                    </div>
                  </div>
                </div>
                <div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className="rounded-lg border border-[#DCEEFE] bg-[#F3FAFF] px-3 py-3 min-h-[108px]">
                      <div className="flex flex-col items-center text-center">
                        <Eye className="w-4 h-4 text-sky-600 mb-2" />
                        <div className="text-sky-700 tabular-nums font-extrabold text-lg leading-7">
                        {selectedPlugin.viewCount}
                        </div>
                        <div className="text-[11px] text-sky-600 mt-2">{t('plugins.detail.viewCount')}</div>
                      </div>
                    </div>
                    <div className="rounded-lg border border-[#E0E7FF] bg-[#F4F6FF] px-3 py-3 min-h-[108px]">
                      <div className="flex flex-col items-center text-center">
                        <Download className="w-4 h-4 text-indigo-600 mb-2" />
                        <div className="text-indigo-700 tabular-nums font-extrabold text-lg leading-7">
                        {selectedPlugin.installCount}
                        </div>
                        <div className="text-[11px] text-indigo-600 mt-2">{t('plugins.detail.installCount')}</div>
                      </div>
                    </div>
                    <div className="rounded-lg border border-[#FFE2EA] bg-[#FFF4F7] px-3 py-3 min-h-[108px]">
                      <div className="flex flex-col items-center text-center">
                        <Heart className="w-4 h-4 text-rose-600 mb-2" />
                        <div className="text-rose-700 tabular-nums font-extrabold text-lg leading-7">
                        {interactionStateMap[selectedPlugin.assetId]?.like_count ?? selectedPlugin.likeCount}
                        </div>
                        <div className="text-[11px] text-rose-600 mt-2">{t('plugins.detail.likeCount')}</div>
                      </div>
                    </div>
                    <div className="rounded-lg border border-[#D8F2F5] bg-[#F2FBFC] px-3 py-3 min-h-[108px]">
                      <div className="flex flex-col items-center text-center">
                        <MessageCircle className="w-4 h-4 text-cyan-600 mb-2" />
                        <div className="text-cyan-700 tabular-nums font-extrabold text-lg leading-7">
                        {selectedPlugin.reviewCount}
                        </div>
                        <div className="text-[11px] text-cyan-600 mt-2">{t('plugins.detail.reviewCount')}</div>
                      </div>
                    </div>
                  </div>
                </div>
                {(selectedPlugin.detailDesc || '').trim().length > 0 && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <BookOpen className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.description')}
                      </Typography>
                    </div>
                    <PluginMarkdown
                      source={selectedPlugin.detailDesc}
                      className="prose prose-sm prose-neutral max-w-none h-64 overflow-y-auto p-4 bg-blue-50 rounded-lg border border-blue-100 shadow-sm prose-headings:scroll-mt-2 prose-pre:bg-gray-900 prose-pre:text-gray-100"
                    />
                  </div>
                )}
                {effectiveDetailVersion ? (
                  <div className="rounded-lg border border-slate-200/90 bg-slate-50/80 p-4">
                    <div className="mb-2 flex items-center gap-1.5">
                      <ScrollText className="h-4 w-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.versionChangelog', { version: effectiveDetailVersion })}
                      </Typography>
                    </div>
                    {detailChangelogLoading ? (
                      <div className="flex items-center gap-2 py-2 text-slate-600">
                        <CircularProgress size={18} />
                        <Typography variant="body2">{t('plugins.detail.changelogLoading')}</Typography>
                      </div>
                    ) : detailChangelogError ? (
                      <Typography variant="body2" color="error">
                        {detailChangelogError}
                      </Typography>
                    ) : detailChangelog ? (
                      <PluginMarkdown
                        source={detailChangelog}
                        className="prose prose-sm prose-neutral max-w-none max-h-48 overflow-y-auto text-gray-900 prose-p:my-1 prose-headings:my-2 prose-headings:scroll-mt-2 [&_p]:text-[0.9375rem]"
                      />
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        {t('plugins.detail.changelogEmpty')}
                      </Typography>
                    )}
                  </div>
                ) : null}
                {selectedPlugin.allVersions.length > 1 ? (
                  <FormControl fullWidth size="small">
                    <InputLabel id="plugin-detail-version-label">{t('plugins.detail.downloadVersion')}</InputLabel>
                    <Select
                      labelId="plugin-detail-version-label"
                      label={t('plugins.detail.downloadVersion')}
                      value={detailDownloadVersion || defaultDownloadVersion(selectedPlugin)}
                      onChange={e => setDetailDownloadVersion(String(e.target.value))}
                    >
                      {[...selectedPlugin.allVersions].reverse().map(v => (
                        <MenuItem key={v} value={v}>
                          v{v}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                ) : null}
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="flex items-center gap-1.5">
                      <Cpu className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.runtime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">{selectedPlugin.runTime}</Typography>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <Tag className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.tags')}
                      </Typography>
                    </div>
                    <div className="mt-1 min-h-[22px]">
                      {selectedPlugin.tags?.length ? (
                        <DetailPluginTags tags={selectedPlugin.tags} />
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          -
                        </Typography>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <CalendarPlus className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.createTime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">
                      {formatPluginDateTime(selectedPlugin.createTime, i18n.language)}
                    </Typography>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <CalendarClock className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.updateTime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">
                      {formatPluginDateTime(selectedPlugin.updateTime, i18n.language)}
                    </Typography>
                  </div>
                </div>
              </div>
            </DialogContent>
            <DialogActions sx={{ flexWrap: 'wrap', gap: 1 }}>
              <Button onClick={() => setDetailDialogOpen(false)}>{t('common.buttons.close')}</Button>
              <Button
                variant="contained"
                startIcon={<Download className="h-4 w-4" />}
                disabled={downloadingAssetId === selectedPlugin.assetId}
                onClick={() => {
                  const v = (detailDownloadVersion || defaultDownloadVersion(selectedPlugin)).trim()
                  void handleDownloadPlugin(selectedPlugin, v || undefined)
                }}
                sx={{ textTransform: 'none' }}
              >
                {t('plugins.actions.download')}
              </Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </div>
  )
}
