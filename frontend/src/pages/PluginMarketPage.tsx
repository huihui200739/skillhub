import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  BarChart3,
  Bookmark,
  CalendarClock,
  CalendarPlus,
  Cpu,
  Download,
  Eye,
  Heart,
  MessageCircle,
  RefreshCw,
  ScrollText,
  Tag,
  Flame,
  LayoutGrid,
  Search,
  X,
  BookOpen,
  AlignLeft,
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
import { pluginCardTooltipProps, pluginDetailHeaderTooltipProps } from '@/components/Common/pluginCardTooltip'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { AppHeader } from '@/components/Common/AppHeader'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { Empty } from '@/components/Common/Empty'
import axios from 'axios'
import { pinyin } from 'pinyin-pro'
import { getPluginArtifactDownload, getPluginVersionDetail } from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { usePluginMarketConfigs, type MarketPlugin } from '@/hooks/usePluginMarketConfigs'

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

type CategoryKey = 'hot' | 'all' | 'software-development' | 'office-productivity' | 'content-creation' | 'multimodal-media' | 'data-science-research' | 'compliance-legal' | 'lifestyle-health' | 'finance-wealth'

const CATEGORY_KEYS: CategoryKey[] = ['hot', 'all', 'software-development', 'office-productivity', 'content-creation', 'multimodal-media', 'data-science-research', 'compliance-legal', 'lifestyle-health', 'finance-wealth']

