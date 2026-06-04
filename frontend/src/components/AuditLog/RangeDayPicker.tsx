// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo, useState } from 'react'
import { Popover } from '@mui/material'
import { DateCalendar, LocalizationProvider, PickersDay } from '@mui/x-date-pickers'
import type { PickersDayProps } from '@mui/x-date-pickers'
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs'
import dayjs, { Dayjs } from 'dayjs'
import 'dayjs/locale/zh-cn'

import type { DateRange } from './AuditSearchBar'

/**
 * 基于 MUI DateCalendar 的双击区间选择器。
 * 交互：
 *   - 第一次点击：选中"开始日期"（结束日期临时清空）；
 *   - 第二次点击：
 *       · 若日期 >= 已选开始日期：作为"结束日期"，关闭弹窗；
 *       · 若日期 <  已选开始日期：作为新的"开始日期"（结束日期再次清空）。
 *   - 快捷按钮：今日 / 近 7 天 / 近 30 天 / 清空（重置为近 7 天默认）。
 *   - 选择日期统一按"北京时区"解释为日界，与后端口径一致。
 */

const BJ_OFFSET_MS = 8 * 60 * 60 * 1000
const MS_PER_DAY = 86_400_000

function startOfBjDay(ms: number): number {
  const bj = new Date(ms + BJ_OFFSET_MS)
  bj.setUTCHours(0, 0, 0, 0)
  return bj.getTime() - BJ_OFFSET_MS
}

function endOfBjDay(ms: number): number {
  const bj = new Date(ms + BJ_OFFSET_MS)
  bj.setUTCHours(23, 59, 59, 999)
  return bj.getTime() - BJ_OFFSET_MS
}

function bjDayjs(ms: number): Dayjs {
  // DateCalendar 只关心 year/month/date 字段。我们直接从北京时间组件构造一个本地 dayjs，
  // 即便用户系统时区不是 BJ，传给 DateCalendar 的"哪一天"在视觉上保持一致。
  const bj = new Date(ms + BJ_OFFSET_MS)
  return dayjs().year(bj.getUTCFullYear()).month(bj.getUTCMonth()).date(bj.getUTCDate())
}

function dayjsToBjStartMs(d: Dayjs): number {
  return Date.UTC(d.year(), d.month(), d.date(), 0, 0, 0, 0) - BJ_OFFSET_MS
}

function dayjsToBjEndMs(d: Dayjs): number {
  return Date.UTC(d.year(), d.month(), d.date(), 23, 59, 59, 999) - BJ_OFFSET_MS
}

