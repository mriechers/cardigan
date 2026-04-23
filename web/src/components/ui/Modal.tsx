import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: string
  children: ReactNode
  maxWidth?: string
  titleId?: string
}

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-lg',
  titleId,
}: ModalProps) {
  const modalRef = useFocusTrap(isOpen)
  const generatedId = useRef(`modal-title-${Math.random().toString(36).slice(2, 8)}`).current
  const labelId = titleId || generatedId

  useEffect(() => {
    if (!isOpen) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        ref={modalRef}
        className={`bg-surface-900 rounded-lg border border-surface-700 w-full ${maxWidth} max-h-[90vh] flex flex-col`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
          <h3 id={labelId} className="text-lg font-display font-semibold text-white">
            {title}
          </h3>
          <button
            onClick={onClose}
            className="text-surface-400 hover:text-white text-2xl leading-none p-1"
            aria-label="Close"
          >
            &times;
          </button>
        </div>
        <div className="flex-1 overflow-auto p-6">
          {children}
        </div>
      </div>
    </div>
  )
}
