// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { getApiClient } from './client'
const apiClient = getApiClient()
import { API_CONFIG, API_ENDPOINTS } from './config'

export interface PlaygroundSession {
  session_id: string
  status: 'starting' | 'ready' | 'active' | 'done' | 'error'
  timeout_seconds: number
}

export interface SseEvent {
  type: 'ready' | 'text' | 'reasoning' | 'tool_call' | 'tool_result' | 'usage' | 'answer' | 'error' | 'done' | 'session_ended' | 'raw'
    | 'team_ready' | 'team_complete' | 'keepalive'
  delta?: string
  content?: string
  name?: string
  input?: string
  output?: string | unknown
  error?: string | unknown
  exit_code?: number
  code?: string
  message?: string
  /** SwarmSkill: 该事件来自哪个角色（'leader' / 'teammate'） */
  role?: string
  /** SwarmSkill: 具体 teammate 成员名（source_member） */
  member?: string
  /** team_ready */
  team_name?: string
  /** team_complete */
  member_count?: number
  task_count?: number
  /** usage：单次 LLM 调用的 token 用量 */
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  /** usage：控制面代理累计的本会话权威 token 总量（优先于 worker 自报） */
  session_total?: number
  [key: string]: unknown
}

export async function createPlaygroundSession(
  skillId: string,
  version: string,
  skillType: 'ordinary' | 'swarm' = 'ordinary',
): Promise<PlaygroundSession> {
  const resp = await apiClient.post<PlaygroundSession>(API_ENDPOINTS.PLAYGROUND.sessions, {
    skill_id: skillId,
    version,
    skill_type: skillType,
  })
  return resp.data
}

export async function sendPlaygroundMessage(sessionId: string, content: string): Promise<void> {
  await apiClient.post(API_ENDPOINTS.PLAYGROUND.messages(sessionId), { content })
}

export function openPlaygroundStream(
  sessionId: string,
  onEvent: (event: SseEvent) => void,
  onError: (err: Event) => void,
): EventSource {
  const url = `${API_CONFIG.BASE_URL}${API_ENDPOINTS.PLAYGROUND.stream(sessionId)}`
  const es = new EventSource(url, { withCredentials: true })
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as SseEvent
      onEvent(data)
    } catch {
      // ignore malformed events
    }
  }
  es.onerror = onError
  return es
}

export async function endPlaygroundSession(sessionId: string): Promise<void> {
  await apiClient.delete(API_ENDPOINTS.PLAYGROUND.end(sessionId))
}

export async function getSiteConfig(): Promise<{ playground_enabled: boolean }> {
  const resp = await apiClient.get<{ playground_enabled: boolean }>(API_ENDPOINTS.SITE.CONFIG)
  return resp.data
}

export interface PlaygroundQuota {
  used: number
  limit: number
  is_unlimited: boolean
  reset_at: string
}

export async function getPlaygroundQuota(): Promise<PlaygroundQuota> {
  const resp = await apiClient.get<PlaygroundQuota>(API_ENDPOINTS.PLAYGROUND.quota)
  return resp.data
}
