// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Send, Loader2, Terminal, ChevronDown, ChevronRight, Square } from 'lucide-react'
import {
  createPlaygroundSession,
  sendPlaygroundMessage,
  openPlaygroundStream,
  endPlaygroundSession,
  getPlaygroundQuota,
  type SseEvent,
  type PlaygroundQuota,
} from '@/api/playground'
import { API_CONFIG, API_ENDPOINTS } from '@/api/config'
import { getStoredOAuthToken } from '@/auth/gitcodeStorage'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Message {
  role: 'user' | 'assistant' | 'reasoning' | 'tool' | 'milestone'
  content: string
  agentRole?: 'leader' | 'teammate'
  memberName?: string
  toolName?: string
  toolInput?: string
  toolResult?: string
  milestoneKind?: 'team_ready' | 'team_complete'
  // 已封口：本轮（一段连续同源输出）结束，后续同源文本另起新气泡。
  // 镜像 openjiuwen 流式分段，避免同一成员多轮被粘成一坨。
  closed?: boolean
  // team_complete 之后 leader 产出的最终整合方案，渲染时突出展示。
  final?: boolean
}

interface TeamProgress {
  leader?: 'done' | 'active' | 'pending'
  teammates: Record<string, 'done' | 'active' | 'pending'>
}

interface Props {
  open: boolean
  skillId: string
  version: string
  skillType: 'ordinary' | 'swarm'
  skillName: string
  onClose: () => void
}

// ── Design tokens ──────────────────────────────────────────────────────────────

const C = {
  bg:           '#ffffff',
  bgAlt:        '#f7f8fa',
  surface:      '#f4f6f9',
  border:       'rgba(0,0,0,0.10)',
  borderSubtle: 'rgba(0,0,0,0.06)',
  text:         '#1d2939',
  textMuted:    '#667085',
  textFaint:    '#98a2b3',
  accent:       '#2563eb',
  accentDark:   '#1d4ed8',
  accentBg:     '#eff6ff',
  purple:       '#7c3aed',
  purpleBg:     '#f5f3ff',
  green:        '#15803d',
  greenBg:      '#f0fdf4',
  amber:        '#92400e',
  amberBg:      '#fffbeb',
  amberBorder:  '#fde68a',
  red:          '#b91c1c',
  redBg:        '#fef2f2',
  userGrad:     'linear-gradient(135deg, #2563eb 0%, #4f46e5 100%)',
  mono:         "'JetBrains Mono','Cascadia Code','Fira Code','Consolas',monospace",
}

// ── Team member identity ─────────────────────────────────────────────────────────
// Each teammate gets a stable color (hashed from name) so "who said/did what" is
// scannable at a glance; the leader keeps a fixed purple. Used for the colored rail
// + name chip on every assistant / reasoning / tool block in SWARM mode.

const MEMBER_PALETTE = [
  '#0891b2', '#c026d3', '#ea580c', '#0d9488',
  '#db2777', '#65a30d', '#4f46e5', '#b45309',
]

function hashStr(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) >>> 0
  return h
}

interface MemberMeta { name: string; label: string; icon: string; color: string }

function memberMeta(memberName?: string, agentRole?: 'leader' | 'teammate'): MemberMeta | null {
  const isLeader = memberName === 'leader' || (!memberName && agentRole === 'leader')
  if (isLeader) return { name: 'leader', label: 'Leader', icon: '⬡', color: C.purple }
  if (!memberName) return null
  const pretty = memberName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
  return { name: memberName, label: pretty, icon: '◇', color: MEMBER_PALETTE[hashStr(memberName) % MEMBER_PALETTE.length] }
}

// ── CSS injected once ──────────────────────────────────────────────────────────

const INJECTED_CSS = `
@keyframes pg-slide {
  from { transform: translateX(20px); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
}
@keyframes pg-msg {
  from { transform: translateY(5px); opacity: 0; }
  to   { transform: translateY(0);   opacity: 1; }
}
@keyframes pg-spin {
  to { transform: rotate(360deg); }
}
@keyframes pg-pulse {
  0%,100% { opacity: 1; }
  50%      { opacity: 0.3; }
}
@keyframes pg-dot {
  0%,80%,100% { transform: translateY(0);    opacity: 0.25; }
  40%         { transform: translateY(-4px); opacity: 1; }
}

.pg-drawer { animation: pg-slide 0.2s cubic-bezier(0.16,1,0.3,1) both; }
.pg-msg    { animation: pg-msg 0.14s ease-out both; }
.pg-spin   { animation: pg-spin 0.8s linear infinite; }
.pg-pulse  { animation: pg-pulse 1.6s ease-in-out infinite; }
.pg-d1     { animation: pg-dot 1.2s 0.00s ease-in-out infinite; }
.pg-d2     { animation: pg-dot 1.2s 0.18s ease-in-out infinite; }
.pg-d3     { animation: pg-dot 1.2s 0.36s ease-in-out infinite; }

.pg-scroll::-webkit-scrollbar       { width: 4px; }
.pg-scroll::-webkit-scrollbar-track { background: transparent; }
.pg-scroll::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.18); border-radius: 2px; }
.pg-scroll::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.30); }

.pg-icon-btn {
  display:flex; align-items:center; justify-content:center;
  width:28px; height:28px; border-radius:6px;
  border:1px solid transparent; background:transparent;
  color:#98a2b3; cursor:pointer; transition:all 0.12s;
}
.pg-icon-btn:hover { background:#f0f2f5; border-color:rgba(0,0,0,0.10); color:#344054; }

.pg-ta { font-family:inherit; resize:none; border-radius:8px;
  border:1px solid rgba(0,0,0,0.15); background:#fff; color:#1d2939;
  padding:10px 13px; font-size:14px; line-height:1.55; outline:none;
  transition:border-color 0.15s, box-shadow 0.15s; width:100%; }
.pg-ta:focus { border-color:#2563eb; box-shadow:0 0 0 3px rgba(37,99,235,0.10); }
.pg-ta::placeholder { color:#98a2b3; }
.pg-ta:disabled { opacity:0.5; cursor:not-allowed; background:#f7f8fa; }

.pg-send {
  display:flex; align-items:center; justify-content:center;
  width:40px; height:40px; border-radius:8px; border:none;
  cursor:pointer; transition:all 0.15s; flex-shrink:0;
}
.pg-send:not(:disabled) { background:linear-gradient(135deg,#2563eb,#4f46e5); color:#fff; box-shadow:0 1px 4px rgba(37,99,235,0.30); }
.pg-send:not(:disabled):hover { filter:brightness(1.08); box-shadow:0 2px 8px rgba(37,99,235,0.40); }
.pg-send:disabled { background:#e9ecef; color:#adb5bd; cursor:not-allowed; }

.pg-stop {
  display:flex; align-items:center; gap:5px;
  padding:4px 10px; border-radius:6px;
  background:#fff0f0; border:1px solid #fecdca;
  color:#b91c1c; font-size:12px; font-weight:600;
  cursor:pointer; transition:all 0.12s;
}
.pg-stop:hover { background:#fee2e2; }
`

