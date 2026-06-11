import { useEffect, useRef, ReactNode } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'

type DialogVariant = 'danger' | 'warning' | 'info'

interface ConfirmDialogProps {
  isOpen: boolean
  onConfirm: () => void
  onCancel: () => void
  title: string
  message: ReactNode
  confirmText?: string
  cancelText?: string
  variant?: DialogVariant
}

export default function ConfirmDialog({
  isOpen,
  onConfirm,
  onCancel,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  variant = 'info',
}: ConfirmDialogProps) {
  const dialogRef = useFocusTrap(isOpen)
  const cancelButtonRef = useRef<HTMLButtonElement>(null)

  // Focus cancel button when dialog opens (safer default for destructive actions)
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => cancelButtonRef.current?.focus(), 0)
    }
  }, [isOpen])

  // Handle Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onCancel()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onCancel])

  // Prevent body scroll when open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [isOpen])

  if (!isOpen) return null

  const variantStyles: Record<DialogVariant, { icon: string; iconBg: string; iconColor: string; confirmBg: string }> = {
    danger: {
      icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
      iconBg: 'bg-status-failed/20',
      iconColor: 'text-status-failed',
      confirmBg: 'bg-status-failed hover:bg-status-failed/80',
    },
    warning: {
      icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
      iconBg: 'bg-status-pending/20',
      iconColor: 'text-status-pending',
      confirmBg: 'bg-status-pending hover:bg-status-pending/80',
    },
    info: {
      icon: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
      iconBg: 'bg-pbs-900/50',
      iconColor: 'text-pbs-400',
      confirmBg: 'bg-pbs-500 hover:bg-pbs-400',
    },
  }

  const styles = variantStyles[variant]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 motion-reduce:transition-none transition-opacity duration-200"
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        className="relative bg-surface-800 rounded-lg border border-surface-700 shadow-xl max-w-md w-full p-6 motion-reduce:transition-none transform transition-all duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-4">
          {/* Icon */}
          <div className={`flex-shrink-0 w-10 h-10 rounded-full ${styles.iconBg} flex items-center justify-center`}>
            <svg
              className={`w-6 h-6 ${styles.iconColor}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={styles.icon} />
            </svg>
          </div>

          {/* Content */}
          <div className="flex-1">
            <h2 id="confirm-dialog-title" className="text-lg font-semibold text-white">
              {title}
            </h2>
            <div id="confirm-dialog-description" className="mt-2 text-sm text-surface-300">
              {message}
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            ref={cancelButtonRef}
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-surface-300 bg-surface-700 hover:bg-surface-600 rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-surface-400 focus:ring-offset-2 focus:ring-offset-surface-800"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm font-medium text-white ${styles.confirmBg} rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-surface-800`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
