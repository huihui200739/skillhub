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
  TEAM_SKILL_ROLES_REQUIRED: 'publish.skillErrorTeamSkillRolesRequired',
  TEAM_SKILL_ROLES_MIN_2: 'publish.skillErrorTeamSkillRolesMin2',
  TEAM_SKILL_ROLE_NO_ID: 'publish.skillErrorTeamSkillRoleNoId',
}

/** 将构建/发布阶段抛出的错误码路由到具体字段，以便就地高亮和展示。 */
const ZIP_ERROR_TO_FIELD: Record<string, PublishFieldKey> = {
  INVALID_NAME: 'skillPkgName',
  INVALID_VERSION: 'pluginVersion',
  INVALID_DISPLAY_NAME: 'skillDisplayName',
  INVALID_DESCRIPTION: 'skillDescription',
  INVALID_SKILL_DESC: 'skillDescription',
  INVALID_TAG: 'skillTags',
  NO_SKILL_FILES: 'skillFolder',
  MISSING_RELATIVE_PATH: 'skillFolder',
  SKILL_MD_NOT_AT_ROOT: 'skillFolder',
  MISSING_SKILL_MD: 'skillFolder',
  MISSING_SKILL_MD_DESCRIPTION: 'skillFolder',
  TOO_MANY_ZIP_ENTRIES: 'skillFolder',
  TEAM_SKILL_ROLES_REQUIRED: 'skillFolder',
  TEAM_SKILL_ROLES_MIN_2: 'skillFolder',
  TEAM_SKILL_ROLE_NO_ID: 'skillFolder',
  ICON_NOT_PNG: 'skillIcon',
  ICON_TOO_LARGE: 'skillIcon',
}

type PublishFieldKey =
  | 'skillPkgName'
  | 'pluginVersion'
  | 'skillDisplayName'
  | 'skillDescription'
  | 'skillTags'
  | 'skillFolder'
  | 'skillIcon'

type PublishFieldErrors = Partial<Record<PublishFieldKey, string>>

type PublishFormProps = {
  onCancel: () => void
  onSuccess?: () => void
}

/**
 * 统一字段外壳：小号标签 + 必填红色星号 + 提示文案；存在 error 时以红字替代 hint。
 * 带 fieldKey 时会在根 div 上挂 `data-field-key`，供「提交失败时滚动定位到第一个错误字段」使用。
 */