let _injected = false
function ensureStyles() {
  if (_injected || typeof document === 'undefined') return
  _injected = true
  const el = document.createElement('style')
  el.textContent = INJECTED_CSS
  document.head.appendChild(el)
}

// ── Tool summary helpers ───────────────────────────────────────────────────────

function toolSummaryLine(
  name: string,
  toolInput: string | undefined,
  running: boolean,
): { label: string; hint?: string; orchestration?: boolean } {
  let args: Record<string, unknown> = {}
  try { if (toolInput) args = JSON.parse(toolInput) } catch {}
  const lname = name.toLowerCase()
  const path = (args.path ?? args.file_path ?? args.filename ?? args.filepath) as string | undefined
  const cmd  = (args.command ?? args.cmd ?? args.script) as string | undefined
  const q    = (args.pattern ?? args.query ?? args.q ?? args.glob ?? args.prompt) as string | undefined
  const url  = args.url as string | undefined

  const verb = (done: string, doing: string) => running ? doing : done

  // ── Team orchestration tools (SWARM): surface the collaboration backbone ──
  const str = (v: unknown) => (typeof v === 'string' ? v : v != null ? JSON.stringify(v) : undefined)
  const member = str(args.member ?? args.member_name ?? args.to ?? args.assignee ?? args.name)
  const taskTitle = str(args.title ?? args.task ?? args.task_title ?? args.description ?? args.content)
  if (lname.includes('build_team'))
    return { label: verb('🛠 Built team', '🛠 Building team'), hint: str(args.members ?? args.roles ?? args.team), orchestration: true }
  if (lname.includes('spawn') && (lname.includes('teammate') || lname.includes('member') || lname.includes('agent')))
    return { label: verb('➕ Added member', '➕ Adding member'), hint: member, orchestration: true }
  if (lname.includes('create_task'))
    return { label: verb('📋 Created task', '📋 Creating task'), hint: taskTitle, orchestration: true }
  if (lname.includes('send_message') || lname === 'delegate')
    return { label: verb('📨 Delegated', '📨 Delegating'), hint: member ? `→ ${member}` : taskTitle, orchestration: true }
  if (lname.includes('claim_task'))  return { label: '📥 Claimed task', hint: taskTitle, orchestration: true }
  if (lname.includes('update_task')) return { label: '✏️ Updated task', hint: str(args.status) ?? taskTitle, orchestration: true }
  if (lname.includes('view_task'))   return { label: '👁 Viewed task', hint: taskTitle, orchestration: true }
  if (lname.includes('list_members')) return { label: '👥 Listed members', orchestration: true }
  if (lname.includes('shutdown_member')) return { label: '⏏ Released member', hint: member, orchestration: true }

  if (lname.includes('list'))   return { label: verb('Listed directories', 'Listing directories'), hint: (args.path as string) ?? '.' }
  if (lname.includes('read'))   return { label: verb('Read file', 'Reading file'), hint: path }
  if (lname.includes('write') || lname.includes('create_file'))
                                return { label: verb('Wrote file', 'Writing file'), hint: path }
  if (lname.includes('edit') || lname.includes('search_replace'))
                                return { label: verb('Edited file', 'Editing file'), hint: path }
  if (lname.includes('bash') || lname.includes('run') || lname.includes('exec'))
                                return { label: verb('Ran command', 'Running command'), hint: cmd ? cmd.split('\n')[0].slice(0, 80) : undefined }
  if (lname.includes('search') || lname.includes('grep'))
                                return { label: verb('Searched', 'Searching'), hint: q }
  if (lname.includes('glob'))   return { label: verb('Glob search', 'Glob searching'), hint: q }
  if (lname.includes('fetch') || lname.includes('web'))
                                return { label: verb('Fetched page', 'Fetching page'), hint: url }
  return { label: name, hint: undefined }
}

