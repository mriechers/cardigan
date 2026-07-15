/**
 * Status color utilities for consistent job status styling across components.
 *
 * Two variants are provided:
 * - getStatusTextColor: Simple text color for compact displays
 * - getStatusBadgeColor: Full badge styling with background, text, and border
 */

export type JobStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'investigating'
  | 'paused'
  | 'cancelled'
  | 'awaiting_review'

/**
 * Human-readable label for a job status. Falls back to the raw value.
 */
export function getStatusLabel(status: string): string {
  switch (status) {
    case 'in_progress':
      return 'in progress'
    case 'awaiting_review':
      return 'needs review'
    default:
      return status
  }
}

/**
 * Get simple text color class for a job status.
 * Use this for compact status displays where only text color is needed.
 *
 * @example
 * <span className={getStatusTextColor(job.status)}>{job.status}</span>
 */
export function getStatusTextColor(status: string): string {
  switch (status) {
    case 'pending':
      return 'text-status-pending'
    case 'in_progress':
      return 'text-status-processing'
    case 'completed':
      return 'text-status-completed'
    case 'failed':
      return 'text-status-failed'
    case 'investigating':
      // teal-400 — distinct from pbs-blue (in_progress/processing) and amber (pending/paused)
      return 'text-teal-400'
    case 'paused':
      return 'text-status-paused'
    case 'cancelled':
      return 'text-status-cancelled'
    case 'awaiting_review':
      // violet-400 — human action needed; distinct from error/paused ambers and reds
      return 'text-violet-400'
    default:
      return 'text-surface-400'
  }
}

/**
 * Get full badge styling classes for a job status.
 * Includes background, text color, and border for badge/pill displays.
 *
 * @example
 * <span className={`px-2 py-0.5 rounded-md text-xs border ${getStatusBadgeColor(job.status)}`}>
 *   {job.status}
 * </span>
 */
export function getStatusBadgeColor(status: string): string {
  switch (status) {
    case 'pending':
      return 'bg-status-pending/15 text-status-pending border-status-pending/30'
    case 'in_progress':
      return 'bg-status-processing/15 text-status-processing border-status-processing/30'
    case 'completed':
      return 'bg-status-completed/15 text-status-completed border-status-completed/30'
    case 'failed':
      return 'bg-status-failed/15 text-status-failed border-status-failed/30'
    case 'investigating':
      // teal-400 (#2dd4bf) — visually distinct from pbs-blue in_progress/processing and amber pending/paused
      return 'bg-teal-500/15 text-teal-400 border-teal-500/30'
    case 'paused':
      return 'bg-status-paused/15 text-status-paused border-status-paused/30'
    case 'cancelled':
      return 'bg-status-cancelled/15 text-status-cancelled border-status-cancelled/30'
    case 'awaiting_review':
      // violet-400 (#a78bfa) — human action needed; ~4.9:1 on surface-900
      return 'bg-violet-500/15 text-violet-400 border-violet-500/30'
    default:
      return 'bg-surface-500/15 text-surface-400 border-surface-500/30'
  }
}
