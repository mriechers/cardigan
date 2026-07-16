import SegmentRow, { EditableSegment } from './SegmentRow'

interface SegmentListProps {
  segments: EditableSegment[]
  rawById: Record<number, string>
  speakerLabels: string[]
  speakerMap: Record<string, string>
  activeSegmentId: number | null
  disabled: boolean
  onTextChange: (id: number, text: string) => void
  onSpeakerChange: (id: number, speaker: string) => void
  onSeek: (seconds: number) => void
}

/**
 * The editable transcript body. Plain mapped rows — SegmentRow is memoized
 * and handles ~1–2k rows fine; add react-window if hour-plus recordings
 * ever feel sluggish.
 */
export default function SegmentList({
  segments,
  rawById,
  speakerLabels,
  speakerMap,
  activeSegmentId,
  disabled,
  onTextChange,
  onSpeakerChange,
  onSeek,
}: SegmentListProps) {
  if (segments.length === 0) {
    return <p className="text-surface-400 text-sm py-8 text-center">No transcript segments.</p>
  }

  return (
    <div className="space-y-1" role="list" aria-label="Transcript segments">
      {segments.map(segment => (
        <div role="listitem" key={segment.id}>
          <SegmentRow
            segment={segment}
            rawText={rawById[segment.id] ?? segment.text}
            speakerLabels={speakerLabels}
            speakerMap={speakerMap}
            isActive={segment.id === activeSegmentId}
            disabled={disabled}
            onTextChange={onTextChange}
            onSpeakerChange={onSpeakerChange}
            onSeek={onSeek}
          />
        </div>
      ))}
    </div>
  )
}
