import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ReadyForWork from './pages/ReadyForWork'
import Queue from './pages/Queue'
import JobDetail from './pages/JobDetail'
import Projects from './pages/Projects'
import Settings from './pages/Settings'
import Help from './pages/Help'
import { ToastProvider } from './components/ui/Toast'
import { PreferencesProvider } from './context/PreferencesContext'

function App() {
  return (
    <PreferencesProvider>
      <ToastProvider>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Queue />} />
            <Route path="ready" element={<ReadyForWork />} />
            <Route path="queue" element={<Queue />} />
            <Route path="jobs/:id" element={<JobDetail />} />
            <Route path="projects" element={<Projects />} />
            <Route path="settings" element={<Settings />} />
            <Route path="help" element={<Help />} />
          </Route>
        </Routes>
      </ToastProvider>
    </PreferencesProvider>
  )
}

export default App
