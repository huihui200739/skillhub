import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Download, Trash2, UploadCloud } from 'lucide-react'
import {
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  Typography,
} from '@mui/material'
import { UserAccountMenu } from '@/components/Common/UserAccountMenu'
import { useQuery, useQueryClient } from 'react-query'
import {
  deletePluginAllVersions,
  deletePluginVersion,
  deleteSkillPatch,
  getPluginVersionDetail,
  getPlugins,
  getSkillPatchArtifactDownload,
  listSkillPatches,
  MarketplaceApiError,
  publishSkillPatch,
  type SkillPatchItem,
} from '@/api/plugin'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { sha256HexOfFile } from '@/utils/sha256File'

function triggerBrowserDownload(url: string, filename: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

export default function MyPluginDetailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { assetId: assetIdParam } = useParams<{ assetId: string }>()
  const assetId = assetIdParam ? decodeURIComponent(assetIdParam) : ''
  const { user, isAuthenticated, logout } = useGitCodeAuth()
  const stateVersion = (location.state as { latestVersion?: string } | null)?.latestVersion

  const [selectedVersion, setSelectedVersion] = useState<string | null>(null)
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [deleteOneOpen, setDeleteOneOpen] = useState(false)
  const [versionToDelete, setVersionToDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [patchFile, setPatchFile] = useState<File | null>(null)
  const [patchChecksum, setPatchChecksum] = useState('')
  const [patchHashing, setPatchHashing] = useState(false)
  const [patchVersion, setPatchVersion] = useState('')
  const [patchDesc, setPatchDesc] = useState('')
  const [patchForce, setPatchForce] = useState(false)
  const [patchUploading, setPatchUploading] = useState(false)
  const [patchError, setPatchError] = useState('')
  const [patchSuccess, setPatchSuccess] = useState('')
  const [patchFileInputKey, setPatchFileInputKey] = useState(0)
  const [patchDeletingVersion, setPatchDeletingVersion] = useState<string | null>(null)
  const [patchDownloadingVersion, setPatchDownloadingVersion] = useState<string | null>(null)

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/profile/plugins/${encodeURIComponent(assetId)}`)
    navigate('/login', { replace: true })
  }, [isAuthenticated, navigate, assetId])

  const {
    data: summaryRes,
    isLoading: summaryLoading,
    error: summaryError,
    refetch: refetchSummary,
  } = useQuery(
    ['my-plugin-summary', assetId, user?.id],
    () =>
      getPlugins({
        publisher_id: user!.id,
        asset_id: assetId,
        page: 1,
        page_size: 1,
      }),
    {
      enabled: Boolean(assetId && user?.id),
      staleTime: 0,
    },
  )

  const summaryItem = summaryRes?.data?.items?.[0]
  const isSkillAsset = (summaryItem?.plugin_type || '').toLowerCase() === 'skill'
  const allVersions = useMemo(() => {
    const raw = summaryItem?.all_versions
    if (Array.isArray(raw) && raw.length > 0) return raw
    const lv = summaryItem?.latest_version?.trim()
    return lv ? [lv] : []
  }, [summaryItem])

  const {
    data: patchRes,
    isLoading: patchesLoading,
    error: patchesError,
    refetch: refetchPatches,
  } = useQuery(
    ['skill-patches', assetId],
    () => listSkillPatches(assetId, { page: 1, page_size: 50 }),
    {
      enabled: Boolean(assetId && isSkillAsset && user?.id),
      staleTime: 0,
    },
  )

  const skillPatches = patchRes?.data.items ?? []

  /** 与列表同步：保持选中版本在 all_versions 内；初次用路由 state / latest 兜底 */
  useEffect(() => {
    if (allVersions.length === 0) {
      setSelectedVersion(null)
      return
    }
    setSelectedVersion(prev => {
      if (prev && allVersions.includes(prev)) return prev
      const hint = stateVersion?.trim()
      if (hint && allVersions.includes(hint)) return hint
      const latest = summaryItem?.latest_version?.trim()
      if (latest && allVersions.includes(latest)) return latest
      return allVersions[allVersions.length - 1]
    })
  }, [allVersions, summaryItem?.latest_version, summaryItem?.asset_id, stateVersion])

  const { data: detail, isLoading: detailLoading, error } = useQuery(
    ['my-plugin-version', assetId, selectedVersion],
    () => getPluginVersionDetail(assetId, selectedVersion!),
    {
      enabled: Boolean(assetId && selectedVersion && user?.id),
      staleTime: 0,
    },
  )

  useEffect(() => {
    if (!patchFile) {
      setPatchChecksum('')
      return
    }
    let cancelled = false
    setPatchHashing(true)
    setPatchError('')
    void sha256HexOfFile(patchFile)
      .then(hex => {
        if (!cancelled) setPatchChecksum(hex)
      })
      .catch(() => {
        if (!cancelled) {
          setPatchChecksum('')
          setPatchError(t('profile.skillPatchHashFailed'))
        }
      })
      .finally(() => {
        if (!cancelled) setPatchHashing(false)
      })
    return () => {
      cancelled = true
    }
  }, [patchFile, t])

  /**
   * GET 插件版本详情为公开接口，非所有者仍能拿到 detail；此处仅用于隐藏删除等写操作。
   * 若日后对 GET 加鉴权并返回 403，需走错误态文案。
   */
  const forbidden =
    detail && user?.id && detail.publisher_id !== user.id ? true : false

  const summaryErrMsg = useMemo(() => {
    if (!summaryError) return ''
    if (summaryError instanceof MarketplaceApiError) return summaryError.message
    return summaryError instanceof Error ? summaryError.message : String(summaryError)
  }, [summaryError])

  const errMsg = useMemo(() => {
    if (!error) return ''
    if (error instanceof MarketplaceApiError) return error.message
    return error instanceof Error ? error.message : String(error)
  }, [error])

  const patchesErrMsg = useMemo(() => {
    if (!patchesError) return ''
    if (patchesError instanceof MarketplaceApiError) return patchesError.message
    return patchesError instanceof Error ? patchesError.message : String(patchesError)
  }, [patchesError])

  const notFound = !summaryLoading && !summaryItem && !summaryErrMsg

  const handleDeleteAll = async () => {
    if (!assetId || !user?.id) return
    setDeleting(true)
    try {
      await deletePluginAllVersions(assetId)
      setDeleteAllOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['my-plugin-summary'] })
      navigate('/profile', { replace: true })
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('profile.deleteFailed')
      window.alert(msg)
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteOne = async () => {
    if (!assetId || !user?.id || !versionToDelete) return
    setDeleting(true)
    const deleted = versionToDelete
    try {
      await deletePluginVersion(assetId, deleted)
      setDeleteOneOpen(false)
      setVersionToDelete(null)

      queryClient.removeQueries({ queryKey: ['my-plugin-version', assetId, deleted], exact: true })

      const rest = allVersions.filter(v => v !== deleted)
      if (rest.length === 0) {
        await queryClient.invalidateQueries({ queryKey: ['my-plugin-summary'] })
        await queryClient.invalidateQueries({ queryKey: ['plugins'] })
        navigate('/profile', { replace: true })
        return
      }

      const nextSel =
        selectedVersion && selectedVersion !== deleted && rest.includes(selectedVersion)
          ? selectedVersion
          : rest[rest.length - 1]
      setSelectedVersion(nextSel)

      const [sumResult] = await Promise.all([
        refetchSummary(),
        queryClient.refetchQueries({ queryKey: ['my-plugin-version', assetId, nextSel], exact: true }),
      ])
      await queryClient.invalidateQueries({ queryKey: ['plugins'] })

      if (!sumResult.data?.data?.items?.[0]) {
        navigate('/profile', { replace: true })
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('profile.deleteFailed')
      window.alert(msg)
    } finally {
      setDeleting(false)
    }
  }

  const handlePublishPatch = async () => {
    if (!assetId || !patchFile || !patchChecksum || patchHashing || patchUploading) return
    setPatchUploading(true)
    setPatchError('')
    setPatchSuccess('')
    try {
      const data = await publishSkillPatch({
        skillAssetId: assetId,
        file: patchFile,
        checksumSha256Hex: patchChecksum,
        patchVersion: patchVersion.trim() || undefined,
        sourceSkillVersion: selectedVersion || summaryItem?.latest_version || undefined,
        versionDesc: patchDesc.trim() || undefined,
        patchType: 'self-evolution',
        force: patchForce,
      })
      setPatchFile(null)
      setPatchChecksum('')
      setPatchVersion('')
      setPatchDesc('')
      setPatchForce(false)
      setPatchFileInputKey(k => k + 1)
      setPatchSuccess(t('profile.skillPatchPublishSuccess', { version: data.patch_version }))
      await refetchPatches()
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : t('profile.skillPatchPublishFailed'))
    } finally {
      setPatchUploading(false)
    }
  }

  const handleDownloadPatch = async (patch: SkillPatchItem) => {
    setPatchDownloadingVersion(patch.patch_version)
    try {
      const meta = await getSkillPatchArtifactDownload(assetId, patch.patch_version)
      const filename = `${summaryItem?.name || 'skill'}_patch_${meta.patch_version}.zip`
      triggerBrowserDownload(meta.download_url, filename)
    } catch (e) {
      window.alert(e instanceof Error ? e.message : t('profile.skillPatchDownloadFailed'))
    } finally {
      setPatchDownloadingVersion(null)
    }
  }

  const handleDeletePatch = async (patch: SkillPatchItem) => {
    if (!window.confirm(t('profile.skillPatchDeleteConfirm', { version: patch.patch_version }))) return
    setPatchDeletingVersion(patch.patch_version)
    try {
      await deleteSkillPatch(assetId, patch.patch_version)
      await refetchPatches()
    } catch (e) {
      window.alert(e instanceof Error ? e.message : t('profile.skillPatchDeleteFailed'))
    } finally {
      setPatchDeletingVersion(null)
    }
  }

  const openDeleteOne = useCallback((v: string) => {
    setVersionToDelete(v)
    setDeleteOneOpen(true)
  }, [])

  const versionsNewestFirst = useMemo(() => [...allVersions].reverse(), [allVersions])
  const patchCanSubmit = Boolean(
    isSkillAsset && patchFile && patchChecksum && !patchHashing && !patchUploading,
  )

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-gradient-to-br from-[#f8fbff] via-[#f6faff] to-[#eef4ff]">
        <Typography variant="body2" color="text.secondary">
          {t('profile.redirecting')}
        </Typography>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-dvh flex-col bg-gradient-to-br from-[#f8fbff] via-[#f6faff] to-[#eef4ff]">
      <header className="relative z-10 border-b border-slate-200/80 bg-white/90 px-4 py-3 shadow-sm shadow-slate-200/40">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              to="/profile"
              className="inline-flex items-center gap-1 text-sm font-medium text-[#0369a1] hover:text-[#0c4a6e]"
            >
              <ArrowLeft className="h-4 w-4" />
              {t('profile.backToList')}
            </Link>
          </div>
          <UserAccountMenu
            primaryLabel={user.name || user.login}
            title={user.name || user.login}
            items={[
              {
                id: 'logout',
                label: t('auth.toolbar.logout'),
                onClick: () => {
                  logout()
                  navigate('/', { replace: true })
                },
              },
            ]}
          />
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        {summaryErrMsg ? (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{summaryErrMsg}</div>
        ) : null}
        {notFound ? (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {t('profile.pluginNotFound')}
          </div>
        ) : null}
        {summaryLoading ? (
          <Typography variant="body2" className="text-slate-500">
            {t('plugins.loading')}
          </Typography>
        ) : summaryItem && allVersions.length === 0 ? (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {t('profile.missingVersion')}
          </div>
        ) : null}

        {summaryItem && allVersions.length > 0 ? (
          <div className="rounded-2xl border border-slate-200/80 bg-white/95 p-6 shadow-sm">
            <Typography variant="h5" className="mb-1 font-bold text-slate-900">
              {summaryItem.display_name || summaryItem.displayName || summaryItem.name}
            </Typography>

            <div className="mb-4 mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
              <FormControl size="small" className="min-w-[220px] flex-1" sx={{ maxWidth: 360 }}>
                <InputLabel id="profile-plugin-version-label">{t('profile.selectVersion')}</InputLabel>
                <Select
                  labelId="profile-plugin-version-label"
                  label={t('profile.selectVersion')}
                  value={selectedVersion ?? ''}
                  onChange={e => setSelectedVersion(String(e.target.value))}
                >
                  {versionsNewestFirst.map(v => (
                    <MenuItem key={v} value={v}>
                      v{v}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              {!forbidden ? (
                <Button
                  color="error"
                  variant="outlined"
                  size="small"
                  disabled={!selectedVersion}
                  startIcon={<Trash2 className="h-4 w-4" aria-hidden />}
                  onClick={() => selectedVersion && openDeleteOne(selectedVersion)}
                  sx={{ textTransform: 'none', flexShrink: 0 }}
                >
                  {t('profile.deleteVersion')}
                </Button>
              ) : null}
            </div>

            {errMsg ? (
              <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errMsg}</div>
            ) : null}
            {forbidden ? (
              <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {t('profile.notOwner')}
              </div>
            ) : null}

            {detailLoading && selectedVersion ? (
              <Typography variant="body2" className="mb-4 text-slate-500">
                {t('plugins.loading')}
              </Typography>
            ) : null}

            {detail ? (
              <>
                <div className="mb-4 space-y-1 text-sm text-slate-700">
                  <div>
                    <span className="font-medium text-slate-900">{t('plugins.detail.publisher')}: </span>
                    {detail.publisher_name}
                  </div>
                  {detail.plugin_type ? (
                    <div>
                      <span className="font-medium text-slate-900">{t('plugins.detail.runtime')}: </span>
                      {detail.plugin_type}
                    </div>
                  ) : null}
                </div>

                {detail.short_desc ? (
                  <div className="mb-4">
                    <Typography variant="subtitle2" className="mb-1 font-bold text-slate-900">
                      {t('plugins.detail.summary')}
                    </Typography>
                    <PluginMarkdown
                      source={detail.short_desc}
                      className="prose prose-sm prose-neutral max-w-none text-slate-800 prose-p:my-1"
                    />
                  </div>
                ) : null}

                {detail.detail_desc ? (
                  <div className="mb-4">
                    <Typography variant="subtitle2" className="mb-1 font-bold text-slate-900">
                      {t('plugins.detail.description')}
                    </Typography>
                    <PluginMarkdown
                      source={detail.detail_desc}
                      className="prose prose-sm prose-neutral max-w-none text-slate-800 prose-p:my-1"
                    />
                  </div>
                ) : null}

                <div className="mb-4 rounded-xl border border-slate-200/90 bg-slate-50/90 p-4">
                  <Typography variant="subtitle2" className="mb-2 font-bold text-slate-900">
                    {t('profile.changelog')} · v{detail.version}
                  </Typography>
                  {detail.changelog?.trim() ? (
                    <PluginMarkdown
                      source={detail.changelog.trim()}
                      className="prose prose-sm prose-neutral max-w-none max-h-64 overflow-y-auto text-slate-800 prose-p:my-1 prose-headings:my-2 prose-headings:scroll-mt-2 [&_p]:text-[0.9375rem]"
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {t('profile.changelogEmpty')}
                    </Typography>
                  )}
                </div>

                {isSkillAsset ? (
                  <div className="mb-4 rounded-xl border border-cyan-200/80 bg-cyan-50/70 p-4">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <Typography variant="subtitle2" className="font-bold text-slate-900">
                          {t('profile.skillPatchTitle')}
                        </Typography>
                        <Typography variant="caption" className="text-slate-600">
                          {t('profile.skillPatchIntro')}
                        </Typography>
                      </div>
                      <Button
                        size="small"
                        variant="text"
                        onClick={() => void refetchPatches()}
                        disabled={patchesLoading}
                        sx={{ textTransform: 'none' }}
                      >
                        {t('plugins.actions.refresh')}
                      </Button>
                    </div>

                    {patchesErrMsg ? (
                      <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                        {patchesErrMsg}
                      </div>
                    ) : null}
                    {patchError ? (
                      <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                        {patchError}
                      </div>
                    ) : null}
                    {patchSuccess ? (
                      <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                        {patchSuccess}
                      </div>
                    ) : null}

                    {!forbidden ? (
                      <div className="mb-4 grid gap-3 rounded-lg border border-white/80 bg-white/80 p-3">
                        <Typography variant="body2" className="font-semibold text-slate-900">
                          {t('profile.skillPatchPublishTitle')}
                        </Typography>
                        <input
                          key={patchFileInputKey}
                          type="file"
                          accept=".zip,application/zip"
                          className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-cyan-900 hover:file:bg-cyan-200"
                          onChange={e => {
                            setPatchFile(e.target.files?.[0] ?? null)
                            setPatchSuccess('')
                          }}
                        />
                        <div className="grid gap-3 md:grid-cols-2">
                          <TextField
                            label={t('profile.skillPatchVersion')}
                            value={patchVersion}
                            onChange={e => setPatchVersion(e.target.value)}
                            size="small"
                            helperText={t('profile.skillPatchVersionHelp')}
                          />
                          <TextField
                            label={t('profile.skillPatchSourceVersion')}
                            value={selectedVersion ?? ''}
                            size="small"
                            InputProps={{ readOnly: true }}
                            helperText={t('profile.skillPatchSourceVersionHelp')}
                          />
                        </div>
                        <TextField
                          label={t('profile.skillPatchDesc')}
                          value={patchDesc}
                          onChange={e => setPatchDesc(e.target.value)}
                          size="small"
                          multiline
                          minRows={2}
                        />
                        <TextField
                          label={t('publish.checksumLabel')}
                          value={patchChecksum}
                          size="small"
                          InputProps={{ readOnly: true }}
                          placeholder={patchFile ? '' : t('profile.skillPatchChecksumPlaceholder')}
                          sx={{ '& .MuiInputBase-input': { fontFamily: 'ui-monospace, monospace', fontSize: 13 } }}
                        />
                        <div className="flex flex-wrap items-center gap-3">
                          <FormControlLabel
                            control={<Checkbox checked={patchForce} onChange={e => setPatchForce(e.target.checked)} />}
                            label={t('profile.skillPatchForce')}
                          />
                          <Button
                            variant="contained"
                            size="small"
                            disabled={!patchCanSubmit}
                            startIcon={patchUploading || patchHashing ? <CircularProgress size={14} /> : <UploadCloud className="h-4 w-4" />}
                            onClick={() => void handlePublishPatch()}
                            sx={{ textTransform: 'none', bgcolor: '#0891b2', '&:hover': { bgcolor: '#0e7490' } }}
                          >
                            {patchUploading ? t('profile.skillPatchUploading') : patchHashing ? t('publish.hashing') : t('profile.skillPatchSubmit')}
                          </Button>
                        </div>
                      </div>
                    ) : null}

                    {patchesLoading ? (
                      <Typography variant="body2" className="text-slate-500">
                        {t('plugins.loading')}
                      </Typography>
                    ) : skillPatches.length === 0 ? (
                      <Typography variant="body2" className="text-slate-500">
                        {t('profile.skillPatchEmpty')}
                      </Typography>
                    ) : (
                      <div className="divide-y divide-cyan-100 overflow-hidden rounded-lg border border-cyan-100 bg-white/85">
                        {skillPatches.map(patch => (
                          <div key={patch.patch_id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-semibold tabular-nums text-slate-900">v{patch.patch_version}</span>
                                <span className="rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-900">
                                  {patch.patch_type}
                                </span>
                                {patch.source_skill_version ? (
                                  <span className="text-xs text-slate-500">
                                    {t('profile.skillPatchBasedOn', { version: patch.source_skill_version })}
                                  </span>
                                ) : null}
                              </div>
                              <div className="mt-1 max-w-[520px] truncate text-sm text-slate-600">
                                {patch.changelog?.trim() || t('profile.changelogEmpty')}
                              </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                              <Button
                                size="small"
                                variant="text"
                                disabled={patchDownloadingVersion === patch.patch_version}
                                startIcon={<Download className="h-4 w-4" />}
                                onClick={() => void handleDownloadPatch(patch)}
                                sx={{ textTransform: 'none' }}
                              >
                                {t('plugins.actions.download')}
                              </Button>
                              {!forbidden ? (
                                <Button
                                  size="small"
                                  color="error"
                                  variant="text"
                                  disabled={patchDeletingVersion === patch.patch_version}
                                  startIcon={<Trash2 className="h-4 w-4" />}
                                  onClick={() => void handleDeletePatch(patch)}
                                  sx={{ textTransform: 'none' }}
                                >
                                  {t('profile.deleteVersion')}
                                </Button>
                              ) : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
              </>
            ) : !detailLoading && selectedVersion && !errMsg ? (
              <Typography variant="body2" className="mb-4 text-slate-500">
                {t('profile.noDetail')}
              </Typography>
            ) : null}

            {!forbidden ? (
              <Button
                color="error"
                variant="outlined"
                onClick={() => setDeleteAllOpen(true)}
                sx={{ textTransform: 'none' }}
              >
                {t('profile.deleteAll')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </main>

      <Dialog open={deleteAllOpen} onClose={() => !deleting && setDeleteAllOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('profile.deleteConfirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" className="text-slate-700">
            {t('profile.deleteConfirmBody')}
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteAllOpen(false)} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('common.buttons.close')}
          </Button>
          <Button color="error" variant="contained" onClick={() => void handleDeleteAll()} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('profile.deleteConfirmAction')}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteOneOpen} onClose={() => !deleting && setDeleteOneOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('profile.deleteVersionConfirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" className="text-slate-700">
            {t('profile.deleteVersionConfirmBody', { version: versionToDelete ?? '' })}
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteOneOpen(false)} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('common.buttons.close')}
          </Button>
          <Button color="error" variant="contained" onClick={() => void handleDeleteOne()} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('profile.deleteVersionConfirmAction')}
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
