/**
 * Status color utilities for consistent job status styling across components.
 */

export type JobStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'investigating'
  | 'paused'
  | 'cancelled'

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
      return 'text-status-paused'
    case 'paused':
      return 'text-status-paused'
    case 'cancelled':
      return 'text-status-cancelled'
    default:
      return 'text-surface-400'
  }
}

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
      return 'bg-pbs-500/15 text-pbs-400 border-pbs-500/30'
    case 'paused':
      return 'bg-status-paused/15 text-status-paused border-status-paused/30'
    case 'cancelled':
      return 'bg-status-cancelled/15 text-status-cancelled border-status-cancelled/30'
    default:
      return 'bg-surface-500/15 text-surface-400 border-surface-500/30'
  }
}
