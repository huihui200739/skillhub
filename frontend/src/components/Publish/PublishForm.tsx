import { type FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Download, FolderUp, ImagePlus, Loader2, UploadCloud } from 'lucide-react'
import { useQuery } from 'react-query'
import { getPlugins, getPublishTemplatePresigned, publishPlugin } from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { sha256HexOfFile } from '@/utils/sha256File'
import { buildSkillPublishZip } from '@/utils/buildSkillPublishZip'

const SKILL_ZIP_ERROR_KEYS: Record<string, string> = {
  INVALID_NAME: 'publish.skillErrorInvalidName',
  INVALID_VERSION: 'publish.skillErrorInvalidVersion',
  INVALID_DISPLAY_NAME: 'publish.skillErrorInvalidDisplayName',
  INVALID_DESCRIPTION: 'publish.skillErrorInvalidDescription',
  INVALID_SKILL_DESC: 'publish.skillErrorInvalidSkillDesc',
  INVALID_TAG: 'publish.skillErrorInvalidTag',
  INVALID_AUTHOR: 'publish.skillErrorInvalidAuthor',
  NO_SKILL_FILES: 'publish.skillErrorNoFiles',
  MISSING_RELATIVE_PATH: 'publish.skillErrorMissingRelativePath',
  SKILL_MD_NOT_AT_ROOT: 'publish.skillErrorSkillMdNotAtRoot',
  MISSING_SKILL_MD: 'publish.skillErrorMissingSkillMd',
  MISSING_SKILL_MD_DESCRIPTION: 'publish.skillErrorMissingSkillMdDescription',
  ICON_NOT_PNG: 'publish.skillErrorIconNotPng',
  ICON_TOO_LARGE: 'publish.skillErrorIconTooLarge',
  TOO_MANY_ZIP_ENTRIES: 'publish.skillErrorTooManyEntries',
}

type PublishFormProps = {
  onCancel: () => void
  onSuccess?: () => void
}