function formatBjDate(ms: number): string {
  const bj = new Date(ms + BJ_OFFSET_MS)
  const y = bj.getUTCFullYear()
  const m = String(bj.getUTCMonth() + 1).padStart(2, '0')
  const d = String(bj.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function isSameBjDay(a: number, b: number): boolean {
  return startOfBjDay(a) === startOfBjDay(b)
}

interface RangeDayPickerProps {
  anchorEl: HTMLElement | null
  open: boolean
  onClose: () => void
  range: DateRange
  onChange: (range: DateRange) => void
}

interface RangeAwareDayProps extends PickersDayProps {
  startMs: number | null
  endMs: number | null
}

function RangeAwareDay(props: RangeAwareDayProps) {
  const { day, startMs, endMs, ...rest } = props
  const ts = dayjsToBjStartMs(day)
  const inRange =
    startMs !== null && endMs !== null && ts >= startOfBjDay(startMs) && ts <= startOfBjDay(endMs)
  const isStart = startMs !== null && isSameBjDay(ts, startMs)
  const isEnd = endMs !== null && isSameBjDay(ts, endMs)
  const isEdge = isStart || isEnd

  return (
    <PickersDay
      {...rest}
      day={day}
      selected={isEdge}
      sx={{
        ...(inRange && !isEdge
          ? { backgroundColor: 'rgba(79, 70, 229, 0.12)', color: '#312E81', borderRadius: 0 }
          : {}),
        ...(isStart
          ? { backgroundColor: '#4F46E5', color: '#fff', '&:hover': { backgroundColor: '#4338CA' } }
          : {}),
        ...(isEnd
          ? { backgroundColor: '#4F46E5', color: '#fff', '&:hover': { backgroundColor: '#4338CA' } }
          : {}),
      }}
    />
  )
}

export function RangeDayPicker({ anchorEl, open, onClose, range, onChange }: RangeDayPickerProps) {
  // pending = 用户已经点了"开始"还没点"结束"时的中间态。
  const [pendingStart, setPendingStart] = useState<number | null>(null)

  // 每次打开重置 pending 状态。
  useEffect(() => {
    if (open) setPendingStart(null)
  }, [open])

  const startMs = pendingStart ?? range.date_from_ms
  const endMs = pendingStart !== null ? null : range.date_to_ms

  const initialMonth = useMemo(() => bjDayjs(startMs), [startMs])

  const handlePick = (value: Dayjs | null) => {
    if (!value) return
    const startTs = dayjsToBjStartMs(value)
    const endTs = dayjsToBjEndMs(value)

    if (pendingStart === null) {
      // 第一次点击：记录起点，等下一次点击
      setPendingStart(startTs)
      return
    }

    if (startTs < pendingStart) {
      // 选了比起点更早的日期 → 重置为新起点
      setPendingStart(startTs)
      return
    }

    // 第二次点击：组成区间，应用
    onChange({
      preset: 'custom',
      date_from_ms: pendingStart,
      date_to_ms: endTs,
    })
    setPendingStart(null)
    onClose()
  }

  const applyPreset = (preset: 'today' | '7d' | '30d') => {
    const end = endOfBjDay(Date.now())
    let start = startOfBjDay(Date.now())
    if (preset === '7d') start = startOfBjDay(Date.now() - 6 * MS_PER_DAY)
    if (preset === '30d') start = startOfBjDay(Date.now() - 29 * MS_PER_DAY)
    onChange({ preset, date_from_ms: start, date_to_ms: end })
    setPendingStart(null)
    onClose()
  }

  const clear = () => {
    // 等同于"重置为默认（近 7 天）"
    applyPreset('7d')
  }

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      slotProps={{ paper: { sx: { mt: 1, borderRadius: 2, boxShadow: '0 10px 30px rgba(0,0,0,0.12)' } } }}
    >
      <div className="flex flex-col gap-2 p-3" style={{ minWidth: 340 }}>
        <div className="flex items-center justify-between text-xs text-[#374151]">
          <span>
            {pendingStart === null
              ? '请点击"开始日期"'
              : `已选开始：${formatBjDate(pendingStart)}，请点击"结束日期"`}
          </span>
          <span className="text-[#9CA3AF]">
            当前：{formatBjDate(range.date_from_ms)} ~ {formatBjDate(range.date_to_ms)}
          </span>
        </div>

        <LocalizationProvider dateAdapter={AdapterDayjs} adapterLocale="zh-cn">
          <DateCalendar
            value={initialMonth}
            onChange={handlePick}
            slots={{
              day: dayProps => (
                <RangeAwareDay
                  {...(dayProps as PickersDayProps)}
                  startMs={startMs}
                  endMs={endMs}
                />
              ),
            }}
            views={['day']}
            sx={{ '& .MuiPickersCalendarHeader-root': { paddingLeft: 1, paddingRight: 1 } }}
          />
        </LocalizationProvider>

        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
          <button
            type="button"
            onClick={() => applyPreset('today')}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-[#374151] hover:bg-slate-50"
          >
            今日
          </button>
          <button
            type="button"
            onClick={() => applyPreset('7d')}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-[#374151] hover:bg-slate-50"
          >
            近 7 天
          </button>
          <button
            type="button"
            onClick={() => applyPreset('30d')}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-[#374151] hover:bg-slate-50"
          >
            近 30 天
          </button>
          <span className="grow" />
          <button
            type="button"
            onClick={clear}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-[#6B7280] hover:bg-slate-50"
          >
            清空
          </button>
        </div>
      </div>
    </Popover>
  )
}
