import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from 'react-query'
import { ArrowLeft, Download } from 'lucide-react'
import { CircularProgress } from '@mui/material'
import axios from 'axios'
import { AppHeader } from '@/components/Common/AppHeader'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import {
  getPluginArtifactDownload,
  getPluginVersionDetail,
  getPlugins,
  postSkillModeration,
  type MarketplacePluginItem,
} from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'

function isCanceledRequest(err: unknown): boolean {
  if (axios.isCancel(err)) return true
  if (!axios.isAxiosError(err)) return false
  return err.code === 'ERR_CANCELED' || err.name === 'CanceledError'
}

function formatDate(ts: number | null | undefined, lang: string): string {
  if (!ts) return '-'
  const ms = ts > 1_000_000_000_000 ? ts : ts * 1000
  return new Date(ms).toLocaleString(lang.startsWith('zh') ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function firstString(...candidates: Array<string | null | undefined>): string {
  for (const item of candidates) {
    if (item && item.trim()) return item.trim()
  }
  return ''
}

function normalizeTagList(tags: string[] | null | undefined): string[] {
  if (!Array.isArray(tags)) return []
  const out = tags.map(x => String(x).trim()).filter(Boolean)
  return [...new Set(out)]
}

function isIconUrl(icon: string | undefined): boolean {
  if (typeof icon !== 'string' || !icon.trim()) return false
  const t = icon.trim()
  if (t.startsWith('http://') || t.startsWith('https://')) return true
  if (t.startsWith('/') && !t.startsWith('//')) return true
  return t.includes('.')
}

function normalizeModerationStatus(raw: string | null | undefined): 'PENDING' | 'APPROVED' | 'REJECTED' {
  const u = (raw || 'APPROVED').toString().toUpperCase()
  if (u === 'PENDING' || u === 'REJECTED') return u
  return 'APPROVED'
}

function mapSkill(raw: MarketplacePluginItem) {
  return {
    assetId: raw.asset_id,
    displayName: firstString(raw.display_name, raw.displayName, raw.name),
    shortDesc: firstString(raw.short_desc, raw.shortDesc),
    detailDesc: firstString(raw.detail_desc, raw.detailDesc),
    iconUri: firstString(raw.icon_uri),
    publisherName: firstString(raw.publisher_name),
    latestVersion: firstString(raw.latest_version),
    tags: normalizeTagList(raw.tags ?? undefined),
    allVersions: Array.isArray(raw.all_versions) ? raw.all_versions : [],
    installCount: raw.install_count ?? 0,
    updateTime: raw.update_time ?? raw.updateTime ?? null,
    moderationStatus: normalizeModerationStatus(raw.moderation_status),
    moderationRejectReason: firstString(raw.moderation_reject_reason),
  }
}

function defaultVersionForSkill(skill: ReturnType<typeof mapSkill>): string {
  const versions = skill.allVersions
  const latest = skill.latestVersion.trim()
  if (latest && versions.includes(latest)) return latest
  if (versions.length) return versions[versions.length - 1]
  return latest
}

async function triggerDownload(url: string, fileName: string): Promise<void> {
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.target = '_blank'
  link.rel = 'noopener noreferrer'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function SkillHeaderIcon({ displayName, iconUri }: { displayName: string; iconUri: string }) {
  const resolved = resolvePluginIconUrl(iconUri)
  const [imgFailed, setImgFailed] = useState(false)
  const letter = (displayName || 'S').slice(0, 1).toUpperCase()
  const showImg = Boolean(resolved && isIconUrl(resolved) && !imgFailed)
  if (showImg) {
    return (
      <div className="flex h-[clamp(3rem,8vw,3.75rem)] w-[clamp(3rem,8vw,3.75rem)] shrink-0 items-center justify-center overflow-hidden rounded-xl bg-sky-100 shadow-sm ring-1 ring-sky-200/60">
        <img src={resolved} alt="" className="h-full w-full object-cover" onError={() => setImgFailed(true)} />
      </div>
    )
  }
  return (
    <div
      className="flex h-[clamp(3rem,8vw,3.75rem)] w-[clamp(3rem,8vw,3.75rem)] shrink-0 items-center justify-center rounded-xl bg-sky-400 text-[clamp(1.125rem,3.5vw,1.5rem)] font-semibold text-white shadow-md ring-1 ring-sky-500/30"
      aria-hidden
    >
      {letter || '?'}
    </div>
  )
}

export default function SkillDetailPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { assetId: encodedAssetId = '' } = useParams<{ assetId: string }>()
  const assetId = decodeURIComponent(encodedAssetId)
  const { isAuthenticated, isMarketModerationAdmin } = useGitCodeAuth()
  const queryClient = useQueryClient()
  /** 忽略「审核前」发出的旧版本详情响应，避免覆盖刚审核后的状态 */
  const versionDetailFetchGen = useRef(0)
  const { openPublish } = usePublishDrawer()
  const [selectedVersion, setSelectedVersion] = useState('')
  const [downloadLoading, setDownloadLoading] = useState(false)
  const [changelog, setChangelog] = useState<string | null>(null)
  const [changelogLoading, setChangelogLoading] = useState(false)
  const [changelogError, setChangelogError] = useState<string | null>(null)
  /** 来自版本详情接口的 install_count；未拉到前用列表里的 installCount */
  const [installCountFromVersionApi, setInstallCountFromVersionApi] = useState<number | null>(null)
  /** null：未拉到版本详情，用列表 tags；非 null：以版本详情为准（可为 []） */
  const [tagsFromVersionApi, setTagsFromVersionApi] = useState<string[] | null>(null)
  const [updateTimeFromVersionApi, setUpdateTimeFromVersionApi] = useState<number | null>(null)
  const [moderationStatus, setModerationStatus] = useState<'PENDING' | 'APPROVED' | 'REJECTED'>('APPROVED')
  const [moderationRejectReason, setModerationRejectReason] = useState<string>('')
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false)
  const [rejectDraft, setRejectDraft] = useState('')
  const [moderationBusy, setModerationBusy] = useState(false)
  /** null：尚未从版本详情接口拿到 viewer 标记；否则以服务端为准 */
  const [versionDetailViewerModerator, setVersionDetailViewerModerator] = useState<boolean | null>(null)
  const downloadRef = useRef(false)

  const detailQuery = useQuery(
    ['skill-detail-raw', assetId],
    async () => {
      const response = await getPlugins({ page: 1, page_size: 1, asset_id: assetId, plugin_type: 'skill' })
      return response.data.items[0] ?? null
    },
    { enabled: Boolean(assetId), retry: 1 },
  )

  const skill = useMemo(() => (detailQuery.data ? mapSkill(detailQuery.data) : null), [detailQuery.data])
  const versionList = skill?.allVersions?.length ? [...skill.allVersions].reverse() : []

  useEffect(() => {
    if (!skill) return
    setInstallCountFromVersionApi(null)
    setTagsFromVersionApi(null)
    setUpdateTimeFromVersionApi(null)
    setVersionDetailViewerModerator(null)
    setModerationStatus(skill.moderationStatus)
    setModerationRejectReason(skill.moderationRejectReason)
    setSelectedVersion(prev => {
      if (prev && skill.allVersions.includes(prev)) return prev
      return defaultVersionForSkill(skill)
    })
  }, [skill])

  useEffect(() => {
    if (!skill || !selectedVersion) {
      setChangelog(null)
      setChangelogLoading(false)
      setChangelogError(null)
      return
    }
    const ac = new AbortController()
    const gen = ++versionDetailFetchGen.current
    setChangelogLoading(true)
    setChangelogError(null)
    setChangelog(null)
    void getPluginVersionDetail(skill.assetId, selectedVersion, { signal: ac.signal })
      .then(res => {
        if (ac.signal.aborted) return
        if (gen !== versionDetailFetchGen.current) return
        const text = res.changelog?.trim()
        setChangelog(text || null)
        setInstallCountFromVersionApi(res.install_count ?? 0)
        setTagsFromVersionApi(normalizeTagList(res.tags ?? undefined))
        setUpdateTimeFromVersionApi(
          res.update_time != null && Number.isFinite(Number(res.update_time)) ? Number(res.update_time) : null,
        )
        setVersionDetailViewerModerator(res.viewer_is_market_moderation_admin === true)
        if (res.moderation_status != null && String(res.moderation_status).trim()) {
          setModerationStatus(normalizeModerationStatus(res.moderation_status))
          setModerationRejectReason(firstString(res.moderation_reject_reason))
        }
        setChangelogLoading(false)
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return
        if (gen !== versionDetailFetchGen.current) return
        if (isCanceledRequest(err)) return
        setChangelogError(err instanceof Error ? err.message : t('plugins.detail.changelogLoadFailed'))
        setChangelogLoading(false)
      })
    return () => ac.abort()
  }, [selectedVersion, skill, t])

  const displayInstallCount = installCountFromVersionApi ?? skill?.installCount ?? 0
  const displayTags = tagsFromVersionApi !== null ? tagsFromVersionApi : skill?.tags ?? []
  const displayUpdateTime = updateTimeFromVersionApi ?? skill?.updateTime ?? null

  const handlePublish = useCallback(() => {
    if (isAuthenticated) {
      openPublish()
      return
    }
    setPostLoginRedirect('/profile/publish?kind=skill')
    navigate('/login')
  }, [isAuthenticated, navigate, openPublish])

  const canShowModerationPanel = useMemo(() => {
    if (versionDetailViewerModerator !== null) return versionDetailViewerModerator
    if (detailQuery.data?.viewer_is_market_moderation_admin === true) return true
    return isMarketModerationAdmin
  }, [
    detailQuery.data?.viewer_is_market_moderation_admin,
    isMarketModerationAdmin,
    versionDetailViewerModerator,
  ])

  const moderationLabel = useMemo(() => {
    if (moderationStatus === 'PENDING') return t('plugins.skillPage.moderationPending')
    if (moderationStatus === 'REJECTED') return t('plugins.skillPage.moderationRejected')
    return t('plugins.skillPage.moderationApproved')
  }, [moderationStatus, t])

  const runApprove = useCallback(async () => {
    if (!skill || moderationBusy) return
    setModerationBusy(true)
    try {
      await postSkillModeration(skill.assetId, { action: 'approve' })
      versionDetailFetchGen.current += 1
      setModerationStatus('APPROVED')
      setModerationRejectReason('')
      void queryClient.invalidateQueries(['skill-detail-raw', assetId])
      void queryClient.invalidateQueries({ queryKey: ['admin-pending-skills'] })
      window.alert(t('plugins.skillPage.moderationSuccess'))
    } catch {
      window.alert(t('plugins.skillPage.moderationFailed'))
    } finally {
      setModerationBusy(false)
    }
  }, [assetId, moderationBusy, queryClient, skill, t])

  const submitReject = useCallback(async () => {
    if (!skill || moderationBusy) return
    const reason = rejectDraft.trim()
    if (!reason) {
      window.alert(t('plugins.skillPage.rejectReasonPlaceholder'))
      return
    }
    setModerationBusy(true)
    try {
      await postSkillModeration(skill.assetId, { action: 'reject', reason })
      versionDetailFetchGen.current += 1
      setModerationStatus('REJECTED')
      setModerationRejectReason(reason)
      setRejectDialogOpen(false)
      setRejectDraft('')
      void queryClient.invalidateQueries(['skill-detail-raw', assetId])
      void queryClient.invalidateQueries({ queryKey: ['admin-pending-skills'] })
      window.alert(t('plugins.skillPage.moderationSuccess'))
    } catch {
      window.alert(t('plugins.skillPage.moderationFailed'))
    } finally {
      setModerationBusy(false)
    }
  }, [assetId, moderationBusy, queryClient, rejectDraft, skill, t])

  const handleDownload = useCallback(async () => {
    if (!skill || downloadRef.current) return
    const version = selectedVersion.trim() || defaultVersionForSkill(skill)
    if (!version) {
      window.alert(t('plugins.actions.downloadFailed'))
      return
    }
    downloadRef.current = true
    setDownloadLoading(true)
    try {
      const data = await getPluginArtifactDownload(skill.assetId, version)
      const base = data.name.trim() || skill.displayName || skill.assetId
      await triggerDownload(data.download_url, `${base.replace(/\s+/g, '-')}_${data.version}.zip`)
    } catch {
      window.alert(t('plugins.actions.downloadFailed'))
    } finally {
      downloadRef.current = false
      setDownloadLoading(false)
    }
  }, [selectedVersion, skill, t])

  /** 与 `AppHeader` 内层一致：左与 logo 对齐，右与登录/账号区对齐 */
  const pageAlignWithHeader = 'px-4 md:px-[8.33%]'
  /** 白卡片内边距：与边框留出距离，正文/图片不贴边 */
  const cardInnerPad = 'px-5 py-4 sm:px-6 sm:py-5 md:px-8 md:py-6'

  return (
    <div className="flex min-h-dvh flex-col overflow-hidden bg-white">
      <AppHeader onPublish={handlePublish} />
      <div className="min-h-0 flex-1 overflow-y-auto bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
        <div className={`w-full ${pageAlignWithHeader} py-2 pb-6 sm:py-3 sm:pb-8`}>
          <Breadcrumbs
            className="mb-2 text-left sm:mb-3"
            items={[
              { label: t('common.breadcrumb.home'), to: '/' },
              {
                label: skill?.displayName?.trim()
                  ? skill.displayName
                  : t('common.breadcrumb.skillDetail'),
              },
            ]}
          />

          {detailQuery.isLoading ? (
            <div className="flex min-h-[12rem] items-center justify-center">
              <CircularProgress size={28} />
            </div>
          ) : null}

          {!detailQuery.isLoading && !skill ? (
            <div className="w-full rounded-lg border border-rose-200 bg-rose-50/95 px-5 py-3 text-left text-sm text-rose-700 sm:rounded-xl sm:px-6 md:px-8">
              {t('profile.noDetail')}
            </div>
          ) : null}

          {skill ? (
            <article className="w-full overflow-hidden rounded-lg border border-slate-200/80 bg-white shadow-sm sm:rounded-xl">
              <header className={`border-b border-slate-100 ${cardInnerPad}`}>
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-[clamp(0.75rem,2vw,1rem)]">
                    <SkillHeaderIcon displayName={skill.displayName} iconUri={skill.iconUri} />
                    <div className="min-w-0 text-left">
                      <h1 className="text-[length:clamp(1.125rem,2.8vw,1.75rem)] font-bold leading-tight text-slate-900">
                        {skill.displayName}
                      </h1>
                      {displayTags.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {displayTags.map(tag => (
                            <span
                              key={tag}
                              className="inline-flex items-center rounded-md border border-slate-200/90 bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-slate-700 shadow-sm"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center justify-start gap-4 sm:justify-end">
                    <span
                      className="inline-flex items-center gap-1 text-[length:clamp(0.75rem,1.3vw,0.8125rem)] tabular-nums text-slate-500"
                      title={t('plugins.detail.installCount')}
                    >
                      <Download className="h-4 w-4 shrink-0 text-indigo-500" aria-hidden />
                      {displayInstallCount}
                    </span>
                    {moderationStatus !== 'APPROVED' ? (
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-medium ${
                          moderationStatus === 'PENDING'
                            ? 'bg-amber-50 text-amber-800 ring-1 ring-amber-200'
                            : 'bg-rose-50 text-rose-800 ring-1 ring-rose-200'
                        }`}
                      >
                        {moderationLabel}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void handleDownload()}
                      disabled={downloadLoading}
                      className="inline-flex h-10 items-center gap-2 rounded-full bg-gradient-to-r from-violet-600 to-indigo-600 px-5 text-sm font-medium text-white shadow-md shadow-indigo-300/35 transition hover:from-violet-500 hover:to-indigo-500 disabled:opacity-60"
                    >
                      <Download className="h-4 w-4 shrink-0" aria-hidden />
                      {t('plugins.actions.download')}
                    </button>
                  </div>
                </div>
              </header>

              <div className={`space-y-8 text-left ${cardInnerPad}`}>
                {canShowModerationPanel ? (
                  <section className="rounded-lg border border-indigo-100 bg-indigo-50/60 px-4 py-3 sm:px-5">
                    <h2 className="text-sm font-semibold text-indigo-900">{t('plugins.skillPage.moderationHeading')}</h2>
                    <p className="mt-1 text-xs text-indigo-800/90">
                      {moderationLabel}
                      {moderationStatus === 'REJECTED' && moderationRejectReason
                        ? ` — ${moderationRejectReason}`
                        : ''}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={moderationBusy || moderationStatus === 'APPROVED'}
                        onClick={() => void runApprove()}
                        className="rounded-full bg-emerald-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-emerald-500 disabled:opacity-50"
                      >
                        {t('plugins.skillPage.approve')}
                      </button>
                      <button
                        type="button"
                        disabled={moderationBusy || moderationStatus === 'APPROVED'}
                        onClick={() => {
                          setRejectDraft('')
                          setRejectDialogOpen(true)
                        }}
                        className="rounded-full bg-rose-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-rose-500 disabled:opacity-50"
                      >
                        {t('plugins.skillPage.reject')}
                      </button>
                    </div>
                  </section>
                ) : null}
                {moderationStatus === 'REJECTED' && moderationRejectReason && !canShowModerationPanel ? (
                  <section className="rounded-lg border border-rose-100 bg-rose-50/80 px-4 py-3 text-sm text-rose-900 sm:px-5">
                    <span className="font-semibold">{t('plugins.skillPage.rejectReasonLabel')}:</span>
                    {moderationRejectReason}
                  </section>
                ) : null}
                <section>
                  <h2 className="mb-4 text-base font-semibold text-slate-900">{t('plugins.skillPage.basicInfo')}</h2>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <div>
                      <div className="text-xs text-slate-400">{t('plugins.skillPage.fieldName')}</div>
                      <div className="mt-1 text-sm font-medium text-slate-800">{skill.displayName}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">{t('plugins.skillPage.fieldPublisher')}</div>
                      <div className="mt-1 text-sm text-slate-800">{skill.publisherName || '—'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">{t('plugins.skillPage.fieldUpdatedAt')}</div>
                      <div className="mt-1 text-sm text-slate-800">{formatDate(displayUpdateTime, i18n.language)}</div>
                    </div>
                  </div>

                  <h3 className="mb-2 mt-6 text-sm font-semibold text-slate-800">{t('plugins.skillPage.descriptionHeading')}</h3>
                  <p className="text-sm leading-relaxed text-slate-600">{skill.shortDesc || '—'}</p>
                </section>

                <section className="border-t border-slate-100 pt-8">
                  <h2 className="mb-3 text-base font-semibold text-slate-900">{t('plugins.skillPage.versionChangelog')}</h2>
                  <select
                    value={selectedVersion}
                    onChange={e => setSelectedVersion(e.target.value)}
                    className="mb-3 h-10 w-full max-w-xs rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-200 sm:max-w-sm"
                  >
                    {(versionList.length ? versionList : [skill.latestVersion || '']).map(v => (
                      <option key={v || 'empty'} value={v}>
                        {v ? `v${v}` : '-'}
                      </option>
                    ))}
                  </select>
                  <div className="rounded-lg border border-slate-100 bg-slate-50/80 px-4 py-3 text-sm text-slate-600 sm:px-5">
                    {changelogLoading ? (
                      <span>{t('plugins.detail.changelogLoading')}</span>
                    ) : changelogError ? (
                      <span className="text-rose-600">{changelogError}</span>
                    ) : changelog ? (
                      <PluginMarkdown source={changelog} className="prose prose-sm max-w-none text-slate-700" />
                    ) : (
                      <span>{t('plugins.detail.changelogEmpty')}</span>
                    )}
                  </div>
                </section>

                <section className="border-t border-slate-100 pt-8">
                  <h2 className="mb-4 text-base font-semibold text-slate-900">{t('plugins.skillPage.detailHeading')}</h2>
                  {skill.detailDesc ? (
                    <div className="prose prose-slate max-w-none text-sm prose-headings:font-semibold prose-a:text-indigo-600 prose-pre:bg-slate-100 prose-pre:text-slate-800 prose-table:text-sm prose-img:rounded-lg sm:text-base">
                      <PluginMarkdown source={skill.detailDesc} className="leading-relaxed text-slate-700" />
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">{t('plugins.noDescription')}</p>
                  )}
                </section>
              </div>
            </article>
          ) : null}
        </div>
      </div>
      {rejectDialogOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
            <h3 className="text-base font-semibold text-slate-900">{t('plugins.skillPage.rejectDialogTitle')}</h3>
            <textarea
              value={rejectDraft}
              onChange={e => setRejectDraft(e.target.value)}
              rows={4}
              className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-200"
              placeholder={t('plugins.skillPage.rejectReasonPlaceholder')}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-full border border-slate-200 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => {
                  setRejectDialogOpen(false)
                  setRejectDraft('')
                }}
                disabled={moderationBusy}
              >
                {t('plugins.skillPage.cancel')}
              </button>
              <button
                type="button"
                className="rounded-full bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-50"
                onClick={() => void submitReject()}
                disabled={moderationBusy}
              >
                {t('plugins.skillPage.submitModeration')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
