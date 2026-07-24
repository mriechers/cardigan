import { forwardRef } from 'react'

interface AudioBarProps {
  src: string
  onTimeUpdate: (seconds: number) => void
}

/**
 * Sticky audio player for the transcript review page. The backend serves
 * the job's audio with Range support, so scrubbing works natively.
 */
const AudioBar = forwardRef<HTMLAudioElement, AudioBarProps>(function AudioBar(
  { src, onTimeUpdate },
  ref
) {
  return (
    <div className="sticky top-0 z-10 bg-surface-900/95 backdrop-blur border border-surface-700 rounded-lg p-2">
      <audio
        ref={ref}
        src={src}
        controls
        preload="metadata"
        className="w-full h-10"
        aria-label="Recording playback"
        onTimeUpdate={e => onTimeUpdate((e.target as HTMLAudioElement).currentTime)}
      />
    </div>
  )
})

export default AudioBar
