// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  BarChart3,
  CalendarClock,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Download,
  Eye,
  Flame,
  Heart,
  Info,
  LayoutGrid,
  List,
  MessageCircle,
  RefreshCw,
  ScrollText,
  Search,
  Sparkles,
  Star,
  Tag,
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
import { useQueries, useQuery, useQueryClient } from 'react-query'
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
} from '@/api/plugin'
import { getPlugins, usePluginListQuery } from '@/api'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { usePluginMarketConfigs, type MarketPlugin } from '@/hooks/usePluginMarketConfigs'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import {
  formatMarketSkillVersionLabel,
  marketSkillVersionFilenameSegment,
} from '@/utils/formatSkillVersionLabel'
import { getPrimarySkillPluginType, parseSkillLikePluginType } from '@/utils/pluginType'

const MARKETPLACE_ACTIVE_TYPE_KEY = 'skillhub.marketplace.activeType'

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

const PLUGIN_INTRO_DISPLAY_MAX = 120

type ViewMode = 'grid' | 'list'

function SkillHubGlyph({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <rect x="4.5" y="6" width="15" height="12" rx="4" stroke="currentColor" strokeWidth="1.8" />
      <path d="M9 9.5h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M9 12h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M9.8 14.5h4.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

function SwarmHubGlyph({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="8" cy="8" r="2.2" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="16" cy="8" r="2.2" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="12" cy="15.5" r="2.4" stroke="currentColor" strokeWidth="1.7" />
      <path d="M9.9 9.2 10.8 10.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M14.1 9.2 13.2 10.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M10 13.8h4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

type SkillTypeTab = 'skill' | 'swarmskill'

function getMarketTypeFromSearchParams(searchParams: URLSearchParams): SkillTypeTab | null {
  return parseSkillLikePluginType(searchParams.get('type'))
}

type CategoryKey =
  | 'hot'
  | 'newest'
  | 'all'
  | 'software-development'
  | 'office-productivity'
  | 'content-creation'
  | 'multimodal-media'
  | 'data-science-research'
  | 'compliance-legal'
  | 'lifestyle-health'
  | 'finance-wealth'

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
  { bg: 'linear-gradient(135deg, #EBF7FF, #C2E4FF)', color: '#1E54F9', shadow: '0 2px 15px rgba(30,84,249,0.25)' },
  { bg: 'linear-gradient(135deg, #FFF0F5, #FFD2E1)', color: '#E04379', shadow: '0 2px 15px rgba(224,67,121,0.25)' },
  { bg: 'linear-gradient(135deg, #F5F0FF, #E6DAFF)', color: '#7C3AED', shadow: '0 2px 15px rgba(124,58,237,0.25)' },
  { bg: 'linear-gradient(135deg, #F0FFD9, #D5FFB9)', color: '#2D8B4E', shadow: '0 2px 15px rgba(45,139,78,0.25)' },
  { bg: 'linear-gradient(135deg, #FFF3E8, #FFE1C5)', color: '#D97706', shadow: '0 2px 15px rgba(217,119,6,0.25)' },
  { bg: 'linear-gradient(135deg, #E5FFF6, #B1F5E0)', color: '#0891B2', shadow: '0 2px 15px rgba(8,145,178,0.25)' },
]

const HAN_SCRIPT_RE = /\p{Script=Han}/u
const PINYIN_AVATAR_OPTS = { toneType: 'none' as const, type: 'string' as const }

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

function hasPluginIconSrc(icon: string | undefined): boolean {
  if (typeof icon !== 'string' || !icon.trim()) return false
  const t = icon.trim()
  if (t.startsWith('http://') || t.startsWith('https://')) return true
  if (t.startsWith('/') && !t.startsWith('//')) return true
  if (t.startsWith('data:image/')) return true
  if (t.startsWith('blob:')) return true
  return t.includes('.')
}

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
      className={`relative flex h-[44px] w-[44px] min-w-[44px] items-center justify-center overflow-hidden rounded-[15px] px-0.5 font-semibold leading-none shadow-[0_1px_2px_rgba(15,23,42,0.06)] sm:h-12 sm:w-12 sm:min-w-[48px] ${ch.length > 1 ? 'text-base sm:text-lg' : 'text-[20px] sm:text-[24px]'}`}
      style={{ background: palette.bg, color: palette.color, boxShadow: `${palette.shadow}, inset 0 0 0 1px rgba(15,23,42,0.06)` }}
    >
      <span style={{ opacity: iconShown ? 0 : 1 }}>{ch || '?'}</span>
      {shouldTryIcon && (
        <img
          key={iconUri}
          src={iconUri}
          alt=""
          className="absolute inset-0 h-full w-full rounded-[15px] object-cover"
          style={{ display: iconShown ? 'block' : 'none' }}
          onLoad={handleImgLoad}
          onError={handleImgError}
        />
      )}
    </div>
  )
}

const CATEGORY_KEYS: CategoryKey[] = [
  'hot',
  'newest',
  'all',
  'software-development',
  'office-productivity',
  'content-creation',
  'multimodal-media',
  'data-science-research',
  'compliance-legal',
  'lifestyle-health',
  'finance-wealth',
]

function isConcreteCategoryKey(k: CategoryKey): k is Exclude<CategoryKey, 'hot' | 'newest' | 'all'> {
  return k !== 'hot' && k !== 'newest' && k !== 'all'
}

const CONCRETE_CATEGORY_KEYS = CATEGORY_KEYS.filter(isConcreteCategoryKey)

const CATEGORY_ICONS: Record<CategoryKey, React.ReactNode> = {
  hot: <Flame className="h-4 w-4" />,
  newest: <Sparkles className="h-4 w-4" />,
  all: <LayoutGrid className="h-4 w-4" />,
  'software-development': <Cpu className="h-4 w-4" />,
  'office-productivity': <AlignLeft className="h-4 w-4" />,
  'content-creation': <BookOpen className="h-4 w-4" />,
  'multimodal-media': <Search className="h-4 w-4" />,
  'data-science-research': <BarChart3 className="h-4 w-4" />,
  'compliance-legal': <Heart className="h-4 w-4" />,
  'lifestyle-health': <MessageCircle className="h-4 w-4" />,
  'finance-wealth': <ScrollText className="h-4 w-4" />,
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
    <div className="animate-pulse rounded-[28px] border border-white/80 bg-white/80 p-5 shadow-[0_20px_60px_rgba(18,52,107,0.08)]">
      <div className="mb-4 flex items-start gap-3">
        <div className="h-12 w-12 shrink-0 rounded-2xl bg-slate-200" />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-4 w-3/4 rounded bg-slate-200" />
          <div className="h-3 w-1/2 rounded bg-slate-200" />
        </div>
      </div>
      <div className="mb-5 space-y-2">
        <div className="h-3 rounded bg-slate-200" />
        <div className="h-3 w-5/6 rounded bg-slate-200" />
      </div>
      <div className="flex items-center justify-between">
        <div className="h-3 w-1/3 rounded bg-slate-200" />
        <div className="h-8 w-20 rounded-full bg-slate-200" />
      </div>
    </div>
  )
}