function isWriteTool(name: string): boolean {
  const lname = name.toLowerCase()
  return ['write', 'edit', 'search_replace', 'create_file'].some(t => lname.includes(t))
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function sendMessageSpeech(toolName: string | undefined, toolInput: string | undefined): { to: string; content: string } | null {
  if (!toolName || !/send_?message|delegate/i.test(toolName)) return null
  try {
    const a = JSON.parse(toolInput ?? '{}') as Record<string, unknown>
    const content = typeof a.content === 'string' ? a.content.trim() : ''
    if (!content) return null
    const rawTo = a.to
    const toStr = typeof rawTo === 'string' ? rawTo : Array.isArray(rawTo) ? rawTo.join(', ') : ''
    const to = toStr === '*' ? '全体' : toStr === 'team_leader' ? '桌长' : toStr || '—'
    return { to, content }
  } catch { return null }
}

function ToolMessage({ msg, running }: { msg: Message; running: boolean }) {
  const hasError = !!(msg.toolResult?.startsWith('exit 1') || msg.toolResult?.startsWith('exit 12'))
  const { label, hint, orchestration } = toolSummaryLine(msg.toolName ?? 'tool', msg.toolInput, running)
  const defaultExpanded = isWriteTool(msg.toolName ?? '') || hasError || !!orchestration
  const [expanded, setExpanded] = useState(defaultExpanded)

  const member = memberMeta(msg.memberName, msg.agentRole)
  const speech = sendMessageSpeech(msg.toolName, msg.toolInput)
  const accentColor = hasError ? C.red : orchestration ? (member?.color ?? C.purple) : C.accent

  const hintShort = hint && hint.length > 52 ? '…' + hint.slice(-48) : hint

  return (
    <div className="pg-msg" style={{
      maxWidth: '95%',
      border: `1px solid ${C.border}`,
      borderLeft: member ? `3px solid ${member.color}` : `1px solid ${C.border}`,
      borderRadius: 8,
      overflow: 'hidden',
      fontSize: 12,
    }}>
      <button
        onClick={() => setExpanded(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          width: '100%', padding: '7px 12px',
          background: orchestration ? `${accentColor}0d` : C.surface,
          cursor: 'pointer', textAlign: 'left',
          borderTop: `2px solid ${accentColor}`,
        }}
      >
        <Terminal size={10} style={{ color: accentColor, flexShrink: 0 }} />
        <span style={{ fontFamily: C.mono, color: accentColor, fontWeight: 600, fontSize: 11 }}>
          {label}
        </span>
        {!expanded && hintShort && (
          <span style={{ fontFamily: C.mono, color: C.textFaint, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {hintShort}
          </span>
        )}
        {member && (
          <span style={{
            flexShrink: 0, background: `${member.color}14`, color: member.color,
            padding: '1px 6px', borderRadius: 4, fontSize: 10, fontFamily: C.mono,
            border: `1px solid ${member.color}40`,
          }}>
            {member.icon} {member.label}
          </span>
        )}
        {running && !expanded && (
          <Loader2 size={10} className="pg-spin" style={{ color: accentColor, flexShrink: 0 }} />
        )}
        <span style={{ marginLeft: 'auto', color: C.textFaint, flexShrink: 0 }}>
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>
      </button>

      {expanded && (
        <div style={{ background: C.bg, borderTop: `1px solid ${C.borderSubtle}` }}>
          {/* 空入参（{} / [] / 空串）不渲染——否则只显示一个孤零零的中括号 */}
          {speech ? (
            <div style={{ padding: '8px 12px', borderBottom: `1px solid ${C.borderSubtle}` }}>
              <div style={{ fontSize: 10, color: C.textFaint, marginBottom: 4, fontFamily: C.mono }}>→ {speech.to}</div>
              <div style={{ fontSize: 13.5, lineHeight: 1.6, color: C.text, wordBreak: 'break-word' }}>
                <PluginMarkdown source={speech.content} />
              </div>
            </div>
          ) : (() => {
            const ti = (msg.toolInput ?? '').trim()
            return ti && ti !== '{}' && ti !== '[]'
          })() && (
            <div className="pg-scroll" style={{
              padding: '8px 12px',
              fontFamily: C.mono, color: C.textMuted,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              maxHeight: 160, overflowY: 'auto',
              borderBottom: `1px solid ${C.borderSubtle}`,
              fontSize: 11, lineHeight: 1.55,
            }}>
              {msg.toolInput}
            </div>
          )}
          {msg.toolResult !== undefined ? (
            <div className="pg-scroll" style={{
              padding: '8px 12px',
              fontFamily: C.mono,
              color: hasError ? C.red : C.green,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              maxHeight: 180, overflowY: 'auto',
              fontSize: 11, lineHeight: 1.55,
            }}>
              {msg.toolResult || (
                <span style={{ color: C.textFaint, fontStyle: 'italic' }}>no output</span>
              )}
            </div>
          ) : (
            <div style={{
              padding: '8px 12px',
              display: 'flex', alignItems: 'center', gap: 7, color: C.textMuted,
            }}>
              <Loader2 size={11} className="pg-spin" />
              <span style={{ fontFamily: C.mono, fontSize: 11 }}>running…</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ReasoningMessage({ content, streaming }: { content: string; streaming?: boolean; msg?: Message }) {
  const [open, setOpen] = useState(false)
  const lineCount = content.split('\n').filter(l => l.trim()).length
  // collapsed preview: first non-empty line, so "Thinking" is no longer just "N lines"
  const preview = content.split('\n').map(l => l.trim()).find(Boolean) ?? ''
  const previewShort = preview.length > 60 ? preview.slice(0, 58) + '…' : preview

  return (
    <div className="pg-msg" style={{
      maxWidth: '92%',
      border: `1px solid ${C.amberBorder}`,
      borderRadius: 8, overflow: 'hidden', fontSize: 12,
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          width: '100%', padding: '7px 12px',
          background: C.amberBg, cursor: 'pointer', textAlign: 'left',
        }}
      >
        <span style={{ fontSize: 11, color: C.amber }}>✦</span>
        <span style={{ color: C.amber, fontWeight: 600, fontSize: 11, fontStyle: 'italic', flexShrink: 0 }}>
          {streaming ? 'Thinking…' : 'Thinking'}
        </span>
        {!open && !streaming && previewShort && (
          <span style={{ color: `${C.amber}aa`, fontSize: 11, fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {previewShort}
          </span>
        )}
        {!open && !streaming && !previewShort && lineCount > 0 && (
          <span style={{ color: C.textFaint, fontSize: 10, marginLeft: 4 }}>
            {lineCount} lines
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: C.textFaint }}>
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>
      </button>
      {open && (
        <div className="pg-scroll" style={{
          padding: '9px 13px',
          background: C.amberBg,
          color: `${C.amber}99`,
          fontStyle: 'italic',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          lineHeight: 1.65, maxHeight: 240, overflowY: 'auto',
          fontSize: 11,
        }}>
          {content}
        </div>
      )}
    </div>
  )
}

// Leader 编排播报（看板状态/任务分派的自言自语）默认折叠弱化，与「最终方案」拉开层次：
// 用户先看成员汇报，编排过程收起，需要时再展开。镜像 ReasoningMessage 的折叠模式，紫色主题。
function OrchestrationMessage({ content }: { content: string }) {
  const [open, setOpen] = useState(false)
  const preview = content.split('\n').map(l => l.trim()).find(Boolean) ?? ''
  const previewShort = preview.length > 60 ? preview.slice(0, 58) + '…' : preview
  return (
    <div className="pg-msg" style={{
      maxWidth: '92%',
      border: `1px solid ${C.purple}30`,
      borderRadius: 8, overflow: 'hidden', fontSize: 12,
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          width: '100%', padding: '7px 12px',
          background: C.purpleBg, cursor: 'pointer', textAlign: 'left',
        }}
      >
        <span style={{ fontSize: 11, color: C.purple }}>⬡</span>
        <span style={{ color: C.purple, fontWeight: 600, fontSize: 11, flexShrink: 0 }}>
          Leader 编排
        </span>
        {!open && previewShort && (
          <span style={{ color: `${C.purple}aa`, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {previewShort}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: C.textFaint }}>
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>
      </button>
      {open && (
        <div className="pg-scroll" style={{
          padding: '9px 13px',
          background: C.purpleBg,
          color: C.textMuted,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          lineHeight: 1.6, maxHeight: 240, overflowY: 'auto',
          fontSize: 12,
        }}>
          {content}
        </div>
      )}
    </div>
  )
}

function TypingDots() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 2px' }}>
      {(['pg-d1', 'pg-d2', 'pg-d3'] as const).map(cls => (
        <div key={cls} className={cls} style={{
          width: 5, height: 5, borderRadius: '50%', background: C.textMuted,
        }} />
      ))}
    </div>
  )
}

// 从后端错误响应体里取人类可读的 message。
// marketplace 全局异常处理器把错误拍平到顶层（{message, error, ...}）；
// FastAPI 默认形态则是 {detail: {message}} 或 {detail: "..."}——全部兼容。
function serverMessage(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return typeof data === 'string' ? data : undefined
  const d = data as Record<string, unknown>
  if (typeof d.message === 'string') return d.message
  const detail = d.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string')
    return (detail as Record<string, unknown>).message as string
  return undefined
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PlaygroundDrawer({ open, skillId, version, skillType, skillName, onClose }: Props) {
  const { t } = useTranslation()

  useEffect(() => { ensureStyles() }, [])

  const [sessionId, setSessionId] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'starting' | 'ready' | 'error'>('idle')
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')
  const [teamProgress, setTeamProgress] = useState<TeamProgress>({ teammates: {} })
  const [quota, setQuota] = useState<PlaygroundQuota | null>(null)
  const [tokens, setTokens] = useState(0)  // 本会话累计 token 用量（usage 事件实时累加）
  // viewMode: narrative(默认,只看对话) / developer(显示 tool + reasoning)
  const [viewMode, setViewMode] = useState<'developer' | 'narrative'>(() => {
    try { return (localStorage.getItem('playground.viewMode') as 'developer' | 'narrative') || 'narrative' } catch { return 'narrative' }
  })
  useEffect(() => { try { localStorage.setItem('playground.viewMode', viewMode) } catch {} }, [viewMode])
  const esRef = useRef<EventSource | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)  // 是否自动贴底；用户向上滚动阅读时置 false，不再被新内容拽下去
  const sawProxyUsageRef = useRef(false)  // 见过控制面权威 token 后，忽略 worker 自报，避免双计
  const currentMemberRef = useRef<string | 'leader'>('leader')
  const teamCompletedRef = useRef(false)  // 见过 team_complete 后，leader 后续产出视为最终方案
  const deltaBufferRef = useRef<{ agentRole?: 'leader' | 'teammate'; memberName?: string; text: string }>({ text: '' })
  const deltaTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const appendAssistant = useCallback((text: string, agentRole?: 'leader' | 'teammate', memberName?: string) => {
    setMessages(prev => {
      // 从后往前找「本成员自己最近一条未封口 assistant 气泡」合并。
      // 只在两处停下：① user 消息；② 撞到本成员自己上一轮「已封口」的气泡（本轮另起新气泡）。
      // 别人的气泡——无论开还是封——一律跳过继续往前找。
      // 并发流式（swarm 多成员同时说话）下，别的成员刚封口的气泡会插在本成员未封口气泡
      // 之前；若在「别人的已封口气泡」处就 break，会把本成员的话从句子中间切断、另起新气泡，
      // 正是「气泡频繁半句截断 + 碎成一堆」的根因。
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i]
        if (m.role === 'user') break
        if (m.role === 'assistant' && m.agentRole === agentRole && m.memberName === memberName) {
          if (m.closed) break  // 本成员上一轮已封口 → 本轮另起新气泡
          const updated = [...prev]
          updated[i] = { ...m, content: m.content + text }
          return updated
        }
        // 其它成员的气泡（开/封）、tool / reasoning / milestone：跳过继续找
      }
      const final = teamCompletedRef.current && agentRole === 'leader'
      return [...prev, { role: 'assistant', content: text, agentRole, memberName, final }]
    })
  }, [])

  const flushDeltaBuffer = useCallback(() => {
    if (!deltaBufferRef.current.text) return
    const { text, agentRole, memberName } = deltaBufferRef.current
    deltaBufferRef.current = { text: '' }
    if (deltaTimeoutRef.current) { clearTimeout(deltaTimeoutRef.current); deltaTimeoutRef.current = null }
    appendAssistant(text, agentRole, memberName)
  }, [appendAssistant])

  const appendReasoning = useCallback((text: string, agentRole?: 'leader' | 'teammate', memberName?: string) => {
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (last?.role === 'reasoning' && last.agentRole === agentRole && last.memberName === memberName)
        return [...prev.slice(0, -1), { ...last, content: last.content + text }]
      return [...prev, { role: 'reasoning', content: text, agentRole, memberName }]
    })
  }, [])

  const handleEvent = useCallback((ev: SseEvent) => {
    if (ev.role) {
      if (ev.role === 'leader') {
        currentMemberRef.current = 'leader'
      } else if (ev.member) {
        currentMemberRef.current = ev.member
        setTeamProgress(prev => {
          if (!prev.teammates[ev.member!])
            return { ...prev, teammates: { ...prev.teammates, [ev.member!]: 'active' } }
          return prev
        })
      }
    }

    if (ev.type === 'team_ready') {
      setTeamProgress(prev => ({ ...prev, leader: 'active' }))
      setMessages(prev => [...prev, {
        role: 'milestone', milestoneKind: 'team_ready',
        content: (ev.team_name as string) || '',
      }])
    } else if (ev.type === 'team_complete') {
      teamCompletedRef.current = true  // 此后 leader 产出 = 最终整合方案
      setTeamProgress(prev => ({ ...prev, leader: 'done' }))
      const mc = ev.member_count as number | undefined
      const tc = ev.task_count as number | undefined
      setMessages(prev => [...prev, {
        role: 'milestone', milestoneKind: 'team_complete',
        content: [mc != null ? `${mc} members` : '', tc != null ? `${tc} tasks` : ''].filter(Boolean).join(' · '),
      }])
    }

    if (ev.type === 'ready') {
      setStatus('ready')
    } else if (ev.type === 'text' && ev.delta) {
      const agentRole = ev.role as 'leader' | 'teammate' | undefined
      const memberName = ev.member as string | undefined
      if (deltaBufferRef.current.text &&
          (deltaBufferRef.current.agentRole !== agentRole || deltaBufferRef.current.memberName !== memberName))
        flushDeltaBuffer()
      deltaBufferRef.current = { agentRole, memberName, text: deltaBufferRef.current.text + (ev.delta as string) }
      if (deltaBufferRef.current.text.length >= 100) {
        flushDeltaBuffer()
      } else {
        if (deltaTimeoutRef.current) clearTimeout(deltaTimeoutRef.current)
        deltaTimeoutRef.current = setTimeout(flushDeltaBuffer, 100)
      }
    } else if (ev.type === 'reasoning' && ev.delta) {
      appendReasoning(
        ev.delta as string,
        ev.role as 'leader' | 'teammate' | undefined,
        ev.member as string | undefined,
      )
    } else if (ev.type === 'answer' && ev.content) {
      flushDeltaBuffer()
      const ansRole = ev.role as 'leader' | 'teammate' | undefined
      const ansMember = ev.member as string | undefined
      const ansContent = ev.content as string
      const isFinal = teamCompletedRef.current && ansRole === 'leader'
      setMessages(prev => {
        // answer = 本轮最终整合输出（完整版）。由于 tool_call 可能把同一轮文本切成多个同源气泡，
        // 上一个版本只能从后往前 match 一个、close 后面那个，前面被切出的气泡仍 closed=false
        // 依然显示为「半截孤悬 + 完整重复」。现在：找出本轮所有同源 unclosed assistant 气泡，
        // 用 answer 完整内容写回最早那条、close 之，后续同源未封口气泡全部删除（内容被包含）。
        // 没找到（纯 answer 无 stream）则追加一个 closed 气泡。
        const sameSourceIdx: number[] = []
        for (let i = prev.length - 1; i >= 0; i--) {
          if (prev[i].role === 'user') break
          if (prev[i].role === 'assistant' && !prev[i].closed
            && prev[i].agentRole === ansRole && prev[i].memberName === ansMember) {
            sameSourceIdx.push(i)
          }
        }
        if (sameSourceIdx.length === 0) {
          return [...prev, { role: 'assistant', content: ansContent, agentRole: ansRole, memberName: ansMember, closed: true, final: isFinal }]
        }
        // sameSourceIdx 是按倒序 push 的，last 个 = 最早出现的
        const earliest = sameSourceIdx[sameSourceIdx.length - 1]
        const toDrop = new Set(sameSourceIdx.slice(0, -1))  // 除最早之外都丢
        const result: Message[] = []
        for (let i = 0; i < prev.length; i++) {
          if (i === earliest) {
            result.push({
              ...prev[i],
              content: ansContent,
              closed: true,
              final: prev[i].final || isFinal,
            })
          } else if (!toDrop.has(i)) {
            result.push(prev[i])
          }
          // else: drop (被 tool_call 切出来的后续同源副本)
        }
        return result
      })
    } else if (ev.type === 'usage') {
      if (typeof ev.session_total === 'number') {
        // 控制面代理的权威累计：直接 set（看得见每一次真实 LLM 调用）
        sawProxyUsageRef.current = true
        setTokens(ev.session_total)
      } else if (!sawProxyUsageRef.current) {
        // 回退：worker 自报的单次用量，仅在没有权威源时自增
        const inc = ev.total_tokens ?? ((ev.input_tokens ?? 0) + (ev.output_tokens ?? 0))
        if (inc > 0) setTokens(prev => prev + inc)
      }
    } else if (ev.type === 'tool_call') {
      // tool_call 会打断流式 text；先把 deltaBuffer 里没满 100 字、还在 100ms 防抖窗口内的
      // 尾巴 flush 到当前气泡，再 push 工具消息。否则那段字会在 tool 之后才落入新气泡，
      // 而那时上一条 message 已经是 tool，appendAssistant 会判断「last 不是 assistant」误新建气泡。
      flushDeltaBuffer()
      const rawInput = ev.input
      const inputStr = rawInput == null ? '' : typeof rawInput === 'string'
        ? rawInput : JSON.stringify(rawInput, null, 2)
      setMessages(prev => [...prev, {
        role: 'tool', toolName: (ev.name as string) || 'tool',
        toolInput: inputStr, content: '',
        agentRole: ev.role as 'leader' | 'teammate' | undefined,
        memberName: ev.member as string | undefined,
      }])
    } else if (ev.type === 'tool_result') {
      const rawOut = ev.output ?? ev.error
      const outStr = typeof rawOut === 'string' ? rawOut : rawOut != null ? JSON.stringify(rawOut, null, 2) : ''
      const result = ev.exit_code != null ? `exit ${ev.exit_code}\n${outStr}` : outStr
      const resMember = ev.member as string | undefined
      const resRole = ev.role as string | undefined
      setMessages(prev => {
        // Pair with the most recent unresolved tool call FROM THE SAME MEMBER. When
        // multiple SWARM members act in parallel their tool_call/tool_result events
        // interleave, so a global "last unresolved" pairing mis-attributes results.
        const matches = (m: Message) =>
          m.role === 'tool' && m.toolResult === undefined &&
          (resMember === undefined || (m.memberName === resMember && m.agentRole === resRole))
        let idx = [...prev].reverse().findIndex(matches)
        if (idx === -1 && resMember !== undefined)
          // fall back to any unresolved tool if no member match (defensive)
          idx = [...prev].reverse().findIndex(m => m.role === 'tool' && m.toolResult === undefined)
        if (idx !== -1) {
          const realIdx = prev.length - 1 - idx
          const updated = [...prev]
          updated[realIdx] = { ...updated[realIdx], toolResult: result }
          return updated
        }
        return [...prev, { role: 'tool', toolName: 'tool', content: '', toolResult: result }]
      })
    } else if (ev.type === 'session_ended') {
      // 控制面主动结束会话（空闲超时回收 / 被删）：落盘半截文字、停发、提示
      flushDeltaBuffer()
      setSending(false)
      esRef.current?.close(); esRef.current = null
      setStatus('error')
      setErrorMsg('会话已结束（空闲超时或被回收），请关闭重新开始体验')
    } else if (ev.type === 'error') {
      flushDeltaBuffer()
      setSending(false)
      esRef.current?.close(); esRef.current = null
      setStatus('error')
      setErrorMsg((ev.message as string) || '执行出错，请关闭重新开始体验')
    } else if (ev.type === 'done') {
      flushDeltaBuffer()
      setSending(false)
      setTeamProgress(prev => {
        const u = { ...prev, teammates: { ...prev.teammates } }
        if (u.leader === 'active') u.leader = 'done'
        Object.keys(u.teammates).forEach(n => { if (u.teammates[n] === 'active') u.teammates[n] = 'done' })
        return u
      })
    }
  }, [appendAssistant, appendReasoning, flushDeltaBuffer])

  useEffect(() => {
    if (!open) return
    setStatus('starting')
    teamCompletedRef.current = false
    setMessages([])
    setErrorMsg('')
    setSessionId(null)
    setTokens(0)
    sawProxyUsageRef.current = false

    if (!getStoredOAuthToken()) {
      setStatus('error')
      setErrorMsg('请先登录后再使用 Playground')
      return
    }

    // Fetch quota in parallel with session creation
    getPlaygroundQuota().then(setQuota).catch(() => {})

    let sid: string | undefined
    let cancelled = false

    createPlaygroundSession(skillId, version, skillType)
      .then(s => {
        sid = s.session_id
        if (cancelled) {
          // Drawer was closed while session was still being created; clean up the pod immediately
          endPlaygroundSession(sid).catch(() => {})
          return
        }
        setSessionId(sid)
        const es = openPlaygroundStream(sid, handleEvent, (ev) => {
          // session 不存在（404，如控制面重启清了内存）→ EventSource 置 CLOSED 不再重连：
          // 收尾并提示，别让浏览器对死 session 反复重连刷 404。瞬时网络抖动（CONNECTING）
          // 则交给 EventSource 自愈，不打断。
          const tgt = ev.target as EventSource | null
          if (tgt && tgt.readyState === EventSource.CLOSED) {
            tgt.close()
            if (esRef.current === tgt) esRef.current = null
            setSending(false)
            setStatus('error')
            setErrorMsg('会话已结束（服务可能重启或超时），请关闭重新开始体验')
          }
        })
        esRef.current = es
        // Refresh quota after successful session creation
        getPlaygroundQuota().then(setQuota).catch(() => {})
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStatus('error')
        const e = err as Record<string, unknown>
        const resp = e?.response as Record<string, unknown> | undefined
        if (resp?.status === 401) {
          setErrorMsg('登录已过期，请重新登录后再试')
        } else if (resp?.status === 429) {
          // marketplace 全局异常处理器把 detail 拍平到顶层（data.message），
          // 旧 FastAPI 默认形态则在 data.detail.message——两者都兼容读。
          setErrorMsg(
            serverMessage(resp.data) ?? '今日 Playground 试用次数已达上限，明日 0 点重置'
          )
          getPlaygroundQuota().then(setQuota).catch(() => {})
        } else if (resp?.status === 409) {
          setErrorMsg(serverMessage(resp.data) ?? '您已有一个活跃的 Playground 会话，请先结束当前会话再新建')
        } else if (resp?.status === 504) {
          setErrorMsg('启动超时：Pod 冷启动时间超出网关限制，请稍后重试')
        } else if (resp?.status === 502 || resp?.status === 503) {
          setErrorMsg(`服务暂时不可用 (${resp.status})，请稍后重试`)
        } else {
          let detail = ''
          if (resp) {
            const data = resp.data as Record<string, unknown> | string | undefined
            // nginx error pages are HTML — don't show raw HTML to the user
            const isHtml = typeof data === 'string' && data.trimStart().startsWith('<')
            if (!isHtml) {
              const raw = typeof data === 'object' && data
                ? (data.detail ?? data.message ?? data) : data
              const msg = typeof raw === 'string' ? raw : raw != null ? JSON.stringify(raw) : undefined
              detail = ` (${resp.status}${msg ? ': ' + msg : ''})`
            } else {
              detail = ` (${resp.status})`
            }
          } else if (typeof e.message === 'string') {
            detail = `: ${e.message}`
          }
          setErrorMsg(`Failed to start session${detail}`)
        }
      })

    return () => {
      cancelled = true
      esRef.current?.close(); esRef.current = null
      if (deltaTimeoutRef.current) clearTimeout(deltaTimeoutRef.current)
      deltaTimeoutRef.current = null; deltaBufferRef.current = { text: '' }
      if (sid) endPlaygroundSession(sid).catch(() => {})
    }
  }, [open, skillId, version, skillType, handleEvent])

  useEffect(() => {
    if (!sessionId) return
    const sid = sessionId
    const url = `${API_CONFIG.BASE_URL}${API_ENDPOINTS.PLAYGROUND.beacon(sid)}`
    const handler = () => navigator.sendBeacon(url, new Blob(['{}'], { type: 'application/json' }))
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [sessionId])

  // 用户滚动时记录是否贴底；离底部 >80px 视为"在往上看历史"，暂停自动跟随
  const onMessagesScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }, [])

  // 仅当用户贴底时才自动滚到底，避免吐字/中间过程把正在阅读的上文拽走
  useEffect(() => {
    if (stickRef.current) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || !sessionId || sending || status !== 'ready') return
    setInput('')
    setSending(true)
    stickRef.current = true  // 用户主动发消息，恢复自动贴底
    teamCompletedRef.current = false  // 新一轮重置，避免上一轮的 final 标记串味
    setMessages(prev => [...prev, { role: 'user', content: text }])
    try {
      await sendPlaygroundMessage(sessionId, text)
    } catch {
      setErrorMsg('Failed to send message')
      setSending(false)
    }
  }, [input, sessionId, sending, status])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }, [handleSend])

  const handleStop = useCallback(async () => {
    if (!sessionId) return
    setSending(false)
    esRef.current?.close(); esRef.current = null
    flushDeltaBuffer()  // 把缓冲里未提交的流式文字落到气泡，别丢
    try { await endPlaygroundSession(sessionId) } catch {}
    // 结束会话（释放 pod），但保留页面已有内容供用户查看——不清 messages/errorMsg
    setSessionId(null); setStatus('idle')
  }, [sessionId, flushDeltaBuffer])

  if (!open) return null

  const isActive = sending || status === 'starting'

  // status dot color
  const dotColor = status === 'error' ? C.red
    : status === 'ready' ? (isActive ? C.amber : C.green)
    : C.textFaint

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', justifyContent: 'flex-end' }}>
      {/* Backdrop（不可点击关闭） */}
      <div
        style={{ position: 'absolute', inset: 0, background: 'rgba(15,20,35,0.15)' }}
      />

      {/* Drawer */}
      <div className="pg-drawer" style={{
        position: 'relative',
        display: 'flex', flexDirection: 'column',
        width: '100%', maxWidth: 600, height: '100%',
        background: C.bg,
        boxShadow: '-8px 0 40px rgba(0,0,0,0.10), -1px 0 0 rgba(0,0,0,0.07)',
      }}>

        {/* ── Header ── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 14px', height: 52,
          background: C.surface, borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
            <div
              className={isActive ? 'pg-pulse' : undefined}
              style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor, flexShrink: 0 }}
            />
            <Terminal size={13} style={{ color: C.textMuted, flexShrink: 0 }} />
            <span style={{
              fontSize: 13, fontWeight: 600, color: C.text,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {skillName}
            </span>
            {skillType === 'swarm' && (
              <span style={{
                flexShrink: 0, fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                background: C.purpleBg, color: C.purple,
                padding: '2px 8px', borderRadius: 20, border: `1px solid ${C.purple}40`,
              }}>
                SWARM
              </span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {/* 视图切换：叙事视图隐藏工具调用与 reasoning，只看玩家同伴说话 */}
            <button
              type="button"
              onClick={() => setViewMode(v => v === 'developer' ? 'narrative' : 'developer')}
              title={viewMode === 'narrative' ? '切换到详情视图（显示工具调用与推理）' : '切换到对话视图（仅看对话）'}
              style={{
                fontSize: 11, fontWeight: 600, fontFamily: C.mono,
                background: viewMode === 'narrative' ? C.purpleBg : C.surface,
                color: viewMode === 'narrative' ? C.purple : C.textMuted,
                border: `1px solid ${viewMode === 'narrative' ? C.purple + '40' : C.border}`,
                padding: '2px 10px', borderRadius: 20, cursor: 'pointer', whiteSpace: 'nowrap',
              }}
            >
              {viewMode === 'narrative' ? '💬 对话' : '🔧 详情'}
            </button>
            {tokens > 0 && (
              <span
                title="本次会话累计消耗 token"
                style={{
                  fontFamily: C.mono, fontSize: 11, color: C.textMuted,
                  background: C.surface, border: `1px solid ${C.border}`,
                  padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap',
                }}
              >
                {tokens.toLocaleString()} tok
              </span>
            )}
            {isActive && (
              <button className="pg-stop" onClick={handleStop}>
                <Square size={9} fill="currentColor" />
                Stop
              </button>
            )}
            <button className="pg-icon-btn" onClick={onClose}>
              <X size={15} />
            </button>
          </div>
        </div>

        {/* ── Messages ── */}
        <div ref={scrollRef} onScroll={onMessagesScroll} className="pg-scroll" style={{
          flex: 1, overflowY: 'auto',
          padding: '20px 16px',
          display: 'flex', flexDirection: 'column', gap: 10,
        }}>

          {/* Starting spinner */}
          {status === 'starting' && messages.length === 0 && (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', gap: 14, flex: 1, paddingTop: 80,
              color: C.textMuted,
            }}>
              <div className="pg-spin" style={{
                width: 32, height: 32, borderRadius: '50%',
                border: `2px solid ${C.border}`, borderTopColor: C.accent,
              }} />
              <span style={{ fontSize: 13 }}>Starting session…</span>
            </div>
          )}

          {/* SwarmSkill team progress（仅开发者视图显示。叙事视图下隐藏进度条） */}
          {viewMode === 'developer' && skillType === 'swarm' && (messages.length > 0 || status === 'ready') && (() => {
            const members = ['leader', ...Object.keys(teamProgress.teammates)]
            const getS = (m: string) => m === 'leader' ? teamProgress.leader : teamProgress.teammates[m]
            const done = members.filter(m => getS(m) === 'done').length
            const active = members.filter(m => getS(m) === 'active').length
            return (
              <div style={{
                background: C.purpleBg, border: `1px solid ${C.purple}25`,
                borderRadius: 8, padding: '10px 14px', fontSize: 12,
              }}>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  {members.map(m => {
                    const s = getS(m)
                    const meta = m === 'leader' ? memberMeta('leader', 'leader') : memberMeta(m, 'teammate')
                    const idle = s !== 'done' && s !== 'active'
                    return (
                      <div key={m} style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <span
                          title={meta?.label || m}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 3,
                            fontSize: 10.5, fontWeight: 600,
                            color: idle ? C.textMuted : (meta?.color || C.purple),
                            opacity: idle ? 0.6 : 1,
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                          }}
                        >
                          <span style={{ flexShrink: 0 }}>{meta?.icon}</span>
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta?.label || m}</span>
                        </span>
                        <div
                          className={s === 'active' ? 'pg-pulse' : undefined}
                          style={{
                            height: 3, borderRadius: 2,
                            background: s === 'done' ? C.green : s === 'active' ? C.purple : C.textFaint,
                            transition: 'background 0.3s',
                          }}
                        />
                      </div>
                    )
                  })}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', color: C.textMuted }}>
                  <span style={{ color: C.purple, fontWeight: 600 }}>Team Progress</span>
                  <span>{done}/{members.length} done{active > 0 && ` · ${active} active`}</span>
                </div>
              </div>
            )
          })()}

          {/* Message list */}
          {messages
            .filter(msg => {
              if (viewMode !== 'narrative') return true
              if (msg.role === 'reasoning') return false
              if (skillType === 'swarm') {
                // swarm（多 agent）对话视图：只展示"真正送达全队的话"，隐藏内部噪声。通用、不依赖
                // 任何具体 skill 的角色名——
                // · tool 只保留发言类(send_message/delegate)，那才是成员真说出口的话；其它工具隐藏；
                // · 队友的普通文本会被框架丢弃（没人收到），属于自言自语 / 噪声，隐藏；leader 的
                //   普通文本面向 user，保留。
                if (msg.role === 'tool') return /send_?message|delegate/i.test(msg.toolName || '')
                if (msg.role === 'assistant' && msg.agentRole === 'teammate') return false
                return true
              }
              // ordinary（单 agent）对话视图：agent 的普通文本就是答案，保留；工具调用隐藏。
              if (msg.role === 'tool') return false
              return true
            })
            .map((msg, i, arr) => {
            const prevMsg = i > 0 ? arr[i - 1] : null
            const msgMember = msg.memberName || (msg.agentRole === 'leader' ? 'leader' : undefined)
            const prevMember = prevMsg
              ? (prevMsg.memberName || (prevMsg.agentRole === 'leader' ? 'leader' : undefined))
              : null
            // 只有「带 markdown 结构」的 leader 正文才算最终方案：表格行 `| … |`、分隔线 `---`、标题 `#`。
            // 不靠长度兜底——开场 / 编排的 prose 也可能很长，用长度会把它误标成「最终方案」摆到最前面。
            // 并发 swarm 里 team_complete 不一定准时到，结构特征比 msg.final 更可靠地认出终局产出。
            const isSubstantialLeaderPlan = msg.role === 'assistant' && msg.agentRole === 'leader'
              && /\n\s*\|.*\|| *\n-{3,}\s*\n|\n#{1,4}\s/.test(msg.content)
            const looksFinal = !!msg.final || isSubstantialLeaderPlan
            // Leader 编排播报折叠弱化：短的、非最终的 leader 播报走折叠组件。
            // 「正在流式的最后一条」与「实质性方案」都不折叠。
            const isOrchestration = msg.role === 'assistant'
              && msg.agentRole === 'leader' && !looksFinal
              && !(i === arr.length - 1 && sending)
            const showHeader = skillType === 'swarm'
              && msgMember && msgMember !== prevMember
              && (msg.role === 'assistant' || msg.role === 'reasoning')
              && !isOrchestration  // 折叠组件自带「Leader 编排」标签，无需再顶一个 Leader 头
            const headerMeta = showHeader ? memberMeta(msg.memberName, msg.agentRole) : null

            if (msg.role === 'milestone') {
              const ready = msg.milestoneKind === 'team_ready'
              return (
                <div key={i} className="pg-msg" style={{
                  display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'center',
                  padding: '5px 14px', borderRadius: 20, fontSize: 11.5, fontWeight: 600,
                  background: ready ? C.purpleBg : C.greenBg,
                  color: ready ? C.purple : C.green,
                  border: `1px solid ${(ready ? C.purple : C.green)}30`,
                }}>
                  <span>{ready ? '⬡' : '✓'}</span>
                  <span>{ready ? 'Team ready' : 'Collaboration complete'}{msg.content ? `: ${msg.content}` : ''}</span>
                </div>
              )
            }

            return (
              <div key={i}>
                {looksFinal ? (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    fontSize: 11.5, fontWeight: 700, color: C.purple,
                    marginBottom: 5, marginLeft: 36,
                  }}>
                    <span>⬡</span>最终方案
                  </div>
                ) : headerMeta ? (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    fontSize: 11, fontWeight: 700, color: headerMeta.color,
                    marginBottom: 5, marginLeft: 36,
                  }}>
                    <span>{headerMeta.icon}</span>{headerMeta.label}
                  </div>
                ) : null}

                {msg.role === 'user' ? (
                  <div className="pg-msg" style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <div style={{
                      maxWidth: '74%',
                      padding: '10px 14px',
                      borderRadius: '16px 4px 16px 16px',
                      background: C.userGrad,
                      color: '#fff',
                      fontSize: 14, lineHeight: 1.6,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    }}>
                      {msg.content}
                    </div>
                  </div>
                ) : msg.role === 'tool' ? (
                  <ToolMessage msg={msg} running={sending && i === arr.length - 1 && msg.toolResult === undefined} />
                ) : msg.role === 'reasoning' ? (
                  <ReasoningMessage content={msg.content} streaming={sending && i === arr.length - 1} msg={msg} />
                ) : isOrchestration ? (
                  <OrchestrationMessage content={msg.content} />
                ) : (() => {
                  const am = memberMeta(msg.memberName, msg.agentRole)
                  return (
                  <div className="pg-msg" style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                    {/* AI / member avatar */}
                    <div style={{
                      width: 26, height: 26, borderRadius: 7,
                      background: am ? am.color : C.userGrad,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0, fontSize: am ? 12 : 9, fontWeight: 800, color: '#ffffffe0',
                      letterSpacing: '0.02em', marginTop: 1,
                    }}>
                      {am ? am.icon : 'AI'}
                    </div>
                    <div style={{
                      flex: 1, minWidth: 0,
                      padding: '10px 14px',
                      borderRadius: '4px 16px 16px 16px',
                      background: looksFinal ? C.purpleBg : C.surface,
                      border: `1px solid ${looksFinal ? C.purple + '40' : C.border}`,
                      borderLeft: looksFinal ? `3px solid ${C.purple}` : am ? `3px solid ${am.color}` : `1px solid ${C.border}`,
                      color: C.text,
                      fontSize: 14, lineHeight: 1.65,
                      wordBreak: 'break-word',
                    }}>
                      <PluginMarkdown source={msg.content} mermaid />
                    </div>
                  </div>
                  )
                })()}
              </div>
            )
          })}

          {/* Typing dots */}
          {sending && messages[messages.length - 1]?.role !== 'tool' && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <div style={{
                width: 26, height: 26, borderRadius: 7,
                background: C.userGrad,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, fontSize: 9, fontWeight: 800, color: '#ffffffcc',
              }}>
                AI
              </div>
              <div style={{
                padding: '10px 14px',
                borderRadius: '4px 16px 16px 16px',
                background: C.surface,
                border: `1px solid ${C.border}`,
              }}>
                <TypingDots />
              </div>
            </div>
          )}

          {/* Error */}
          {errorMsg && (
            <div className="pg-msg" style={{
              padding: '10px 14px',
              background: C.redBg, border: `1px solid ${C.red}30`,
              borderRadius: 8, fontSize: 13, color: C.red,
              lineHeight: 1.5,
            }}>
              <span style={{ fontWeight: 700 }}>Error: </span>{errorMsg}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* ── Input area ── */}
        <div style={{
          flexShrink: 0,
          padding: '12px 16px 16px',
          borderTop: `1px solid ${C.border}`,
          background: C.surface,
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginBottom: 8 }}>
            <textarea
              className="pg-ta"
              rows={2}
              disabled={status !== 'ready' || sending}
              value={input}
              placeholder={
                status === 'starting' ? 'Starting session…'
                : status === 'ready'   ? 'Message… (↵ send, ⇧↵ newline)'
                : status === 'error'   ? 'Session failed'
                : 'Waiting…'
              }
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              className="pg-send"
              onClick={handleSend}
              disabled={!input.trim() || status !== 'ready' || sending}
            >
              <Send size={15} />
            </button>
          </div>

          {/* Status line */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 11, color: C.textFaint,
          }}>
            <span style={{
              color: status === 'ready' && !sending ? C.green
                : status === 'ready' && sending ? C.amber
                : status === 'error' ? C.red
                : C.textFaint,
            }}>●</span>
            <span>
              {status === 'ready' && !sending && 'Ready'}
              {status === 'ready' && sending && 'Running…'}
              {status === 'starting' && 'Starting session'}
              {status === 'error' && 'Session error'}
              {status === 'idle' && 'Idle'}
            </span>
            {quota && !quota.is_unlimited && (
              <span style={{
                marginLeft: 4,
                padding: '1px 6px',
                borderRadius: 4,
                background: quota.used >= quota.limit ? C.redBg : C.bgAlt,
                border: `1px solid ${quota.used >= quota.limit ? C.red + '30' : C.border}`,
                color: quota.used >= quota.limit ? C.red : C.textFaint,
                fontFamily: C.mono,
                fontSize: 10,
              }}>
                今日 {quota.used}/{quota.limit}
              </span>
            )}
            {sessionId && (
              <span style={{ marginLeft: 'auto', fontFamily: C.mono, fontSize: 10 }}>
                {sessionId.slice(0, 8)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
