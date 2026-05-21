import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import { ToastProvider } from './components/ui/Toast'
import { PreferencesProvider } from './context/PreferencesContext'

// Lazy-load pages that aren't needed on initial render
const ReadyForWork = lazy(() => import('./pages/ReadyForWork'))
const Queue = lazy(() => import('./pages/Queue'))
const JobDetail = lazy(() => import('./pages/JobDetail'))
const Projects = lazy(() => import('./pages/Projects'))
const Settings = lazy(() => import('./pages/Settings'))
const Help = lazy(() => import('./pages/Help'))

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="text-surface-400 text-sm">Loading...</div>
    </div>
  )
}

function App() {
  return (
    <PreferencesProvider>
      <ToastProvider>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/queue" replace />} />
            <Route path="ready" element={<Suspense fallback={<PageLoader />}><ReadyForWork /></Suspense>} />
            <Route path="queue" element={<Suspense fallback={<PageLoader />}><Queue /></Suspense>} />
            <Route path="jobs/:id" element={<Suspense fallback={<PageLoader />}><JobDetail /></Suspense>} />
            <Route path="projects" element={<Suspense fallback={<PageLoader />}><Projects /></Suspense>} />
            <Route path="settings" element={<Suspense fallback={<PageLoader />}><Settings /></Suspense>} />
            <Route path="help" element={<Suspense fallback={<PageLoader />}><Help /></Suspense>} />
          </Route>
        </Routes>
      </ToastProvider>
    </PreferencesProvider>
  )
}

export default App