/** 统一字段外壳：小号标签 + 必填红色星号 + 提示文案。 */
function Field({
  label,
  required,
  hint,
  htmlFor,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  htmlFor?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 flex items-center gap-1 text-[12.5px] font-medium text-[#334155]"
      >
        {required ? <span className="text-[#F43F5E]">*</span> : null}
        <span>{label}</span>
      </label>
      {children}
      {hint ? <p className="mt-1.5 text-[11.5px] leading-[1.4] text-[#94A3B8]">{hint}</p> : null}
    </div>
  )
}

const inputBase =
  'block h-10 w-full rounded-lg border border-[#E2E8F0] bg-white px-3 text-[13.5px] text-[#0F172A] placeholder:text-[#94A3B8] transition-colors hover:border-[#CBD5E1] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const textareaBase =
  'block w-full rounded-lg border border-[#E2E8F0] bg-white px-3 py-2 text-[13.5px] leading-[1.6] text-[#0F172A] placeholder:text-[#94A3B8] transition-colors hover:border-[#CBD5E1] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

export function PublishForm({ onCancel, onSuccess }: PublishFormProps) {
  const { t } = useTranslation()
  const { user, isAuthenticated } = useGitCodeAuth()
  const skillFolderInputId = useId()
  const skillIconInputId = useId()
  const skillNameId = useId()
  const skillVersionId = useId()
  const skillDisplayNameId = useId()
  const skillDescriptionId = useId()
  const skillTagsId = useId()
  const versionDescId = useId()
  const pluginLinkId = useId()

  const [file, setFile] = useState<File | null>(null)
  const [checksum, setChecksum] = useState('')
  const [hashing, setHashing] = useState(false)
  const [pluginId, setPluginId] = useState('')
  const [pluginVersion, setPluginVersion] = useState('')
  const [versionDesc, setVersionDesc] = useState('')
  const [force, setForce] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [successMsg, setSuccessMsg] = useState('')
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateError, setTemplateError] = useState('')

  const [skillPkgName, setSkillPkgName] = useState('')
  const [skillDisplayName, setSkillDisplayName] = useState('')
  const [skillDescription, setSkillDescription] = useState('')
  const [skillTagsInput, setSkillTagsInput] = useState('')
  const [skillIconFile, setSkillIconFile] = useState<File | null>(null)
  const [skillIconPreview, setSkillIconPreview] = useState('')
  const [skillIconError, setSkillIconError] = useState('')
  const [skillFolderFiles, setSkillFolderFiles] = useState<File[] | null>(null)
  const [skillFolderInputKey, setSkillFolderInputKey] = useState(0)
  const [skillIconInputKey, setSkillIconInputKey] = useState(0)
  const [packing, setPacking] = useState(false)
  const prevSkillPluginIdRef = useRef('')

  const skillMetadataLocked = Boolean(pluginId.trim())

  useEffect(() => {
    if (!skillIconFile) {
      setSkillIconPreview('')
      return
    }
    const url = URL.createObjectURL(skillIconFile)
    setSkillIconPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [skillIconFile])

  const { data: myPluginsRes, isLoading: myPluginsLoading } = useQuery(
    ['publish-my-plugins', user?.id, 'skill'],
    () =>
      getPlugins({
        publisher_id: user!.id,
        page: 1,
        page_size: 100,
        plugin_type: 'skill',
      }),
    { enabled: Boolean(isAuthenticated && user?.id) },
  )

  const myPlugins = useMemo(() => {
    const items = myPluginsRes?.data?.items ?? []
    return [...items].sort((a, b) => {
      const na = (a.display_name || a.displayName || a.name || '').toLowerCase()
      const nb = (b.display_name || b.displayName || b.name || '').toLowerCase()
      return na.localeCompare(nb, undefined, { sensitivity: 'base' })
    })
  }, [myPluginsRes])

  useEffect(() => {
    const prev = prevSkillPluginIdRef.current
    const pid = pluginId.trim()
    if (pid) {
      const row = myPlugins.find(p => p.asset_id === pid)
      if (row) {
        setSkillPkgName(row.name ?? '')
        setSkillDisplayName(row.display_name || row.displayName || row.name || '')
        setSkillDescription(
          String(row.detail_desc || row.detailDesc || row.short_desc || row.shortDesc || '').trim(),
        )
        setSkillTagsInput((row.tags ?? []).filter(Boolean).join(', '))
      }
    } else if (prev) {
      setSkillPkgName('')
      setSkillDisplayName('')
      setSkillDescription('')
      setSkillTagsInput('')
    }
    prevSkillPluginIdRef.current = pluginId
  }, [pluginId, myPlugins])

  const skillFolderRootName = useMemo(() => {
    const first = skillFolderFiles?.[0] as (File & { webkitRelativePath?: string }) | undefined
    const p = first?.webkitRelativePath
    if (!p) return ''
    return p.split(/[/\\]/).filter(Boolean)[0] ?? ''
  }, [skillFolderFiles])

  /** 与下方防抖打包共用：用于「可发布」判断；实际上传在 onSubmit 内会再次同步打包，避免依赖过期的预览 zip。 */
  const skillFormReady = useMemo(() => {
    const login = user?.login?.trim()
    return Boolean(
      login &&
      skillFolderFiles &&
      skillFolderFiles.length > 0 &&
      skillPkgName.trim() &&
      pluginVersion.trim() &&
      skillDisplayName.trim(),
    )
  }, [user?.login, skillFolderFiles, skillPkgName, pluginVersion, skillDisplayName])

  useEffect(() => {
    if (!skillFormReady) {
      setPacking(false)
      setFile(null)
      return
    }

    const login = user?.login?.trim()
    if (!login) {
      setPacking(false)
      setFile(null)
      return
    }

    let cancelled = false
    setPacking(true)
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const tags = skillTagsInput
            .split(/[,，]/)
            .map(s => s.trim())
            .filter(Boolean)
          const zipFile = await buildSkillPublishZip({
            name: skillPkgName.trim(),
            version: pluginVersion.trim(),
            displayName: skillDisplayName.trim(),
            description: skillDescription.trim() || undefined,
            tags,
            authorLogin: login,
            iconFile: skillIconFile ?? undefined,
            skillDirectoryFiles: skillFolderFiles!,
          })
          if (cancelled) return
          setFile(zipFile)
          setError('')
        } catch (e) {
          if (cancelled) return
          setFile(null)
          const code = e instanceof Error ? e.message : ''
          const i18nKey = SKILL_ZIP_ERROR_KEYS[code]
          setError(i18nKey ? t(i18nKey) : t('publish.skillPackFailed'))
        } finally {
          if (!cancelled) setPacking(false)
        }
      })()
    }, 700)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [
    skillFormReady,
    user?.login,
    skillIconFile,
    skillFolderFiles,
    skillPkgName,
    pluginVersion,
    skillDisplayName,
    skillDescription,
    skillTagsInput,
    t,
  ])

  useEffect(() => {
    if (!file) {
      setChecksum('')
      return
    }
    let cancelled = false
    setHashing(true)
    void sha256HexOfFile(file)
      .then(hex => {
        if (!cancelled) {
          setChecksum(hex)
          setError('')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChecksum('')
          setError(t('publish.hashFailed'))
        }
      })
      .finally(() => {
        if (!cancelled) setHashing(false)
      })
    return () => {
      cancelled = true
    }
  }, [file, t])

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!skillFormReady || uploading || successMsg) return
    setUploading(true)
    setError('')
    setSuccessMsg('')
    try {
      const login = user?.login?.trim()
      if (!login || !skillFolderFiles?.length) {
        setError(t('publish.uploadFailed'))
        return
      }
      const tags = skillTagsInput
        .split(/[,，]/)
        .map(s => s.trim())
        .filter(Boolean)
      const zipFile = await buildSkillPublishZip({
        name: skillPkgName.trim(),
        version: pluginVersion.trim(),
        displayName: skillDisplayName.trim(),
        description: skillDescription.trim() || undefined,
        tags,
        authorLogin: login,
        iconFile: skillIconFile ?? undefined,
        skillDirectoryFiles: skillFolderFiles,
      })
      const checksumFresh = await sha256HexOfFile(zipFile)
      const data = await publishPlugin({
        file: zipFile,
        checksumSha256Hex: checksumFresh,
        pluginId: pluginId.trim() || undefined,
        pluginVersion: pluginVersion.trim() || undefined,
        versionDesc: versionDesc.trim() || undefined,
        force,
      })
      setFile(null)
      setChecksum('')
      setPluginId('')
      setPluginVersion('')
      setVersionDesc('')
      setForce(false)
      setSkillPkgName('')
      setSkillDisplayName('')
      setSkillDescription('')
      setSkillTagsInput('')
      setSkillIconFile(null)
      setSkillIconError('')
      setSkillFolderFiles(null)
      setSkillFolderInputKey(k => k + 1)
      setSkillIconInputKey(k => k + 1)
      setSuccessMsg(
        t('publish.successDetail', {
          name: data.name,
          version: data.version,
          pluginId: data.plugin_id,
        }),
      )
      onSuccess?.()
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('publish.uploadFailed')
      const i18nKey = SKILL_ZIP_ERROR_KEYS[msg]
      setError(i18nKey ? t(i18nKey) : msg)
    } finally {
      setUploading(false)
    }
  }

  useEffect(() => {
    if (!successMsg) return
    const id = window.setTimeout(() => {
      onCancel()
    }, 1500)
    return () => window.clearTimeout(id)
  }, [successMsg, onCancel])

  const onDownloadTemplate = async () => {
    setTemplateError('')
    setTemplateBusy(true)
    try {
      const { download_url: url } = await getPublishTemplatePresigned({ kind: 'skill' })
      window.location.assign(url)
    } catch (err) {
      setTemplateError(err instanceof Error ? err.message : t('publish.templateDownloadFailed'))
    } finally {
      setTemplateBusy(false)
    }
  }

  const canSubmit = Boolean(skillFormReady && !uploading && !successMsg)

  return (
    <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col" aria-busy={uploading || packing || hashing}>
      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-7 pb-6 pt-4">
        {/* Error / success / template error banners */}
        {error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] leading-5 text-rose-800">
            {error}
          </div>
        ) : null}
        {successMsg ? (
          <div className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-[12.5px] leading-5 text-emerald-900">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden />
            <div>
              <div>{successMsg}</div>
              <div className="mt-0.5 text-[11.5px] text-emerald-800/80">
                {t('publish.redirectHint')}
              </div>
            </div>
          </div>
        ) : null}
        {templateError ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] leading-5 text-amber-900">
            {templateError}
          </div>
        ) : null}

        <Field
          htmlFor={pluginLinkId}
          label={t('publish.fieldPluginIdSkill')}
          hint={
            myPluginsLoading ? t('publish.pluginListLoading') : t('publish.fieldPluginIdHelpSkill')
          }
        >
          <select
            id={pluginLinkId}
            value={pluginId}
            onChange={e => setPluginId(String(e.target.value))}
            className={inputBase}
          >
            <option value="">{t('publish.pluginIdNewOptionSkill')}</option>
            {myPlugins.map(p => {
              const title = p.display_name || p.displayName || p.name
              const pkgName = p.name?.trim() || '—'
              return (
                <option key={p.asset_id} value={p.asset_id}>
                  {title}
                  {pkgName && pkgName !== title ? ` (${pkgName})` : ''}
                </option>
              )
            })}
          </select>
        </Field>

        {skillMetadataLocked ? (
          <div className="-mt-3 rounded-md border border-sky-200/70 bg-sky-50/70 px-3 py-2 text-[11.5px] leading-5 text-[#0C4A8A]">
            {t('publish.fieldSkillMetadataLockedHint')}
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-4">
          <Field
            htmlFor={skillNameId}
            label={t('publish.fieldSkillName')}
            required
            hint={t('publish.fieldSkillNameHelp')}
          >
            <input
              id={skillNameId}
              type="text"
              className={inputBase}
              value={skillPkgName}
              onChange={e => setSkillPkgName(e.target.value)}
              disabled={skillMetadataLocked}
              placeholder="my-demo-skill"
              required
            />
          </Field>

          <Field
            htmlFor={skillVersionId}
            label={t('publish.fieldVersionSkill')}
            required
            hint={t('publish.fieldVersionSkillHelp')}
          >
            <input
              id={skillVersionId}
              type="text"
              className={inputBase}
              value={pluginVersion}
              onChange={e => setPluginVersion(e.target.value)}
              placeholder="1.0.0"
              required
            />
          </Field>
        </div>

        <Field
          htmlFor={skillDisplayNameId}
          label={t('publish.fieldSkillDisplayName')}
          required
          hint={t('publish.fieldSkillDisplayNameHelp')}
        >
          <input
            id={skillDisplayNameId}
            type="text"
            className={inputBase}
            value={skillDisplayName}
            onChange={e => setSkillDisplayName(e.target.value)}
            disabled={skillMetadataLocked}
            placeholder={t('publish.fieldSkillDisplayName')}
            required
          />
        </Field>

        <Field htmlFor={skillDescriptionId} label={t('publish.fieldSkillDescription')} hint={t('publish.fieldSkillDescriptionHelp')}>
          <textarea
            id={skillDescriptionId}
            className={textareaBase}
            rows={3}
            value={skillDescription}
            onChange={e => setSkillDescription(e.target.value)}
            disabled={skillMetadataLocked}
            placeholder={t('publish.fieldSkillDescriptionPlaceholder')}
          />
        </Field>

        <Field
          htmlFor={skillTagsId}
          label={t('publish.fieldSkillTags')}
          hint={t('publish.fieldSkillTagsHelp')}
        >
          <input
            id={skillTagsId}
            type="text"
            className={inputBase}
            value={skillTagsInput}
            onChange={e => setSkillTagsInput(e.target.value)}
            disabled={skillMetadataLocked}
            placeholder="tag1, tag2"
          />
        </Field>

        {/* Skill folder drop zone */}
        <Field
          label={t('publish.fieldSkillFolder')}
          required
          hint={t('publish.fieldSkillFolderHelp')}
        >
          <input
            key={skillFolderInputKey}
            id={skillFolderInputId}
            type="file"
            className="sr-only"
            // @ts-expect-error webkitdirectory 非标准属性，用于选择文件夹
            webkitdirectory=""
            multiple
            onChange={e => {
              const list = e.target.files
              setSkillFolderFiles(list && list.length ? Array.from(list) : null)
            }}
          />
          <label
            htmlFor={skillFolderInputId}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-center transition-colors ${
              skillFolderFiles?.length
                ? 'border-[#CBD5E1] bg-[#F8FAFC]'
                : 'border-[#CBD5E1] hover:border-[#1E54F9] hover:bg-[#F5F8FF]'
            }`}
          >
            <span
              className={`inline-flex h-9 w-9 items-center justify-center rounded-full ${
                skillFolderFiles?.length ? 'bg-emerald-50 text-emerald-600' : 'bg-[#EEF4FF] text-[#1E54F9]'
              }`}
            >
              {skillFolderFiles?.length ? (
                <CheckCircle2 className="h-4 w-4" aria-hidden />
              ) : (
                <FolderUp className="h-4 w-4" aria-hidden />
              )}
            </span>
            {skillFolderFiles?.length ? (
              <div className="space-y-0.5">
                <div className="text-[13px] font-medium text-[#0F172A]">
                  {skillFolderRootName || '—'}
                </div>
                <div className="text-[11.5px] text-[#64748B]">
                  {t('publish.skillFolderSelected', { name: skillFolderRootName || '—' })}
                </div>
              </div>
            ) : (
              <div className="space-y-0.5">
                <div className="text-[13px] font-medium text-[#334155]">
                  {t('publish.skillFolderChoose')}
                </div>
                <div className="text-[11.5px] text-[#94A3B8]">
                  {t('publish.skillFolderNone')}
                </div>
              </div>
            )}
          </label>
        </Field>

        {/* Skill icon picker with thumbnail preview + 即时校验 */}
        <Field label={t('publish.fieldSkillIcon')} hint={t('publish.fieldSkillIconHelp')}>
          <input
            key={skillIconInputKey}
            id={skillIconInputId}
            type="file"
            accept="image/png,.png"
            className="sr-only"
            onChange={e => {
              const f = e.target.files?.[0] ?? null
              if (!f) {
                setSkillIconFile(null)
                setSkillIconError('')
                return
              }
              const isPng =
                f.type === 'image/png' || f.name.toLowerCase().endsWith('.png')
              if (!isPng) {
                setSkillIconFile(null)
                setSkillIconError(t('publish.skillErrorIconNotPng'))
                setSkillIconInputKey(k => k + 1)
                return
              }
              if (f.size > 5 * 1024 * 1024) {
                setSkillIconFile(null)
                setSkillIconError(t('publish.skillErrorIconTooLarge'))
                setSkillIconInputKey(k => k + 1)
                return
              }
              setSkillIconError('')
              setSkillIconFile(f)
            }}
          />
          <div
            className={`flex items-center gap-3 rounded-xl p-1 ${
              skillIconError ? 'bg-rose-50/50 ring-1 ring-rose-200' : ''
            }`}
          >
            <label
              htmlFor={skillIconInputId}
              className={`group relative inline-flex h-[72px] w-[72px] shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-xl border border-dashed transition-colors ${
                skillIconError
                  ? 'border-rose-300 bg-rose-50'
                  : 'border-[#CBD5E1] bg-[#F8FAFC] hover:border-[#1E54F9] hover:bg-[#F5F8FF]'
              }`}
            >
              {skillIconPreview ? (
                <img
                  src={skillIconPreview}
                  alt=""
                  aria-hidden
                  className="h-full w-full object-cover"
                />
              ) : (
                <ImagePlus
                  className={`h-5 w-5 transition-colors ${
                    skillIconError
                      ? 'text-rose-400'
                      : 'text-[#94A3B8] group-hover:text-[#1E54F9]'
                  }`}
                  aria-hidden
                />
              )}
            </label>
            <div className="min-w-0 text-[12px] text-[#64748B]">
              <div className="truncate text-[13px] font-medium text-[#0F172A]">
                {skillIconFile?.name ?? t('publish.fieldSkillIcon')}
              </div>
              <div className="mt-0.5">PNG · ≤ 5MB</div>
            </div>
          </div>
          {skillIconError ? (
            <p className="mt-1.5 flex items-start gap-1 text-[12px] leading-[1.5] text-rose-600">
              <span aria-hidden>⚠</span>
              <span>{skillIconError}</span>
            </p>
          ) : null}
        </Field>

        {/* SHA-256 校验和：只读展示；打包/计算中显示进度徽标，完成后显示绿色“已就绪”。 */}
        <div className="rounded-lg border border-slate-200 bg-[#F8FAFC] px-3 py-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="text-[11.5px] font-medium text-[#64748B]">
              {t('publish.checksumLabel')}
            </span>
            {packing ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-[#1E54F9]">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                {t('publish.skillPacking')}
              </span>
            ) : hashing ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-[#1E54F9]">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                {t('publish.hashing')}
              </span>
            ) : checksum ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
                <CheckCircle2 className="h-3 w-3" aria-hidden />
                {t('publish.checksumReady')}
              </span>
            ) : null}
          </div>
          <div className="break-all font-mono text-[11px] leading-[1.6] tracking-[0.01em] text-[#334155] select-all">
            {checksum || (
              <span className="text-[#94A3B8]">{t('publish.checksumPlaceholderSkill')}</span>
            )}
          </div>
        </div>

        <Field htmlFor={versionDescId} label={t('publish.fieldVersionDesc')}>
          <textarea
            id={versionDescId}
            className={textareaBase}
            rows={3}
            maxLength={1000}
            value={versionDesc}
            onChange={e => setVersionDesc(e.target.value)}
            placeholder={t('publish.fieldVersionDesc')}
          />
          <p className="mt-1 text-right text-[11px] text-[#94A3B8]">{versionDesc.length}/1000</p>
        </Field>

        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-[#334155]">
          <input
            type="checkbox"
            checked={force}
            onChange={e => setForce(e.target.checked)}
            className="h-4 w-4 rounded border-[#CBD5E1] text-[#1E54F9] focus:ring-[#DBE6FF]"
          />
          <span>{t('publish.fieldForce')}</span>
        </label>
      </div>

      <footer className="shrink-0 border-t border-slate-100 bg-white/95 px-7 py-4 backdrop-blur">
        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={uploading}
            className="inline-flex h-9 min-w-[96px] items-center justify-center rounded-full border border-[#E2E8F0] bg-white px-4 text-sm font-medium text-[#475569] transition-colors hover:border-[#CBD5E1] hover:text-[#0F172A] disabled:opacity-60"
          >
            {t('profile.card.cancel')}
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex h-9 min-w-[116px] items-center justify-center gap-1.5 rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-sm font-medium text-white shadow-[0_6px_16px_-6px_rgba(30,84,249,0.55)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            {uploading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                <span>{t('publish.uploading')}</span>
              </>
            ) : (
              <>
                <UploadCloud className="h-3.5 w-3.5" aria-hidden />
                <span>{t('appHeader.publish')}</span>
              </>
            )}
          </button>
        </div>
      </footer>
    </form>
  )
}

export default PublishForm
