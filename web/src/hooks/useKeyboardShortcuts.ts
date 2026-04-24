import { useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * Global keyboard shortcuts for navigation and actions.
 *
 * Shortcuts:
 * - g+q: Go to queue (home)
 * - g+r: Go to ready for work
 * - g+p: Go to projects
 * - g+s: Go to settings
 * - /: Focus search input (if available on page)
 * - ?: Show help modal
 *
 * Shortcuts are disabled when typing in inputs, textareas, or contenteditable elements.
 */
export function useKeyboardShortcuts() {
  const navigate = useNavigate()

  const handleKeyPress = useCallback(
    (event: KeyboardEvent) => {
      // Ignore shortcuts when typing in form fields
      const target = event.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return
      }

      // Handle '/' for search
      if (event.key === '/') {
        event.preventDefault()
        const searchInput = document.getElementById('queue-search') as HTMLInputElement
        if (searchInput) {
          searchInput.focus()
        }
        return
      }

      // Handle 'g' prefix for navigation shortcuts
      if (event.key === 'g') {
        // Set up one-time listener for next key
        const handleNextKey = (nextEvent: KeyboardEvent) => {
          // Ignore if typing in form field
          const nextTarget = nextEvent.target as HTMLElement
          if (
            nextTarget.tagName === 'INPUT' ||
            nextTarget.tagName === 'TEXTAREA' ||
            nextTarget.isContentEditable
          ) {
            document.removeEventListener('keydown', handleNextKey)
            return
          }

          switch (nextEvent.key) {
            case 'q':
              nextEvent.preventDefault()
              navigate('/')
              break
            case 'r':
              nextEvent.preventDefault()
              navigate('/ready')
              break
            case 'p':
              nextEvent.preventDefault()
              navigate('/projects')
              break
            case 's':
              nextEvent.preventDefault()
              navigate('/settings')
              break
          }
          document.removeEventListener('keydown', handleNextKey)
        }

        document.addEventListener('keydown', handleNextKey, { once: true })

        // Clean up after 1 second if no second key pressed
        setTimeout(() => {
          document.removeEventListener('keydown', handleNextKey)
        }, 1000)
      }

      // Handle '?' for help
      if (event.key === '?') {
        event.preventDefault()
      }
    },
    [navigate]
  )

  useEffect(() => {
    document.addEventListener('keydown', handleKeyPress)
    return () => {
      document.removeEventListener('keydown', handleKeyPress)
    }
  }, [handleKeyPress])
}

/**
 * Get list of available keyboard shortcuts for display in help UI.
 */
export function getKeyboardShortcuts() {
  return [
    { keys: 'g q', description: 'Go to Queue' },
    { keys: 'g r', description: 'Go to Ready for Work' },
    { keys: 'g p', description: 'Go to Projects' },
    { keys: 'g s', description: 'Go to Settings' },
    { keys: '/', description: 'Focus search (on Queue page)' },
    { keys: '?', description: 'Show keyboard shortcuts help' },
  ]
}