function Field({
  label,
  required,
  hint,
  htmlFor,
  error,
  fieldKey,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  htmlFor?: string
  error?: string
  fieldKey?: PublishFieldKey
  children: React.ReactNode
}) {
  return (
    <div data-field-key={fieldKey}>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 flex items-center gap-1 text-[12.5px] font-medium text-[#334155]"
      >
        {required ? <span className="text-[#F43F5E]">*</span> : null}
        <span>{label}</span>
      </label>
      {children}
      {error ? (
        <p
          className="mt-1.5 flex items-start gap-1 text-[12px] leading-[1.5] text-rose-600"
          role="alert"
        >
          <span aria-hidden>⚠</span>
          <span>{error}</span>
        </p>
      ) : hint ? (
        <p className="mt-1.5 text-[11.5px] leading-[1.4] text-[#94A3B8]">{hint}</p>
      ) : null}
    </div>
  )
}

/** 字段在表单中从上到下的排序；用于「跳转到第一个错误」。 */
const PUBLISH_FIELD_ORDER: PublishFieldKey[] = [
  'skillPkgName',
  'pluginVersion',
  'skillDisplayName',
  'skillDescription',
  'skillTags',
  'skillFolder',
  'skillIcon',
]

/** 字段在中文语境下的可读名称，用于错误汇总条。 */
const FIELD_LABEL_KEYS: Record<PublishFieldKey, string> = {
  skillPkgName: 'publish.fieldSkillName',
  pluginVersion: 'publish.fieldVersionSkill',
  skillDisplayName: 'publish.fieldSkillDisplayName',
  skillDescription: 'publish.fieldSkillDescription',
  skillTags: 'publish.fieldSkillTags',
  skillFolder: 'publish.fieldSkillFolder',
  skillIcon: 'publish.fieldSkillIcon',
}

const inputBase =
  'block h-10 w-full rounded-lg border border-[#E2E8F0] bg-white px-3 text-[13.5px] text-[#0F172A] placeholder:text-[#94A3B8] transition-colors hover:border-[#CBD5E1] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const inputError =
  'block h-10 w-full rounded-lg border border-rose-300 bg-rose-50/30 px-3 text-[13.5px] text-[#0F172A] placeholder:text-rose-300/80 transition-colors hover:border-rose-400 focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-100 disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const textareaBase =
  'block w-full rounded-lg border border-[#E2E8F0] bg-white px-3 py-2 text-[13.5px] leading-[1.6] text-[#0F172A] placeholder:text-[#94A3B8] transition-colors hover:border-[#CBD5E1] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const textareaError =
  'block w-full rounded-lg border border-rose-300 bg-rose-50/30 px-3 py-2 text-[13.5px] leading-[1.6] text-[#0F172A] placeholder:text-rose-300/80 transition-colors hover:border-rose-400 focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-100 disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const inputCls = (hasError?: boolean) => (hasError ? inputError : inputBase)
const textareaCls = (hasError?: boolean) => (hasError ? textareaError : textareaBase)

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
  /** 无法归属到单个字段的错误（上传失败、未登录、未知后端错误等），展示在顶部横幅。 */
  const [generalError, setGeneralError] = useState('')
  /** 可归属到具体字段的错误：直接在字段下方展示，并高亮对应输入框。 */
  const [fieldErrors, setFieldErrors] = useState<PublishFieldErrors>({})
  const [successMsg, setSuccessMsg] = useState('')
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateError, setTemplateError] = useState('')

  const [skillPkgName, setSkillPkgName] = useState('')
  const [skillDisplayName, setSkillDisplayName] = useState('')
  const [skillDescription, setSkillDescription] = useState('')
  const [skillTagsInput, setSkillTagsInput] = useState('')
  const [skillIconFile, setSkillIconFile] = useState<File | null>(null)
  const [skillIconPreview, setSkillIconPreview] = useState('')
  const [skillFolderFiles, setSkillFolderFiles] = useState<File[] | null>(null)
  const [skillFolderInputKey, setSkillFolderInputKey] = useState(0)
  const [skillIconInputKey, setSkillIconInputKey] = useState(0)
  const [packing, setPacking] = useState(false)
  const prevSkillPluginIdRef = useRef('')

  /** 清除某个字段的错误（一般由 onChange 调用，让用户输入时错误即时消失）。 */
  const clearFieldError = (key: PublishFieldKey) => {
    setFieldErrors(prev => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  /**
   * 按表单顺序把视图滚动到第一个含错误字段并聚焦。
   * 通过 DOM 查询（`role="alert"`）避免 React 状态闭包问题；等待两帧让提交后的 setState 先刷新。
   */
  const scrollToFirstError = () => {
    const doScroll = () => {
      for (const key of PUBLISH_FIELD_ORDER) {
        const wrapper = document.querySelector<HTMLElement>(
          `[data-field-key="${key}"]`,
        )
        if (!wrapper) continue
        if (!wrapper.querySelector('[role="alert"]')) continue
        wrapper.scrollIntoView({ behavior: 'smooth', block: 'center' })
        const input = wrapper.querySelector<HTMLInputElement | HTMLTextAreaElement>(
          'input:not([type="file"]), textarea',
        )
        if (input) {
          try {
            input.focus({ preventScroll: true })
          } catch {
            /* ignore focus errors on disabled/hidden inputs */
          }
        }
        return
      }
      // 没有字段错误但有 general error：把表单滚回顶部，让顶部 banner 出现在视野内。
      const scrollRoot = document.querySelector<HTMLElement>(
        '[data-publish-form-scroll]',
      )
      scrollRoot?.scrollTo({ top: 0, behavior: 'smooth' })
    }
    requestAnimationFrame(() => requestAnimationFrame(doScroll))
  }

  /**
   * 对后端回传的人类可读错误做启发式路由。按正则匹配关键词，把错误落到对应字段。
   * 任何提到 SKILL.md / 子目录 / frontmatter / plugin.yaml 结构的问题都归到 skillFolder，
   * 因为用户只能通过调整本地 skill 目录来修复。
   */
  const detectFieldByKeyword = (message: string): PublishFieldKey | null => {
    if (!message) return null
    if (/SKILL\.md|frontmatter|子目录|sub\s*dir|plugin\.yaml|skill slug|非隐藏子目录/i.test(message)) {
      return 'skillFolder'
    }
    if (/图标|\bicon\b|\.png\b|PNG/i.test(message)) return 'skillIcon'
    if (/(插件|skill).*(版本|version)|版本号|semver/i.test(message)) return 'pluginVersion'
    return null
  }

  /** 把构建/上传阶段的错误码或原始消息路由到对应字段；路由不到则作为顶部 general error。 */
  const applyErrorFromCode = (rawCode: string, fallbackMsg: string) => {
    const code = (rawCode || '').trim()
    // 1) 已知 enum 错误码：直接用映射表 + i18n
    const enumField = ZIP_ERROR_TO_FIELD[code]
    const i18nKey = SKILL_ZIP_ERROR_KEYS[code]
    if (enumField || i18nKey) {
      const msg = i18nKey ? t(i18nKey) : code || fallbackMsg
      if (enumField) {
        setFieldErrors(prev => ({ ...prev, [enumField]: msg }))
      } else {
        setGeneralError(msg)
      }
      return
    }
    // 2) 人类可读消息（通常来自后端）：用关键词启发式路由到具体字段
    const msg = code || fallbackMsg
    const heuristicField = detectFieldByKeyword(msg)
    if (heuristicField) {
      setFieldErrors(prev => ({ ...prev, [heuristicField]: msg }))
    } else {
      setGeneralError(msg)
    }
  }

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
          setGeneralError('')
          // 打包成功清掉上一次打包遗留的字段错误，避免用户已修正后还留红。
          setFieldErrors({})
        } catch (e) {
          if (cancelled) return
          setFile(null)
          const code = e instanceof Error ? e.message : ''
          applyErrorFromCode(code, t('publish.skillPackFailed'))
        } finally {
          if (!cancelled) setPacking(false)
        }
      })()
    }, 400)

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
          setGeneralError('')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChecksum('')
          setGeneralError(t('publish.hashFailed'))
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
    setGeneralError('')
    setFieldErrors({})
    setSuccessMsg('')
    try {
      const login = user?.login?.trim()
      if (!login || !skillFolderFiles?.length) {
        setGeneralError(t('publish.uploadFailed'))
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
      setFieldErrors({})
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
      const msg = err instanceof Error ? err.message : ''
      applyErrorFromCode(msg, t('publish.uploadFailed'))
      // 用户可能已滑到底部看不到字段错误：提交失败后主动滚动到第一个错误字段。
      scrollToFirstError()
    } finally {
      setUploading(false)
    }
  }

  /** 用户点击成功弹框的「确认」：先关弹框再关抽屉，保证 UI 顺序流畅。 */
  const handleSuccessConfirm = () => {
    setSuccessMsg('')
    onCancel()
  }

  /** 成功弹框打开时禁用 Escape 关抽屉的副作用：仅响应模态内的 Escape 等同于确认。 */
  useEffect(() => {
    if (!successMsg) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault()
        e.stopPropagation()
        handleSuccessConfirm()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [successMsg])

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

  /** 错误汇总条使用：按表单顺序排列字段错误的数量与可读标签。 */
  const fieldErrorCount = PUBLISH_FIELD_ORDER.reduce(
    (n, k) => (fieldErrors[k] ? n + 1 : n),
    0,
  )
  const fieldErrorLabels = PUBLISH_FIELD_ORDER.filter(k => fieldErrors[k]).map(
    k => t(FIELD_LABEL_KEYS[k]),
  )

  return (
    <>
    <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col" aria-busy={uploading || packing || hashing}>
      <div
        className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pb-6 pt-4 sm:px-7"
        data-publish-form-scroll
      >
        {/* 顶部横幅仅展示「无法归属到字段」的错误；字段级错误显示在字段下方。 */}
        {generalError ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] leading-5 text-rose-800">
            {generalError}
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
            error={fieldErrors.skillPkgName}
            fieldKey="skillPkgName"
          >
            <input
              id={skillNameId}
              type="text"
              className={inputCls(Boolean(fieldErrors.skillPkgName))}
              value={skillPkgName}
              onChange={e => {
                setSkillPkgName(e.target.value)
                clearFieldError('skillPkgName')
              }}
              disabled={skillMetadataLocked}
              placeholder="my-demo-skill"
              required
              aria-invalid={Boolean(fieldErrors.skillPkgName)}
            />
          </Field>

          <Field
            htmlFor={skillVersionId}
            label={t('publish.fieldVersionSkill')}
            required
            hint={t('publish.fieldVersionSkillHelp')}
            error={fieldErrors.pluginVersion}
            fieldKey="pluginVersion"
          >
            <input
              id={skillVersionId}
              type="text"
              className={inputCls(Boolean(fieldErrors.pluginVersion))}
              value={pluginVersion}
              onChange={e => {
                setPluginVersion(e.target.value)
                clearFieldError('pluginVersion')
              }}
              placeholder="1.0.0"
              required
              aria-invalid={Boolean(fieldErrors.pluginVersion)}
            />
          </Field>
        </div>

        <Field
          htmlFor={skillDisplayNameId}
          label={t('publish.fieldSkillDisplayName')}
          required
          hint={t('publish.fieldSkillDisplayNameHelp')}
          error={fieldErrors.skillDisplayName}
          fieldKey="skillDisplayName"
        >
          <input
            id={skillDisplayNameId}
            type="text"
            className={inputCls(Boolean(fieldErrors.skillDisplayName))}
            value={skillDisplayName}
            onChange={e => {
              setSkillDisplayName(e.target.value)
              clearFieldError('skillDisplayName')
            }}
            disabled={skillMetadataLocked}
            placeholder={t('publish.fieldSkillDisplayName')}
            required
            aria-invalid={Boolean(fieldErrors.skillDisplayName)}
          />
        </Field>

        <Field
          htmlFor={skillDescriptionId}
          label={t('publish.fieldSkillDescription')}
          hint={t('publish.fieldSkillDescriptionHelp')}
          error={fieldErrors.skillDescription}
          fieldKey="skillDescription"
        >
          <textarea
            id={skillDescriptionId}
            className={textareaCls(Boolean(fieldErrors.skillDescription))}
            rows={3}
            value={skillDescription}
            onChange={e => {
              setSkillDescription(e.target.value)
              clearFieldError('skillDescription')
            }}
            disabled={skillMetadataLocked}
            placeholder={t('publish.fieldSkillDescriptionPlaceholder')}
            aria-invalid={Boolean(fieldErrors.skillDescription)}
          />
        </Field>

        <Field
          htmlFor={skillTagsId}
          label={t('publish.fieldSkillTags')}
          hint={t('publish.fieldSkillTagsHelp')}
          error={fieldErrors.skillTags}
          fieldKey="skillTags"
        >
          <input
            id={skillTagsId}
            type="text"
            className={inputCls(Boolean(fieldErrors.skillTags))}
            value={skillTagsInput}
            onChange={e => {
              setSkillTagsInput(e.target.value)
              clearFieldError('skillTags')
            }}
            disabled={skillMetadataLocked}
            placeholder="tag1, tag2"
            aria-invalid={Boolean(fieldErrors.skillTags)}
          />
        </Field>

        {/* Skill folder drop zone */}
        <Field
          label={t('publish.fieldSkillFolder')}
          required
          hint={t('publish.fieldSkillFolderHelp')}
          error={fieldErrors.skillFolder}
          fieldKey="skillFolder"
        >
          <input
            key={skillFolderInputKey}
            id={skillFolderInputId}
            type="file"
            className="sr-only"
            // @ts-expect-error webkitdirectory 非标准属性，用于选择文件夹
            webkitdirectory=""
            multiple
            onClick={e => {
              // 允许用户重复选择同一目录时也触发 change，拿到最新文件快照。
              e.currentTarget.value = ''
            }}
            onChange={e => {
              const list = e.target.files
              setSkillFolderFiles(list && list.length ? Array.from(list) : null)
              clearFieldError('skillFolder')
            }}
          />
          <label
            htmlFor={skillFolderInputId}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-center transition-colors ${
              fieldErrors.skillFolder
                ? 'border-rose-300 bg-rose-50/40 hover:border-rose-400'
                : skillFolderFiles?.length
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
        <Field
          label={t('publish.fieldSkillIcon')}
          hint={t('publish.fieldSkillIconHelp')}
          error={fieldErrors.skillIcon}
          fieldKey="skillIcon"
        >
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
                clearFieldError('skillIcon')
                return
              }
              const isPng =
                f.type === 'image/png' || f.name.toLowerCase().endsWith('.png')
              if (!isPng) {
                setSkillIconFile(null)
                setFieldErrors(prev => ({ ...prev, skillIcon: t('publish.skillErrorIconNotPng') }))
                setSkillIconInputKey(k => k + 1)
                return
              }
              if (f.size > 5 * 1024 * 1024) {
                setSkillIconFile(null)
                setFieldErrors(prev => ({ ...prev, skillIcon: t('publish.skillErrorIconTooLarge') }))
                setSkillIconInputKey(k => k + 1)
                return
              }
              clearFieldError('skillIcon')
              setSkillIconFile(f)
            }}
          />
          <div
            className={`flex items-center gap-3 rounded-xl p-1 ${
              fieldErrors.skillIcon ? 'bg-rose-50/50 ring-1 ring-rose-200' : ''
            }`}
          >
            <label
              htmlFor={skillIconInputId}
              className={`group relative inline-flex h-[72px] w-[72px] shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-xl border border-dashed transition-colors ${
                fieldErrors.skillIcon
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
                    fieldErrors.skillIcon
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

      {/*
       * 错误汇总条：当存在字段错误时紧贴 footer 上方常驻显示，解决「用户滑到底部点提交、
       * 但错误提示散落在上方看不到」的问题；点击可直接跳转到第一个错误字段。
       */}
      {fieldErrorCount > 0 ? (
        <div className="shrink-0 border-t border-rose-100 bg-rose-50/90 px-4 py-2.5 sm:px-7">
          <button
            type="button"
            onClick={() => scrollToFirstError()}
            className="group flex w-full items-center justify-between gap-3 text-left"
          >
            <span className="flex min-w-0 items-center gap-2 text-[12.5px] leading-5 text-rose-700">
              <span
                aria-hidden
                className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-100 text-rose-600"
              >
                ⚠
              </span>
              <span className="truncate">
                {t('publish.fieldErrorsSummary', { count: fieldErrorCount })}
                <span className="text-rose-500/80">
                  {' · '}
                  {fieldErrorLabels.join(', ')}
                </span>
              </span>
            </span>
            <span className="shrink-0 text-[12px] font-medium text-rose-600 group-hover:underline">
              {t('publish.jumpToFirstError')}
            </span>
          </button>
        </div>
      ) : null}

      <footer className="shrink-0 border-t border-slate-100 bg-white/95 px-4 py-4 backdrop-blur sm:px-7">
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

    {successMsg ? (
      <div
        className="fixed inset-0 z-[60] flex items-center justify-center px-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-success-dialog-title"
      >
        <div className="absolute inset-0 bg-slate-900/55 backdrop-blur-[2px] animate-notice-in" />
        <div className="relative w-full max-w-[420px] overflow-hidden rounded-2xl border border-white/60 bg-white shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)] animate-notice-in">
          <span
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-[#1E54F9] via-[#6B5BFF] to-[#852EFE]"
          />
          <div className="flex flex-col items-center gap-3 px-6 pt-8 pb-5 text-center">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 ring-1 ring-emerald-100">
              <CheckCircle2 className="h-7 w-7 text-emerald-600" aria-hidden />
            </span>
            <h3
              id="publish-success-dialog-title"
              className="text-[16px] font-semibold text-[#0F172A]"
            >
              {t('publish.successDialogTitle')}
            </h3>
            <p className="text-[13px] leading-[1.6] text-[#475569]">
              {successMsg}
            </p>
            <p className="text-[12px] leading-[1.5] text-[#94A3B8]">
              {t('publish.successDialogHint')}
            </p>
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-slate-100 bg-[#FAFBFF] px-5 py-3">
            <button
              type="button"
              onClick={handleSuccessConfirm}
              autoFocus
              className="inline-flex h-9 min-w-[92px] items-center justify-center rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-sm font-medium text-white shadow-[0_6px_16px_-6px_rgba(30,84,249,0.55)] transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
            >
              {t('publish.successDialogConfirm')}
            </button>
          </div>
        </div>
      </div>
    ) : null}
    </>
  )
}

export default PublishForm
