interface SpeakerMapPanelProps {
  speakerMap: Record<string, string>
  suggestions: string[]
  segmentCounts: Record<string, number>
  firstSegmentStart: Record<string, number>
  disabled: boolean
  onNameChange: (label: string, name: string) => void
  onPlaySample: (seconds: number) => void
}

/**
 * Maps detected speaker labels (SPEAKER_00, …) to real names. Names apply
 * live to every segment row and become "Name:" prefixes in the approved
 * transcript. Suggestions come from the intake speaker list.
 */
export default function SpeakerMapPanel({
  speakerMap,
  suggestions,
  segmentCounts,
  firstSegmentStart,
  disabled,
  onNameChange,
  onPlaySample,
}: SpeakerMapPanelProps) {
  const labels = Object.keys(speakerMap).sort()
  if (labels.length === 0) return null

  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 p-4 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-white">Speakers</h2>
        <p className="text-xs text-surface-400 mt-0.5">
          Name each detected speaker — names label every caption in the final transcript.
        </p>
      </div>
      <datalist id="speaker-suggestions">
        {suggestions.map(name => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <ul className="space-y-2">
        {labels.map(label => (
          <li key={label} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor={`speaker-name-${label}`} className="font-mono text-xs text-surface-400">
                {label}
              </label>
              <div className="flex items-center gap-2 text-xs text-surface-400">
                <span>{segmentCounts[label] ?? 0} segments</span>
                {firstSegmentStart[label] !== undefined && (
                  <button
                    type="button"
                    onClick={() => onPlaySample(firstSegmentStart[label])}
                    className="text-pbs-400 hover:text-pbs-300 transition-colors"
                    aria-label={`Play a sample of ${speakerMap[label]?.trim() || label}`}
                  >
                    ▶ sample
                  </button>
                )}
              </div>
            </div>
            <input
              id={`speaker-name-${label}`}
              type="text"
              list="speaker-suggestions"
              value={speakerMap[label]}
              disabled={disabled}
              placeholder="Speaker name"
              onChange={e => onNameChange(label, e.target.value)}
              className="w-full bg-surface-900 border border-surface-600 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-surface-400 focus:outline-none focus:border-pbs-400"
            />
          </li>
        ))}
      </ul>
    </div>
  )
}
