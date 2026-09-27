import { Handle, Position } from '@xyflow/react'
import { Calendar, CalendarDays, Clock, Timer } from 'lucide-react'
import { memo } from 'react'
import {
  APP_TIME_ZONE,
  convertWeeklySchedule,
  LEGACY_SCHEDULE_TIME_ZONE,
} from '@/lib/dateTime'
import { cn } from '@/lib/utils'
import type { StartNodeData } from '@/types/flow'

interface StartNodeProps {
  data: StartNodeData
  selected?: boolean
}

const scheduleIcons: Record<string, typeof Clock> = {
  once: Calendar,
  daily: Clock,
  weekly: CalendarDays,
  interval: Timer,
}

const scheduleLabels: Record<string, string> = {
  once: 'One-time',
  daily: 'Daily',
  weekly: 'Weekly',
  interval: 'Interval',
}

export const StartNode = memo(({ data, selected }: StartNodeProps) => {
  const Icon = scheduleIcons[data.scheduleType] || Clock
  const weekdayNames = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
  const scheduleDays = (data.days || [0, 1, 2, 3, 4])
    .map((day) => weekdayNames[day])
    .filter((day): day is string => Boolean(day))
  const displayedSchedule = convertWeeklySchedule(
    data.time || '09:15',
    scheduleDays,
    data.timezone || LEGACY_SCHEDULE_TIME_ZONE,
    APP_TIME_ZONE
  )

  // Format interval display
  const getIntervalDisplay = () => {
    const value = data.intervalValue || 1
    const unit = data.intervalUnit || 'minutes'
    const unitShort = unit === 'seconds' ? 's' : unit === 'hours' ? 'h' : 'm'
    return `${value}${unitShort}`
  }

  return (
    <div className={cn('workflow-node node-start min-w-[120px]', selected && 'selected')}>
      <div className="p-2">
        <div className="mb-1.5 flex items-center gap-1.5">
          <div className="node-icon flex h-5 w-5 items-center justify-center rounded">
            <Icon className="h-3 w-3" />
          </div>
          <div>
            <div className="text-xs font-medium leading-tight">Start</div>
            <div className="text-[9px] text-muted-foreground">
              {scheduleLabels[data.scheduleType] || 'Schedule'}
            </div>
          </div>
        </div>
        <div className="rounded bg-muted/50 px-1.5 py-1 text-[10px]">
          {/* Show interval for interval schedule, time for others */}
          {data.scheduleType === 'interval' ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Every:</span>
              <span className="mono-data font-medium">{getIntervalDisplay()}</span>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Almaty:</span>
              <span className="mono-data font-medium">{displayedSchedule.time}</span>
            </div>
          )}
          {(data.scheduleType === 'daily' || data.scheduleType === 'weekly') &&
            data.days &&
            data.days.length > 0 &&
            data.days.length < 7 && (
              <div className="mt-0.5 flex items-center justify-between">
                <span className="text-muted-foreground">Days:</span>
                <span className="mono-data text-[9px]">
                  {displayedSchedule.days
                    .map((day) => ['M', 'T', 'W', 'T', 'F', 'S', 'S'][weekdayNames.indexOf(day)])
                    .join('')}
                </span>
              </div>
            )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bottom-0 !translate-y-1/2" />
    </div>
  )
})

StartNode.displayName = 'StartNode'