const CATEGORY_ICONS: Record<CategoryKey, React.ReactNode> = {
  hot: <Flame className="w-5 h-5" />,
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

export default function PluginMarketPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { isAuthenticated } = useGitCodeAuth()
  const [searchInput, setSearchInput] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [activeCategory, setActiveCategory] = useState<CategoryKey>('all')
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

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearchKeyword(searchInput.trim())
      setCurrentPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    setCurrentPage(1)
  }, [activeCategory])

  const isHotCategory = activeCategory === 'hot'
  const activeCategoryId = activeCategory === 'hot' || activeCategory === 'all' ? undefined : activeCategory

  const { marketPlugins, total, page, pageSize: serverPageSize, loading, error, refreshMarketPlugins } =
    usePluginMarketConfigs({
      page: currentPage,
      pageSize,
      searchKeyword,
      catalogKind: 'skill',
      categoryId: activeCategoryId,
      orderBy: isHotCategory ? 'install_count' : undefined,
      desc: isHotCategory ? true : undefined,
    })

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

  useEffect(() => {
    if (!detailDialogOpen || !selectedPlugin || !effectiveDetailVersion) {
      setDetailChangelog(null)
      setDetailChangelogLoading(false)
      setDetailChangelogError(null)
      return
    }
    const ac = new AbortController()
    setDetailChangelogLoading(true)
    setDetailChangelogError(null)
    setDetailChangelog(null)
    void getPluginVersionDetail(selectedPlugin.assetId, effectiveDetailVersion, { signal: ac.signal })
      .then(data => {
        const raw = data.changelog?.trim()
        setDetailChangelog(raw && raw.length > 0 ? raw : null)
        setDetailChangelogLoading(false)
      })
      .catch((err: unknown) => {
        if (isCanceledRequest(err)) {
          return
        }
        setDetailChangelogError(err instanceof Error ? err.message : t('plugins.detail.changelogLoadFailed'))
        setDetailChangelogLoading(false)
      })
    return () => ac.abort()
  }, [detailDialogOpen, selectedPlugin, effectiveDetailVersion, t])

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

          return (
            <div
              key={plugin.assetId}
              className="group relative rounded-2xl border border-[#e6e6e6] bg-white/95 backdrop-blur-sm transition-all duration-300 hover:shadow-[0_4px_40px_rgba(0,0,0,0.1)] hover:border-[#d0d0d0] cursor-pointer"
              onClick={() => handleViewPlugin(plugin)}
            >
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
                    <span className="inline-flex items-center gap-1">
                      <Download className="w-3.5 h-3.5" />
                      {plugin.installCount}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={downloadingAssetId === plugin.assetId}
                    onClick={e => {
                      e.stopPropagation()
                      void handleDownloadPlugin(plugin)
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
  }, [marketPlugins, searchKeyword, t, handleDownloadPlugin, downloadingAssetId])

  const sidebar = useMemo(() => (
    <aside className="w-[248px] shrink-0">
      <nav className="flex flex-col gap-1">
        {CATEGORY_KEYS.map(key => {
          const isActive = activeCategory === key
          const isHot = key === 'hot'
          return (
            <button
              key={key}
              onClick={() => setActiveCategory(key)}
              className={`flex items-center gap-2.5 w-full h-9 px-3 rounded-lg text-sm transition-colors ${
                isHot
                  ? isActive
                    ? 'bg-gradient-to-r from-[#FFF7ED] to-[#FFFBF1] text-[#191919] font-medium'
                    : 'hover:bg-orange-50/50 text-[#191919]'
                  : isActive
                    ? 'bg-[#f0f5ff] text-[#191919] font-medium'
                    : 'hover:bg-gray-50 text-[#191919]'
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
  ), [activeCategory, t])

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
      <AppHeader onPublish={handlePublishClick} />

      <div className="flex-1 min-h-0 overflow-auto">
        <div className="mx-auto max-w-[1600px] px-8">
          <div className="py-6 text-center">
            <h1 className="text-[#191919] text-[28px] md:text-[40px] font-semibold leading-tight tracking-tight">
              {t('plugins.marketTitle')} {t('plugins.marketHeroSuffix')}
            </h1>
            <p className="mt-2 text-[#595959] text-sm md:text-base max-w-[600px] mx-auto leading-relaxed">
              {t('plugins.marketSubtitle')}
            </p>
          </div>

          <div className="flex items-center justify-center mb-6">
            <div className="relative w-full max-w-[1000px]">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-[#aeaeae]" />
              <input
                type="text"
                placeholder={t('plugins.searchPlaceholder')}
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                className="w-full h-14 pl-12 pr-10 rounded-full border border-transparent bg-white text-base text-[#191919] placeholder-[#aeaeae] shadow-[0_4px_45px_rgba(0,0,0,0.1)] transition-all hover:shadow-[0_4px_50px_rgba(0,0,0,0.12)] focus:outline-none focus:shadow-[0_4px_50px_rgba(0,0,0,0.15)]"
              />
              {searchInput && (
                <button
                  onClick={() => setSearchInput('')}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-[#aeaeae] hover:text-[#777777] transition-colors"
                  type="button"
                >
                  <X className="w-5 h-5" />
                </button>
              )}
            </div>
          </div>

          <div className="flex items-center justify-between mb-6">
            <div />
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="h-9 px-4 bg-white border border-[#e6e6e6] text-[#191919] rounded-lg text-sm font-medium hover:bg-[#f9f9f9] hover:border-[#d0d0d0] transition-colors flex items-center gap-2"
            >
              {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              <span>{t('plugins.actions.refresh')}</span>
            </button>
          </div>

          {error && (
            <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3">
              <div className="flex items-center">
                <span className="text-red-800 text-sm">{error}</span>
              </div>
            </div>
          )}

          <div className="flex gap-5 pb-4">
            {sidebar}
            <div className="flex-1 min-w-0">
              {loading ? (
                <div className="flex items-center justify-center py-16">
                  <CircularProgress />
                </div>
              ) : (
                gridView
              )}
            </div>
          </div>
        </div>
      </div>

      {total > 0 && (
        <div className="shrink-0 border-t border-[#e5e7eb] bg-gradient-to-r from-[#E3F2FD] to-[#F3E9FF] px-8 py-3">
          <div className="mx-auto max-w-[1600px] flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm text-gray-700">
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
            <div className="flex items-center gap-2 text-sm text-gray-700">
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
        slotProps={{ paper: { sx: { borderRadius: 3 } } }}
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
                    <Bookmark className="w-4 h-4" />
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
                        {selectedPlugin.likeCount}
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
                onClick={() =>
                  void handleDownloadPlugin(
                    selectedPlugin,
                    selectedPlugin.allVersions.length > 1
                      ? detailDownloadVersion || defaultDownloadVersion(selectedPlugin)
                      : undefined,
                  )
                }
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