function SkeletonRow() {
  return (
    <div className="animate-pulse rounded-[28px] border border-white/80 bg-white/80 p-5 shadow-[0_20px_60px_rgba(18,52,107,0.06)]">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 rounded-2xl bg-slate-200" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-48 rounded bg-slate-200" />
          <div className="h-3 w-full max-w-[560px] rounded bg-slate-200" />
        </div>
        <div className="hidden h-9 w-24 rounded-full bg-slate-200 md:block" />
      </div>
    </div>
  )
}

function RefetchGridOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0 z-10 rounded-[20px] bg-white/45 backdrop-blur-[1px]">
      <div className="grid h-full grid-cols-1 gap-4 p-0 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="animate-pulse rounded-[28px] border border-white/75 bg-white/55 p-5 shadow-[0_20px_60px_rgba(18,52,107,0.04)]"
          >
            <div className="mb-4 flex items-start gap-3">
              <div className="h-12 w-12 shrink-0 rounded-2xl bg-white/75" />
              <div className="flex-1 space-y-2 pt-1">
                <div className="h-4 w-3/4 rounded bg-white/80" />
                <div className="h-3 w-1/2 rounded bg-white/80" />
              </div>
            </div>
            <div className="mb-5 space-y-2">
              <div className="h-3 rounded bg-white/80" />
              <div className="h-3 w-5/6 rounded bg-white/80" />
            </div>
            <div className="flex items-center justify-between">
              <div className="h-3 w-1/3 rounded bg-white/80" />
              <div className="h-8 w-20 rounded-full bg-white/80" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function RefetchListOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0 z-10 rounded-[20px] bg-white/45 backdrop-blur-[1px]">
      <div className="space-y-3 p-0">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="animate-pulse rounded-[28px] border border-white/75 bg-white/55 p-5 shadow-[0_20px_60px_rgba(18,52,107,0.04)]"
          >
            <div className="flex items-center gap-4">
              <div className="h-12 w-12 rounded-2xl bg-white/80" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-48 rounded bg-white/80" />
                <div className="h-3 w-full max-w-[560px] rounded bg-white/80" />
              </div>
              <div className="hidden h-9 w-24 rounded-full bg-white/80 md:block" />
            </div>
          </div>
        ))}
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
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {visible.map((tag, i) => (
        <span
          key={`${tag}-${i}`}
          className="shrink-0 rounded-md border border-black/5 px-2 py-0.5 text-[11px] font-medium"
          style={{ backgroundColor: tagBg[i % tagBg.length], color: tagFg[i % tagFg.length] }}
        >
          {tag}
        </span>
      ))}
      {hidden.length > 0 && (
        <Tooltip {...pluginCardTooltipProps} title={hidden.join(' · ')}>
          <span className="shrink-0 cursor-default rounded-md border border-gray-300/80 bg-gray-200 px-2 py-0.5 text-[11px] font-medium text-gray-700">
            +{hidden.length}
          </span>
        </Tooltip>
      )}
    </div>
  )
}

const PAGE_SIZE_OPTIONS = [12, 24, 48]
const MODEL_ACCESS_NOTICE_DISMISSED_KEY = 'marketplace_model_access_notice_dismissed_version1'

function ViewToggle({ value, onChange, t }: { value: ViewMode; onChange: (mode: ViewMode) => void; t: (key: string) => string }) {
  return (
    <div className="inline-flex items-center gap-1 rounded-full border border-[#E7EAF2] bg-white px-1.5 py-1 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <button
        type="button"
        onClick={() => onChange('grid')}
        aria-label={t('plugins.viewMode.grid')}
        title={t('plugins.viewMode.grid')}
        className={`inline-flex h-[26px] min-w-[46px] items-center justify-center gap-1 rounded-full px-2 text-[10px] font-medium transition-colors ${
          value === 'grid' ? 'bg-[#F4F6FF] text-[#4F46E5]' : 'text-slate-400 hover:text-slate-600'
        }`}
      >
        <LayoutGrid className="h-[13px] w-[13px]" />
        <span>{t('plugins.viewMode.grid')}</span>
      </button>
      <button
        type="button"
        onClick={() => onChange('list')}
        aria-label={t('plugins.viewMode.list')}
        title={t('plugins.viewMode.list')}
        className={`inline-flex h-[26px] min-w-[46px] items-center justify-center gap-1 rounded-full px-2 text-[10px] font-medium transition-colors ${
          value === 'list' ? 'bg-[#F4F6FF] text-[#4F46E5]' : 'text-slate-400 hover:text-slate-600'
        }`}
      >
        <List className="h-[13px] w-[13px]" />
        <span>{t('plugins.viewMode.list')}</span>
      </button>
    </div>
  )
}

