import { memo, useLayoutEffect, useRef } from 'react'
import { formatClock } from '../../utils/formatTime'

export interface EditableSegment {
  id: number
  start: number
  end: number
  speaker: string | null
  text: string
}

interface SegmentRowProps {
  segment: EditableSegment
  rawText: string
  speakerLabels: string[]
  speakerMap: Record<string, string>
  isActive: boolean
  disabled: boolean
  onTextChange: (id: number, text: string) => void
  onSpeakerChange: (id: number, speaker: string) => void
  onSeek: (seconds: number) => void
}

/**
 * One editable transcript segment: seekable timestamp, speaker select,
 * auto-growing textarea. Memoized — the list can hold a couple thousand
 * rows, so parent callbacks must be stable.
 */
const SegmentRow = memo(function SegmentRow({
  segment,
  rawText,
  speakerLabels,
  speakerMap,
  isActive,
  disabled,
  onTextChange,
  onSpeakerChange,
  onSeek,
}: SegmentRowProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isDirty = segment.text.trim() !== rawText.trim()

  useLayoutEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${el.scrollHeight}px`
    }
  }, [segment.text])

  return (
    <div
      className={`flex gap-3 rounded-md px-3 py-2 border-l-2 transition-colors ${
        isActive ? 'bg-pbs-900/30 border-pbs-400' : isDirty ? 'border-status-pending bg-surface-800/60' : 'border-transparent'
      }`}
      data-segment-id={segment.id}
    >
      <div className="flex flex-col items-start gap-1 w-28 shrink-0">
        <button
          type="button"
          onClick={() => onSeek(segment.start)}
          className="font-mono text-xs text-pbs-400 hover:text-pbs-300 transition-colors"
          title="Play from here"
          aria-label={`Play from ${formatClock(segment.start)}`}
        >
          ▶ {formatClock(segment.start)}
        </button>
        {segment.speaker && (
          <>
            <label htmlFor={`segment-speaker-${segment.id}`} className="sr-only">
              Speaker for segment at {formatClock(segment.start)}
            </label>
            <select
              id={`segment-speaker-${segment.id}`}
              value={segment.speaker}
              disabled={disabled}
              onChange={e => onSpeakerChange(segment.id, e.target.value)}
              className="w-full bg-surface-900 border border-surface-700 rounded px-1.5 py-1 text-xs text-surface-300 focus:outline-none focus:border-pbs-400"
            >
              {speakerLabels.map(label => (
                <option key={label} value={label}>
                  {speakerMap[label]?.trim() || label}
                </option>
              ))}
            </select>
          </>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <label htmlFor={`segment-text-${segment.id}`} className="sr-only">
          Transcript text at {formatClock(segment.start)}
        </label>
        <textarea
          id={`segment-text-${segment.id}`}
          ref={textareaRef}
          value={segment.text}
          disabled={disabled}
          rows={1}
          onChange={e => onTextChange(segment.id, e.target.value)}
          className="w-full resize-none overflow-hidden bg-transparent text-sm text-surface-200 leading-relaxed focus:outline-none focus:bg-surface-900 focus:border focus:border-pbs-400 rounded px-1.5 py-1 -mx-1.5"
          spellCheck
        />
        {isDirty && (
          <p className="text-xs text-surface-500 mt-0.5" title={rawText}>
            <span className="text-status-pending">edited</span> — original: <span className="italic">{rawText}</span>
          </p>
        )}
      </div>
    </div>
  )
})

export default SegmentRow
