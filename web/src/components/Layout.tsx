import { useState, useRef, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import StatusBar from './StatusBar'
import { useKeyboardShortcuts, getKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { usePreferences } from '../context/PreferencesContext'

export default function Layout() {
  useKeyboardShortcuts()
  const { preferences } = usePreferences()
  const [showHelp, setShowHelp] = useState(false)
  const helpModalRef = useFocusTrap(showHelp)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  // Apply preferences to document root
  useEffect(() => {
    const root = document.documentElement

    // Text size - set CSS custom property
    const textScaleMap = {
      default: 1,
      large: 1.125,   // 18px base
      larger: 1.25,   // 20px base
    }
    root.style.setProperty('--text-scale', textScaleMap[preferences.textSize].toString())

    // Reduce motion - add class to root
    if (preferences.reduceMotion) {
      root.classList.add('reduce-motion')
    } else {
      root.classList.remove('reduce-motion')
    }

    // High contrast - add class to root
    if (preferences.highContrast) {
      root.classList.add('high-contrast')
    } else {
      root.classList.remove('high-contrast')
    }
  }, [preferences])

  // Listen for '?' to show help
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      if (e.key === '?' && !showHelp) {
        const target = e.target as HTMLElement
        if (
          target.tagName !== 'INPUT' &&
          target.tagName !== 'TEXTAREA' &&
          !target.isContentEditable
        ) {
          e.preventDefault()
          setShowHelp(true)
        }
      } else if (e.key === 'Escape' && showHelp) {
        setShowHelp(false)
      }
    }
    document.addEventListener('keydown', handleKeyPress)
    return () => document.removeEventListener('keydown', handleKeyPress)
  }, [showHelp])

  const closeHelp = () => {
    setShowHelp(false)
    setTimeout(() => triggerRef.current?.focus(), 0)
  }

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-gray-700 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      {/* Skip Navigation Link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:px-4 focus:py-2 focus:bg-blue-600 focus:text-white focus:rounded-md focus:shadow-lg"
      >
        Skip to main content
      </a>

      {/* Status Bar */}
      <StatusBar />

      {/* Navigation */}
      <nav className="bg-gray-800 border-b border-gray-700" aria-label="Main navigation">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center space-x-4">
              <img
                src="https://wisconsinpublictv.s3.us-east-2.amazonaws.com/wp-content/uploads/2023/08/pbs-wisconsin-wblue-rgb-2-412x62.png"
                alt="PBS Wisconsin"
                className="h-6"
              />
              <span className="text-lg font-semibold text-white">
                Cardigan
              </span>
              <span className="text-xs text-gray-400">v4.0</span>
            </div>
            <div className="flex items-center space-x-1">
              <NavLink
                to="/"
                className={navLinkClass}
                end
              >
                Dashboard
              </NavLink>
              <NavLink
                to="/ready"
                className={navLinkClass}
              >
                Ready for Work
              </NavLink>
              <NavLink
                to="/queue"
                className={navLinkClass}
              >
                Queue
              </NavLink>
              <NavLink
                to="/projects"
                className={navLinkClass}
              >
                Projects
              </NavLink>
              <NavLink
                to="/settings"
                className={navLinkClass}
              >
                Settings
              </NavLink>
              <NavLink
                to="/system"
                className={navLinkClass}
              >
                System
              </NavLink>
              <NavLink
                to="/help"
                className={navLinkClass}
              >
                Help
              </NavLink>
              <button
                ref={triggerRef}
                onClick={() => setShowHelp(true)}
                className="px-3 py-2 rounded-md text-sm font-medium transition-colors text-gray-300 hover:bg-gray-700 hover:text-white"
                aria-label="Show keyboard shortcuts"
                title="Keyboard shortcuts (?)"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main id="main-content" tabIndex={-1} className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
        <Outlet />
      </main>

      {/* Keyboard Shortcuts Help Modal */}
      {showHelp && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={closeHelp}
        >
          <div
            ref={helpModalRef}
            className="bg-gray-900 rounded-lg border border-gray-700 w-full max-w-md"
            role="dialog"
            aria-modal="true"
            aria-labelledby="shortcuts-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
              <h3 id="shortcuts-modal-title" className="text-lg font-medium text-white">
                Keyboard Shortcuts
              </h3>
              <button
                onClick={closeHelp}
                className="text-gray-400 hover:text-white text-2xl leading-none"
                aria-label="Close shortcuts help"
              >
                &times;
              </button>
            </div>
            {/* Modal Content */}
            <div className="px-6 py-4">
              <div className="space-y-3">
                {getKeyboardShortcuts().map((shortcut, index) => (
                  <div key={index} className="flex items-center justify-between">
                    <span className="text-gray-300">{shortcut.description}</span>
                    <kbd className="px-2 py-1 bg-gray-800 border border-gray-600 rounded text-sm font-mono text-gray-300">
                      {shortcut.keys}
                    </kbd>
                  </div>
                ))}
              </div>
              <div className="mt-6 pt-4 border-t border-gray-700">
                <p className="text-xs text-gray-500">
                  Press <kbd className="px-1 py-0.5 bg-gray-800 border border-gray-600 rounded text-xs font-mono">?</kbd> to open this help anytime
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