export default function PluginMarketPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const { user, isAuthenticated } = useGitCodeAuth()
  const [searchInput, setSearchInput] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
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
  const activeCategory = useMemo<CategoryKey>(() => {
    const cat = searchParams.get('category')
    return cat && (CATEGORY_KEYS as string[]).includes(cat) ? (cat as CategoryKey) : 'all'
  }, [searchParams])
  const fallbackType = useMemo<SkillTypeTab>(() => {
    if (typeof window === 'undefined') return getPrimarySkillPluginType()
    try {
      return parseSkillLikePluginType(window.sessionStorage.getItem(MARKETPLACE_ACTIVE_TYPE_KEY)) ?? getPrimarySkillPluginType()
    } catch {
      return getPrimarySkillPluginType()
    }
  }, [])
  const activeType = useMemo<SkillTypeTab>(() => {
    return getMarketTypeFromSearchParams(searchParams) ?? fallbackType
  }, [fallbackType, searchParams])
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
  }, [activeCategory, activeType])

  useEffect(() => {
    const rawType = searchParams.get('type')
    const normalizedType = parseSkillLikePluginType(rawType)
    const resolvedType = normalizedType ?? fallbackType
    if (rawType === resolvedType) return
    setSearchParams(
      prev => {
        const next = new URLSearchParams(prev)
        next.set('type', resolvedType)
        return next
      },
      { replace: true },
    )
  }, [fallbackType, searchParams, setSearchParams])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      window.sessionStorage.setItem(MARKETPLACE_ACTIVE_TYPE_KEY, activeType)
    } catch {
    }
  }, [activeType])

  const handleSetCategory = useCallback(
    (key: CategoryKey) => {
      setSearchParams(
        prev => {
          const next = new URLSearchParams(prev)
          next.set('category', key)
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const isHotCategory = activeCategory === 'hot'
  const isNewestCategory = activeCategory === 'newest'
  const activeCategoryId = activeCategory === 'hot' || activeCategory === 'newest' || activeCategory === 'all' ? undefined : activeCategory

  const { marketPlugins, total, page, loading, fetching, error, refreshMarketPlugins } = usePluginMarketConfigs({
    page: currentPage,
    pageSize,
    searchKeyword,
    pluginType: activeType,
    categoryId: activeCategoryId,
    orderBy: isHotCategory ? 'install_count' : isNewestCategory ? 'create_time' : undefined,
    desc: isHotCategory || isNewestCategory ? true : undefined,
  })

  const approvedSkillMarketTotalQuery = usePluginListQuery({
    page: 1,
    page_size: 1,
    plugin_type: activeType,
    moderation_status: 'APPROVED',
  })
  const approvedSkillMarketTotal = approvedSkillMarketTotalQuery.data?.data?.total

  const categoryTotalsQueries = useQueries(
    CONCRETE_CATEGORY_KEYS.map(categoryId => ({
      queryKey: [
        'plugins',
        'skill-category-total',
        activeType,
        {
          page: 1,
          page_size: 1,
          plugin_type: activeType,
          moderation_status: 'APPROVED',
          category_id: categoryId,
        },
      ] as const,
      queryFn: () =>
        getPlugins({
          page: 1,
          page_size: 1,
          plugin_type: activeType,
          moderation_status: 'APPROVED',
          category_id: categoryId,
        }),
      keepPreviousData: true,
    })),
  )
  const categoryTotalSignature = categoryTotalsQueries.map(q => q.data?.data?.total).join(',')

  const categorySkillCount = useMemo((): Partial<Record<CategoryKey, number>> => {
    const out: Partial<Record<CategoryKey, number>> = {}
    if (typeof approvedSkillMarketTotal === 'number') {
      out.hot = approvedSkillMarketTotal
      out.newest = approvedSkillMarketTotal
      out.all = approvedSkillMarketTotal
    }
    for (let i = 0; i < CONCRETE_CATEGORY_KEYS.length; i += 1) {
      const key = CONCRETE_CATEGORY_KEYS[i]
      const n = categoryTotalsQueries[i]?.data?.data?.total
      if (key != null && typeof n === 'number') out[key] = n
    }
    return out
  }, [approvedSkillMarketTotal, categoryTotalSignature])

  const marketAssetIds = useMemo(() => marketPlugins.map(plugin => plugin.assetId).filter(Boolean), [marketPlugins])
  const interactionViewerKey = isAuthenticated ? `user:${user?.id ? String(user.id) : 'token'}` : 'guest'

  const interactionStatesQuery = useQuery(
    ['market-interactions', interactionViewerKey, ...marketAssetIds],
    () => getPluginInteractionsBatch(marketAssetIds),
    { enabled: marketAssetIds.length > 0, keepPreviousData: true },
  )

  const interactionStateMap = useMemo(() => {
    const map: Record<string, AssetInteractionState> = {}
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

  const handleSetType = useCallback(
    (nextType: SkillTypeTab) => {
      if (nextType === activeType) return
      setCurrentPage(1)
      setSearchParams(
        prev => {
          const next = new URLSearchParams(prev)
          next.set('type', nextType)
          return next
        },
        { replace: false },
      )
    },
    [activeType, setSearchParams],
  )

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
        if (isCanceledRequest(err)) return
        setDetailChangelogError(err instanceof Error ? err.message : t('plugins.detail.changelogLoadFailed'))
        setDetailChangelogLoading(false)
      })
    return () => ac.abort()
  }, [detailDialogOpen, selectedAssetId, effectiveDetailVersion, t, queryClient])

  const handleViewPlugin = (plugin: MarketPlugin) => {
    const version = defaultDownloadVersion(plugin)
    const query = version ? `?version=${encodeURIComponent(version)}` : ''
    navigate(`/skills/${encodeURIComponent(plugin.assetId)}${query}`)
  }

  const handleRefresh = async () => {
    await refreshMarketPlugins()
  }

  const { openPublish } = usePublishDrawer()
  const handlePublishClick = useCallback(() => {
    if (isAuthenticated) {
      openPublish(activeType)
      return
    }
    setPostLoginRedirect(`/profile/publish?kind=${activeType}`)
    navigate('/login')
  }, [activeType, isAuthenticated, navigate, openPublish])

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
        const redirect = searchParams.toString()
        setPostLoginRedirect(`/${redirect ? `?${redirect}` : ''}`)
        navigate('/login')
        return
      }
      if (isOwnSkill(plugin) || blockedByModeration) return
      const key = `${plugin.assetId}:${actionType}`
      if (interactingKey === key) return
      const snapshotIds = marketAssetIds
      const snapshotViewerKey = interactionViewerKey
      setInteractingKey(key)
      try {
        const result = await togglePluginInteract(plugin.assetId, actionType)
        queryClient.setQueryData<AssetInteractionState[]>(['market-interactions', snapshotViewerKey, ...snapshotIds], old => {
          const base =
            old?.length
              ? old
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
            item.asset_id !== plugin.assetId
              ? item
              : {
                  ...item,
                  liked: actionType === 'like' ? result.active : item.liked,
                  starred: actionType === 'star' ? result.active : item.starred,
                  like_count: actionType === 'like' ? (result.like_count ?? item.like_count) : item.like_count,
                  star_count: actionType === 'star' ? (result.star_count ?? item.star_count) : item.star_count,
                },
          )
        })
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
        const versionSeg = marketSkillVersionFilenameSegment(meta.version, plugin)
        const filename = `${safeName}_${versionSeg}.zip`
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

  const locale = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'
  const marketTypeLabel = t(`plugins.marketTypeLabel.${activeType}`)
  const noMatchingSkillTitle = t(`plugins.noMatchingSkillByType.${activeType}`)
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const renderInteractionTip = (plugin: MarketPlugin) => {
    const ownSkill = isOwnSkill(plugin)
    const blockedByModeration = plugin.moderationStatus === 'PENDING' || plugin.moderationStatus === 'REJECTED'
    return ownSkill
      ? t('plugins.actions.selfInteractForbidden')
      : blockedByModeration
        ? plugin.moderationStatus === 'PENDING'
          ? t('plugins.skillPage.moderationPending')
          : t('plugins.skillPage.moderationRejected')
        : ''
  }

  const renderStats = (plugin: MarketPlugin, compact = false) => {
    const ownSkill = isOwnSkill(plugin)
    const blockedByModeration = plugin.moderationStatus === 'PENDING' || plugin.moderationStatus === 'REJECTED'
    const interactDisabled = ownSkill || blockedByModeration
    const tip = renderInteractionTip(plugin)
    const itemClass = compact
      ? 'inline-flex h-6 sm:h-[26px] items-center gap-1 sm:gap-1.5 rounded-[4px] border border-[#EEF2F7] bg-[#FBFCFE] px-2 text-[11px] sm:text-[12px] font-medium text-slate-500'
      : 'inline-flex h-[30px] sm:h-8 items-center gap-1.5 sm:gap-2 rounded-full border border-[#EEF2F7] bg-[#FAFBFD] px-2.5 sm:px-3 text-[12px] sm:text-[13px] font-medium text-slate-500'
    return (
      <div
        className={`flex flex-wrap items-center text-slate-400 ${
          compact ? 'gap-1.5 text-[11px] sm:gap-2' : 'gap-2 text-[11px] sm:gap-[10px] sm:text-[12px]'
        }`}
      >
        <Tooltip {...pluginCardTooltipProps} title={tip} disableHoverListener={!tip}>
          <span className="inline-flex">
            <button
              type="button"
              disabled={interactDisabled || interactingKey === `${plugin.assetId}:like`}
              onClick={e => {
                e.stopPropagation()
                void handleToggleInteract(plugin, 'like')
              }}
              className={`${itemClass} tabular-nums transition-colors ${
                ownSkill
                  ? 'cursor-default'
                  : blockedByModeration
                    ? 'cursor-default'
                    : (interactionStateMap[plugin.assetId]?.liked ?? false)
                      ? 'border-rose-100 bg-rose-50/80 text-rose-600'
                      : 'hover:border-rose-100 hover:bg-rose-50/70 hover:text-rose-600'
              }`}
            >
              <Heart
                className={`h-5 w-5 shrink-0 ${(interactionStateMap[plugin.assetId]?.liked ?? false) ? 'fill-rose-600 text-rose-600' : 'text-rose-400'}`}
              />
              {interactionStateMap[plugin.assetId]?.like_count ?? plugin.likeCount}
            </button>
          </span>
        </Tooltip>
        <Tooltip {...pluginCardTooltipProps} title={tip} disableHoverListener={!tip}>
          <span className="inline-flex">
            <button
              type="button"
              disabled={interactDisabled || interactingKey === `${plugin.assetId}:star`}
              onClick={e => {
                e.stopPropagation()
                void handleToggleInteract(plugin, 'star')
              }}
              className={`${itemClass} tabular-nums transition-colors ${
                ownSkill
                  ? 'cursor-default'
                  : blockedByModeration
                    ? 'cursor-default'
                    : (interactionStateMap[plugin.assetId]?.starred ?? false)
                      ? 'border-amber-100 bg-amber-50/85 text-amber-600'
                      : 'hover:border-amber-100 hover:bg-amber-50/75 hover:text-amber-600'
              }`}
            >
              <Star
                className={`h-5 w-5 shrink-0 ${(interactionStateMap[plugin.assetId]?.starred ?? false) ? 'fill-amber-500 text-amber-500' : 'text-amber-400'}`}
              />
              {interactionStateMap[plugin.assetId]?.star_count ?? plugin.starCount}
            </button>
          </span>
        </Tooltip>
        <span className={itemClass} title={t('plugins.detail.installCount')}>
          <Download className="h-5 w-5 shrink-0 text-indigo-400" />
          {plugin.installCount}
        </span>
        <span className={itemClass} title={t('plugins.detail.viewCount')}>
          <Eye className="h-5 w-5 shrink-0 text-sky-500" />
          {plugin.viewCount}
        </span>
      </div>
    )
  }

  const gridView = useMemo(() => {
    if (marketPlugins.length === 0) {
      return (
        <div className="rounded-[32px] border border-dashed border-slate-200 bg-white/80 px-6 py-16 shadow-[0_24px_80px_rgba(15,23,42,0.06)]">
          <Empty
            searchTerm={searchKeyword}
            type="plugins"
            customTitle={noMatchingSkillTitle}
            customDescription={t('plugins.noMatchingSkillDescription')}
          />
        </div>
      )
    }

    return (
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 xl:gap-[18px] xl:justify-start">
        {marketPlugins.map(plugin => {
          const intro = truncatePluginIntro(plugin.shortDesc || t('plugins.noDescription'), PLUGIN_INTRO_DISPLAY_MAX)
          return (
            <article
              key={plugin.assetId}
              className="group relative flex min-h-[196px] w-full cursor-pointer flex-col overflow-hidden rounded-[16px] border border-[#ECEEF5] bg-white shadow-[0_1px_3px_rgba(15,23,42,0.018)] transition-all duration-200 hover:-translate-y-[2px] hover:border-[#CFCBFF] hover:shadow-[0_12px_24px_rgba(91,87,246,0.12)] xl:mx-0 xl:max-w-[452px]"
              onClick={() => handleViewPlugin(plugin)}
            >
              <div className="flex h-full min-h-[196px] flex-col rounded-[14px] bg-white px-[22px] py-5 sm:px-6 md:px-[26px] xl:px-7 2xl:px-[30px]">
              {plugin.pinOrder != null && (
                <Tooltip {...pluginCardTooltipProps} title={t('plugins.pinnedBadge')}>
                  <span aria-label={t('plugins.pinnedBadgeAria')} className="absolute right-2.5 top-2.5 inline-flex h-5 w-5 items-center justify-center rounded-md bg-amber-50 text-amber-600 ring-1 ring-amber-200/80">
                    <Pin className="h-[10px] w-[10px]" strokeWidth={2.2} />
                  </span>
                </Tooltip>
              )}
              <div className="relative flex items-start gap-3.5 pr-4 pt-0.5">
                <div className="shrink-0"><PluginAvatar iconUri={plugin.iconUri} displayName={plugin.displayName} /></div>
                <div className="min-w-0 flex-1 pt-[3px]">
                  <div className="flex min-w-0 items-center gap-2">
                    <h3 className="truncate text-[15px] font-semibold leading-[1.24] text-[#1B1B1F] sm:text-[16px]">{plugin.displayName}</h3>
                  </div>
                  <div className="mt-2.5 flex min-w-0 items-center justify-between gap-1.5">
                    <div className="flex min-w-0 flex-1 items-center gap-1.25 overflow-hidden pr-2">
                      {plugin.tags?.slice(0, 2).map((tag, i) => (
                        <span key={`${tag}-${i}`} className="truncate max-w-[84px] rounded-sm bg-[#F7F8FC] px-1.5 py-0.5 text-[11px] font-medium leading-none text-slate-500">
                          {tag}
                        </span>
                      ))}
                      {plugin.tags && plugin.tags.length > 2 && (
                        <Tooltip {...pluginCardTooltipProps} title={plugin.tags.slice(2).join(' · ')}>
                          <span className="shrink-0 rounded-sm bg-[#F7F8FC] px-1.5 py-0.5 text-[11px] font-medium leading-none text-slate-400">+{plugin.tags.length - 2}</span>
                        </Tooltip>
                      )}
                    </div>
                    {plugin.latestVersion && (
                      <span className="shrink-0 rounded-full bg-[#F4F7FF] px-1.5 py-[3px] text-[11px] font-medium leading-none text-[#5D6B85]">
                        {formatMarketSkillVersionLabel(plugin.latestVersion, plugin)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex min-h-0 flex-1 flex-col justify-between gap-4 pt-4 sm:gap-[18px] sm:pt-[18px]">
                <div className="flex min-h-0 flex-1 items-center">
                  <div className="relative w-full overflow-hidden px-0 py-0">
                    <p
                      className="text-[12px] leading-[1.6] text-slate-500 sm:text-[13px]"
                      style={{
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        wordBreak: 'break-word',
                        overflowWrap: 'anywhere',
                      }}
                      title={intro.truncated ? intro.full : undefined}
                    >
                      {intro.display}
                    </p>
                  </div>
                </div>
                <div className="relative flex items-end justify-between gap-2">
                  <div className="min-w-0">{renderStats(plugin, true)}</div>
                  <button
                    type="button"
                    disabled={downloadingAssetId === plugin.assetId}
                    onClick={e => {
                      e.stopPropagation()
                      const v = defaultDownloadVersion(plugin).trim()
                      void handleDownloadPlugin(plugin, v || undefined)
                    }}
                    className="mt-auto inline-flex h-auto min-w-0 shrink-0 items-center justify-center self-end px-0 py-0 text-[14px] sm:text-[15px] font-semibold leading-none text-transparent bg-[linear-gradient(90deg,#5B57F6_0%,#8B5CFF_100%)] bg-clip-text drop-shadow-[0_0_0_rgba(123,92,255,0)] transition-all duration-200 hover:scale-[1.05] hover:bg-[linear-gradient(90deg,#4F46E5_0%,#A855F7_100%)] hover:drop-shadow-[0_3px_8px_rgba(123,92,255,0.24)] disabled:pointer-events-none disabled:opacity-50"
                  >
                    {downloadingAssetId === plugin.assetId ? `${t('plugins.actions.download')}...` : t('plugins.actions.download')}
                  </button>
                </div>
              </div>
              </div>
            </article>
          )
        })}
      </div>
    )
  }, [defaultDownloadVersion, downloadingAssetId, handleDownloadPlugin, handleToggleInteract, handleViewPlugin, interactionStateMap, interactingKey, isOwnSkill, marketPlugins, searchKeyword, t])

  const listView = useMemo(() => {
    if (marketPlugins.length === 0) {
      return (
        <div className="rounded-[32px] border border-dashed border-slate-200 bg-white/80 px-6 py-16 shadow-[0_24px_80px_rgba(15,23,42,0.06)]">
          <Empty
            searchTerm={searchKeyword}
            type="plugins"
            customTitle={noMatchingSkillTitle}
            customDescription={t('plugins.noMatchingSkillDescription')}
          />
        </div>
      )
    }

    return (
      <div className="space-y-[10px]">
        {marketPlugins.map(plugin => {
          const intro = truncatePluginIntro(plugin.shortDesc || t('plugins.noDescription'), PLUGIN_INTRO_DISPLAY_MAX)
          return (
            <article
              key={plugin.assetId}
              className="group cursor-pointer rounded-[12px] border border-[#ECEEF5] bg-white px-4 py-3 shadow-[0_3px_10px_rgba(15,23,42,0.025)] transition-all duration-200 hover:-translate-y-[2px] hover:border-[#CFCBFF] hover:shadow-[0_14px_30px_rgba(91,87,246,0.14)]"
              onClick={() => handleViewPlugin(plugin)}
            >
              <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:gap-5">
                <div className="flex min-w-0 flex-1 items-start gap-4">
                  <PluginAvatar iconUri={plugin.iconUri} displayName={plugin.displayName} />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold leading-5 text-[#191919] sm:text-[16px]">{plugin.displayName}</h3>
                      {plugin.pinOrder != null && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200/80">
                          <Pin className="h-3 w-3" />
                          {t('plugins.pinnedBadge')}
                        </span>
                      )}
                      {plugin.latestVersion && (
                        <span className="rounded-full bg-[#F4F7FF] px-1.5 py-[3px] text-[11px] font-medium leading-none text-[#5D6B85]">
                          {formatMarketSkillVersionLabel(plugin.latestVersion, plugin)}
                        </span>
                      )}
                    </div>
                    <p className="mt-2.5 line-clamp-2 text-[12px] leading-[1.6] text-slate-500" title={intro.truncated ? intro.full : undefined}>
                      {intro.display}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      {plugin.tags?.slice(0, 4).map((tag, i) => (
                        <span key={`${tag}-${i}`} className="rounded-full bg-[#F5F7FB] px-2.5 py-0.5 text-[11px] font-medium text-slate-600">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex flex-col gap-2 xl:min-w-[258px] xl:self-stretch">
                  <div className="flex justify-end pt-0.5">{renderStats(plugin, true)}</div>
                  <button
                    type="button"
                    disabled={downloadingAssetId === plugin.assetId}
                    onClick={e => {
                      e.stopPropagation()
                      const v = defaultDownloadVersion(plugin).trim()
                      void handleDownloadPlugin(plugin, v || undefined)
                    }}
                    className="mt-auto inline-flex h-auto min-w-0 shrink-0 items-center justify-center self-end px-0 py-0 text-[14px] sm:text-[15px] font-semibold leading-none text-transparent bg-[linear-gradient(90deg,#5B57F6_0%,#8B5CFF_100%)] bg-clip-text drop-shadow-[0_0_0_rgba(123,92,255,0)] transition-all duration-200 hover:scale-[1.08] hover:bg-[linear-gradient(90deg,#4F46E5_0%,#A855F7_100%)] hover:drop-shadow-[0_4px_10px_rgba(123,92,255,0.32)] disabled:pointer-events-none disabled:opacity-50"
                  >
                    {downloadingAssetId === plugin.assetId ? `${t('plugins.actions.download')}...` : t('plugins.actions.download')}
                  </button>
                </div>
              </div>
            </article>
          )
        })}
      </div>
    )
  }, [defaultDownloadVersion, downloadingAssetId, handleDownloadPlugin, handleToggleInteract, handleViewPlugin, interactionStateMap, interactingKey, isOwnSkill, marketPlugins, searchKeyword, t])

  const sidebar = useMemo(
    () => (
      <aside className="hidden w-[clamp(220px,16vw,248px)] shrink-0 xl:block">
        <div className="mb-3 flex min-h-[42px] items-end sm:mb-4" />
        <nav aria-label={t('plugins.categoryNavAria')} className="rounded-[8px] bg-transparent px-0 pb-0 shadow-none">
          <div className="space-y-0.5">
            {CATEGORY_KEYS.map(key => {
              const isActive = activeCategory === key
              const count = categorySkillCount[key]
              const label = t(`plugins.category.${key}`)
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => handleSetCategory(key)}
                  aria-label={count != null ? `${label}, ${count.toLocaleString(locale)}` : label}
                  className={`flex h-10 w-full items-center justify-between rounded-[8px] border px-3 text-[12.5px] transition-colors ${
                    isActive
                      ? 'border-[#E8EBF2] bg-white text-[#2F3A4C] shadow-[0_1px_3px_rgba(15,23,42,0.03)]'
                      : 'border-transparent bg-transparent text-[#667085] hover:bg-white/75 hover:text-[#344054]'
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-[10px]">
                    <span className={`text-[13px] ${isActive ? 'text-[#4F46E5]' : 'text-[#98A2B3]'}`}>{CATEGORY_ICONS[key]}</span>
                    <span className={`truncate text-left ${isActive ? 'font-medium' : 'font-normal'}`}>{label}</span>
                  </span>
                  {count != null && <span className={`shrink-0 text-[12px] tabular-nums ${isActive ? 'text-[#98A2B3]' : 'text-[#B0B8C5]'}`}>{count.toLocaleString(locale)}</span>}
                </button>
              )
            })}
          </div>
        </nav>
      </aside>
    ),
    [activeCategory, categorySkillCount, handleSetCategory, locale, t],
  )

  const categoryMobileNav = useMemo(
    () => (
      <nav
        className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 xl:hidden"
        style={{ WebkitOverflowScrolling: 'touch' }}
        aria-label={t('plugins.categoryNavAria')}
      >
        {CATEGORY_KEYS.map(key => {
          const isActive = activeCategory === key
          const count = categorySkillCount[key]
          const label = t(`plugins.category.${key}`)
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleSetCategory(key)}
              aria-label={count != null ? `${label}, ${count.toLocaleString(locale)}` : label}
              className={`flex shrink-0 items-center gap-2 rounded-full border px-[14px] py-[6px] text-[14px] font-medium shadow-[0_2px_8px_rgba(15,23,42,0.03)] transition-colors ${
                isActive ? 'border-[#c8d9ff] bg-[#EEF3FF] text-[#1E54F9]' : 'border-slate-200 bg-white/90 text-slate-600'
              }`}
            >
              {CATEGORY_ICONS[key]}
              <span className="whitespace-nowrap">{label}</span>
              {count != null && <span className="shrink-0 text-[12px] tabular-nums opacity-70">{count.toLocaleString(locale)}</span>}
            </button>
          )
        })}
      </nav>
    ),
    [activeCategory, categorySkillCount, handleSetCategory, locale, t],
  )

  return (
    <div className="relative flex min-h-dvh flex-col overflow-x-hidden bg-[linear-gradient(180deg,#FCFCFE_0%,#F9F7FC_48%,#FBFBFD_100%)]">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[286px] bg-[radial-gradient(circle_at_top,#EDEFFF_0%,rgba(242,237,255,0.42)_26%,rgba(251,252,254,0)_70%)]" />

      <AppHeader onPublish={handlePublishClick} />

      <main className="relative z-10 flex-1 pb-2">
        <div className="mx-auto w-full max-w-[1646px] px-4 sm:px-5 lg:px-6 xl:px-6">
          <section className="pb-2 pt-[18px] sm:pb-[14px] sm:pt-[26px]">
            <div className="mx-auto max-w-[720px] text-center">
              <h1 className="mx-auto max-w-[700px] text-balance text-[24px] font-semibold leading-[1.18] tracking-[-0.032em] text-[#1B1B1F] sm:text-[28px] lg:text-[30px]">
                {t('plugins.marketTitle')} <span className="inline">{t('plugins.marketHeroSuffix')}</span>
              </h1>
              <p className="mx-auto mt-[6px] max-w-[680px] text-pretty text-[12px] leading-[1.6] text-slate-400 sm:text-[12px]">
                {t('plugins.marketSubtitleLead')}{' '}
                {typeof approvedSkillMarketTotal === 'number' && (
                  <>
                    <span className="font-semibold text-[#4F46E5]">{approvedSkillMarketTotal.toLocaleString(locale)}</span>{' '}
                    {t('plugins.marketSubtitleCountSuffix')}{' '}
                  </>
                )}
                {t('plugins.marketSubtitleTail', { typeLabel: marketTypeLabel })}
              </p>
            </div>

            <div className="mt-7 flex justify-center sm:mt-8">
              <div
                className="grid w-full max-w-[408px] grid-cols-2 gap-0 overflow-hidden rounded-[12px] bg-[linear-gradient(99.61deg,rgba(30,84,249,0.06)_0%,rgba(131,45,251,0.06)_100%)] p-0.5 shadow-none"
                role="tablist"
                aria-label="skill type tabs"
              >
                {([
                  {
                    value: 'swarmskill',
                    label: 'Swarm Skill',
                    icon: SwarmHubGlyph,
                    activeClasses: 'bg-white text-[#191919] shadow-[0_4px_16px_rgba(15,23,42,0.08)]',
                    inactiveClasses: 'bg-transparent text-[#191919] hover:bg-transparent',
                  },
                  {
                    value: 'skill',
                    label: 'Skill',
                    icon: SkillHubGlyph,
                    activeClasses: 'bg-white text-[#191919] shadow-[0_4px_16px_rgba(15,23,42,0.08)]',
                    inactiveClasses: 'bg-transparent text-[#191919] hover:bg-transparent',
                  },
                ] as const).map(option => {
                  const active = activeType === option.value
                  const Icon = option.icon
                  return (
                    <button
                      key={option.value}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => handleSetType(option.value)}
                      className={`flex h-[52px] items-center justify-center gap-2 rounded-[10px] px-4 text-center transition-colors duration-200 ${
                        active ? option.activeClasses : option.inactiveClasses
                      }`}
                    >
                      <Icon className="h-5 w-5 shrink-0 text-[#191919]" />
                      <span className="text-[14px] font-semibold leading-none text-[#191919]">
                        {option.label}
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="mt-4 h-px sm:mt-5" />

            <div className="mx-auto mt-3 flex max-w-[980px] justify-center sm:mt-4">
              <div className="relative w-full rounded-full border border-[#ECEEF5] bg-white px-5 py-[6px] shadow-[0_8px_22px_rgba(15,23,42,0.04)]">
                <div className="pointer-events-none absolute left-5 top-1/2 -translate-y-1/2 text-slate-300">
                  <Search className="h-4 w-4" />
                </div>
                <input
                  type="text"
                  placeholder={t('plugins.searchPlaceholder')}
                  value={searchInput}
                  onChange={e => {
                    const val = e.target.value
                    setSearchInput(val)
                    if (!val.trim()) {
                      setSearchKeyword('')
                      setCurrentPage(1)
                    }
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') handleSearch()
                  }}
                  className="h-[42px] w-full rounded-full bg-transparent pl-9 pr-24 text-[14px] text-[#191919] outline-none placeholder:text-slate-400 sm:h-11 sm:pr-[104px] sm:text-[14px]"
                />
                {(searchInput || searchKeyword) && (
                  <button
                    type="button"
                    onClick={() => {
                      setSearchInput('')
                      setSearchKeyword('')
                      setCurrentPage(1)
                    }}
                    className="absolute right-[52px] top-1/2 inline-flex h-[26px] w-[26px] -translate-y-1/2 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-600 sm:right-[56px]"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleSearch}
                  disabled={fetching}
                  className="absolute right-2.5 top-1/2 inline-flex h-[30px] w-[30px] -translate-y-1/2 items-center justify-center rounded-full bg-[#F8FAFC] text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:opacity-70 sm:h-8 sm:w-8"
                >
                  {fetching && searchKeyword ? <RefreshCw className="h-[14px] w-[14px] animate-spin" /> : <Search className="h-[14px] w-[14px]" />}
                </button>
              </div>
            </div>
          </section>

          {!noticeDismissed && (
            <div className="mb-[10px] flex items-start gap-2 rounded-[14px] border border-[#EEF1F6] bg-white/68 px-3 py-[6px] text-[12px] text-slate-500 shadow-[0_2px_6px_rgba(15,23,42,0.018)] sm:items-center">
              <span className="mt-0.5 inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-[#F2F5FF] text-[#4F46E5] sm:mt-0">
                <Info className="h-3 w-3" />
              </span>
              <span className="min-w-0 flex-1 leading-5">{t('plugins.modelAccessNotice')}</span>
              <button
                type="button"
                onClick={dismissModelAccessNotice}
                aria-label={t('common.buttons.close')}
                title={t('common.buttons.close')}
                className="inline-flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-700"
              >
                <X className="h-[14px] w-[14px]" />
              </button>
            </div>
          )}

          {error && (
            <div className="mb-6 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <section className="pb-2">
            {categoryMobileNav}
            <div className="mt-[6px] flex flex-col gap-3 xl:grid xl:grid-cols-[clamp(220px,16vw,248px)_minmax(0,1fr)] xl:items-start xl:gap-[18px]">
              {sidebar}
              <div className="min-w-0">
                <div className="mb-3 flex min-h-[42px] items-end justify-end gap-1.5 sm:mb-4">
                  <div className="flex shrink-0 items-center gap-1.5">
                    <ViewToggle value={viewMode} onChange={setViewMode} t={t} />
                    <button
                      type="button"
                      onClick={handleRefresh}
                      disabled={loading || fetching}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[#E7EAF2] bg-white text-slate-400 shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition-colors hover:border-[#DCE3EE] hover:text-slate-600 disabled:opacity-60"
                      title={t('plugins.actions.refresh')}
                      aria-label={t('plugins.actions.refresh')}
                    >
                      <RefreshCw className={`h-[14px] w-[14px] ${loading || fetching ? 'animate-spin' : ''}`} />
                    </button>
                  </div>
                </div>
                <div className="relative rounded-[20px] border border-transparent bg-white/10 p-0 shadow-none backdrop-blur-[1px]">
                  {fetching && marketPlugins.length > 0 ? (
                    viewMode === 'grid' ? <RefetchGridOverlay /> : <RefetchListOverlay />
                  ) : null}

                  {loading && marketPlugins.length === 0 ? (
                  viewMode === 'grid' ? (
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                      {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} />)}
                    </div>
                  )
                ) : viewMode === 'grid' ? (
                  gridView
                ) : (
                  listView
                )}
                </div>
              </div>
            </div>
          </section>

          {total > 0 && (
            <section className="pb-1 pt-0">
              <div className="flex flex-col gap-1.5 rounded-[15px] border border-[#EEF1F6] bg-white/64 px-4 py-2 shadow-[0_1px_3px_rgba(15,23,42,0.012)] sm:px-5 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-slate-500">
                  <span>{t('common.pagination.pageSize')}</span>
                  <select
                    value={pageSize}
                    onChange={e => {
                      setPageSize(Number(e.target.value))
                      setCurrentPage(1)
                    }}
                    className="h-8 rounded-full border border-[#E7EAF3] bg-white px-3 text-[13px] text-slate-600 outline-none"
                  >
                    {PAGE_SIZE_OPTIONS.map(opt => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                  <span>{t('common.pagination.items')}</span>
                  <span className="text-slate-300">•</span>
                  <span>{t('common.pagination.total', { total })}</span>
                </div>
                <div className="flex flex-wrap items-center gap-1 text-[12px] text-slate-600 lg:justify-end">
                  <button
                    type="button"
                    onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-[#E7EAF3] bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-50"
                    title={t('common.pagination.previous')}
                  >
                    <ChevronLeft className="h-[14px] w-[14px]" />
                  </button>
                  <div className="rounded-full border border-[#E7EAF3] bg-white px-3 py-[6px] text-[13px] font-medium text-slate-600">
                    {t('common.pagination.pagePrefix')} {page} {t('common.pagination.pageSuffix', { total: totalPages })}
                  </div>
                  <button
                    type="button"
                    onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-[#E7EAF3] bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-50"
                    title={t('common.pagination.next')}
                  >
                    <ChevronRight className="h-[14px] w-[14px]" />
                  </button>
                </div>
              </div>
            </section>
          )}
        </div>
      </main>



      <Dialog
        open={detailDialogOpen}
        onClose={() => setDetailDialogOpen(false)}
        maxWidth="md"
        fullWidth
        fullScreen={detailDialogFullScreen}
        slotProps={{
          paper: {
            sx: detailDialogFullScreen ? { borderRadius: 0, margin: 0, maxHeight: '100%' } : { borderRadius: 3 },
          },
        }}
      >
        {selectedPlugin && (
          <>
            <DialogTitle className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center space-x-3">
                <PluginAvatar iconUri={selectedPlugin.iconUri} displayName={selectedPlugin.displayName} />
                <div className="min-w-0">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <Typography variant="h6" className="min-w-0 truncate font-black text-[#111827]">
                      {selectedPlugin.displayName}
                    </Typography>
                    {selectedPlugin.latestVersion ? (
                      <span className="shrink-0 rounded-md border border-indigo-100/80 bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
                        {formatMarketSkillVersionLabel(selectedPlugin.latestVersion, selectedPlugin)}
                      </span>
                    ) : null}
                  </div>
                  <Typography variant="caption" color="text.secondary" className="mt-0.5 block truncate">
                    {t('plugins.detail.publisher')}: {selectedPlugin.publisherName || '-'}
                  </Typography>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Tooltip {...pluginDetailHeaderTooltipProps} title={t('plugins.detail.ratingTooltip')}>
                  <span className="inline-flex cursor-default items-center gap-1 rounded-full border border-amber-100 bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-700">
                    <BarChart3 className="h-[14px] w-[14px] text-amber-500" />
                    {selectedPlugin.averageRating}
                  </span>
                </Tooltip>
                <Tooltip {...pluginDetailHeaderTooltipProps} title={t('plugins.actions.favorite')}>
                  <IconButton size="small" onClick={handleFavoriteComingSoon} sx={{ color: '#64748b' }}>
                    <Star className="h-4 w-4" />
                  </IconButton>
                </Tooltip>
              </div>
            </DialogTitle>
            <DialogContent>
              <div className="space-y-5">
                <div>
                  <div className="flex min-w-0 items-center gap-2">
                    <div className="flex shrink-0 items-center gap-1.5">
                      <AlignLeft className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" component="span" className="font-bold text-gray-900">
                        {t('plugins.detail.summary')}:
                      </Typography>
                    </div>
                    <div className="flex min-w-0 flex-1 items-center">
                      {selectedPlugin.shortDesc ? (
                        <PluginMarkdown
                          source={selectedPlugin.shortDesc}
                          className="prose prose-sm prose-neutral min-w-0 max-w-none flex-1 text-gray-900 prose-p:my-0 prose-headings:scroll-mt-2 [&_p]:text-[0.9375rem] [&_p]:leading-snug"
                        />
                      ) : (
                        <Typography variant="body2">{t('plugins.noDescription')}</Typography>
                      )}
                    </div>
                  </div>
                </div>
                <div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <div className="min-h-[108px] rounded-lg border border-[#DCEEFE] bg-[#F3FAFF] px-3 py-3">
                      <div className="flex flex-col items-center text-center">
                        <Eye className="mb-2 h-4 w-4 text-sky-600" />
                        <div className="text-lg font-extrabold leading-7 tabular-nums text-sky-700">{selectedPlugin.viewCount}</div>
                        <div className="mt-2 text-[11px] text-sky-600">{t('plugins.detail.viewCount')}</div>
                      </div>
                    </div>
                    <div className="min-h-[108px] rounded-lg border border-[#E0E7FF] bg-[#F4F6FF] px-3 py-3">
                      <div className="flex flex-col items-center text-center">
                        <Download className="mb-2 h-4 w-4 text-indigo-600" />
                        <div className="text-lg font-extrabold leading-7 tabular-nums text-indigo-700">{selectedPlugin.installCount}</div>
                        <div className="mt-2 text-[11px] text-indigo-600">{t('plugins.detail.installCount')}</div>
                      </div>
                    </div>
                    <div className="min-h-[108px] rounded-lg border border-[#FFE2EA] bg-[#FFF4F7] px-3 py-3">
                      <div className="flex flex-col items-center text-center">
                        <Heart className="mb-2 h-4 w-4 text-rose-600" />
                        <div className="text-lg font-extrabold leading-7 tabular-nums text-rose-700">
                          {interactionStateMap[selectedPlugin.assetId]?.like_count ?? selectedPlugin.likeCount}
                        </div>
                        <div className="mt-2 text-[11px] text-rose-600">{t('plugins.detail.likeCount')}</div>
                      </div>
                    </div>
                    <div className="min-h-[108px] rounded-lg border border-[#D8F2F5] bg-[#F2FBFC] px-3 py-3">
                      <div className="flex flex-col items-center text-center">
                        <MessageCircle className="mb-2 h-4 w-4 text-cyan-600" />
                        <div className="text-lg font-extrabold leading-7 tabular-nums text-cyan-700">{selectedPlugin.reviewCount}</div>
                        <div className="mt-2 text-[11px] text-cyan-600">{t('plugins.detail.reviewCount')}</div>
                      </div>
                    </div>
                  </div>
                </div>
                {(selectedPlugin.detailDesc || '').trim().length > 0 && (
                  <div>
                    <div className="mb-2 flex items-center gap-1.5">
                      <BookOpen className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.description')}
                      </Typography>
                    </div>
                    <PluginMarkdown
                      source={selectedPlugin.detailDesc}
                      className="prose prose-sm prose-neutral h-64 max-w-none overflow-y-auto rounded-lg border border-blue-100 bg-blue-50 p-4 shadow-sm prose-headings:scroll-mt-2 prose-pre:bg-gray-900 prose-pre:text-gray-100"
                    />
                  </div>
                )}
                {effectiveDetailVersion ? (
                  <div className="rounded-lg border border-slate-200/90 bg-slate-50/80 p-4">
                    <div className="mb-2 flex items-center gap-1.5">
                      <ScrollText className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.versionChangelog', {
                          version: formatMarketSkillVersionLabel(effectiveDetailVersion, selectedPlugin),
                        })}
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
                        className="prose prose-sm prose-neutral max-h-48 max-w-none overflow-y-auto text-gray-900 prose-p:my-1 prose-headings:my-2 prose-headings:scroll-mt-2 [&_p]:text-[0.9375rem]"
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
                          {formatMarketSkillVersionLabel(v, selectedPlugin)}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                ) : null}
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="flex items-center gap-1.5">
                      <Cpu className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.runtime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">{selectedPlugin.runTime}</Typography>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <Tag className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.tags')}
                      </Typography>
                    </div>
                    <div className="mt-1 min-h-[22px]">
                      {selectedPlugin.tags?.length ? <DetailPluginTags tags={selectedPlugin.tags} /> : <Typography variant="body2" color="text.secondary">-</Typography>}
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <CalendarPlus className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.createTime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">{formatPluginDateTime(selectedPlugin.createTime, i18n.language)}</Typography>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <CalendarClock className="h-4 w-4 shrink-0 text-slate-600" />
                      <Typography variant="subtitle1" className="font-bold text-gray-900">
                        {t('plugins.detail.updateTime')}
                      </Typography>
                    </div>
                    <Typography variant="body2" className="mt-0.5">{formatPluginDateTime(selectedPlugin.updateTime, i18n.language)}</Typography>
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
