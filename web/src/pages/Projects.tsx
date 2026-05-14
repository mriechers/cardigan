import { useEffect, useState, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useFocusTrap } from '../hooks/useFocusTrap'
import ProseContainer from '../components/ProseContainer'
import { formatRelativeTime, formatTimestamp } from '../utils/formatTime'
import { ARTIFACT_LABELS } from '../utils/artifactLabels'
import ScreengrabsBox from '../components/ScreengrabsBox'

interface CompletedJob {
  id: number
  project_name: string
  project_path: string
  transcript_file: string
  status: string
  completed_at: string
  actual_cost: number
  phases: Array<{
    name: string
    status: string
    cost: number
    tokens: number
  }>
}

interface ProjectArtifact {
  name: string       // Technical filename (e.g., 'analyst_output.md')
  label: string      // Friendly display name (e.g., 'Analysis')
  path: string
  type: 'directory' | 'file'
  size?: number
  modified?: string
}

interface PaginatedResponse {
  jobs: CompletedJob[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

interface SSTMetadata {
  media_id: string | null
  release_title: string | null
  short_description: string | null
  media_manager_url: string | null
  youtube_url: string | null
  airtable_url: string | null
}

export default function Projects() {
  const [jobs, setJobs] = useState<CompletedJob[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedProject, setSelectedProject] = useState<CompletedJob | null>(null)
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([])
  const [viewingArtifact, setViewingArtifact] = useState<{
    name: string      // Technical filename
    label: string     // Friendly display name
    content: string
    isJson: boolean
  } | null>(null)
  const [loadingArtifact, setLoadingArtifact] = useState(false)
  const [sstMetadata, setSstMetadata] = useState<SSTMetadata | null>(null)
  const [loadingSst, setLoadingSst] = useState(false)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const modalRef = useFocusTrap(!!viewingArtifact)

  // Pagination and search state
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const PAGE_SIZE = 50

  const fetchCompletedJobs = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        status: 'completed',
        page: page.toString(),
        page_size: PAGE_SIZE.toString(),
        sort: 'newest',
      })
      if (search) {
        params.set('search', search)
      }

      const response = await fetch(`/api/queue/?${params}`)
      if (response.ok) {
        const data: PaginatedResponse = await response.json()
        setJobs(data.jobs || [])
        setTotal(data.total)
        setTotalPages(data.total_pages)
      }
    } catch (err) {
      console.error('Failed to fetch completed jobs:', err)
    } finally {
      setLoading(false)
    }
  }, [page, search])

  useEffect(() => {
    fetchCompletedJobs()
  }, [fetchCompletedJobs])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setSearch(searchInput)
    setPage(1) // Reset to first page on new search
  }

  const clearSearch = () => {
    setSearchInput('')
    setSearch('')
    setPage(1)
  }

  const selectProject = async (job: CompletedJob) => {
    setSelectedProject(job)
    // Generate artifacts from completed phases
    setArtifacts(getExpectedArtifacts(job))

    // Fetch SST metadata from Airtable
    setSstMetadata(null)
    setLoadingSst(true)
    try {
      const response = await fetch(`/api/jobs/${job.id}/sst-metadata`)
      if (response.ok) {
        const data: SSTMetadata = await response.json()
        setSstMetadata(data)
      }
      // If 404/503, just don't show metadata (no Airtable link or not configured)
    } catch {
      // Silently fail - SST metadata is optional enhancement
    } finally {
      setLoadingSst(false)
    }
  }

  const handleViewArtifact = async (artifact: ProjectArtifact, event: React.MouseEvent<HTMLButtonElement>) => {
    if (!selectedProject) return

    // Save reference to trigger button
    triggerRef.current = event.currentTarget

    setLoadingArtifact(true)
    try {
      const response = await fetch(`/api/jobs/${selectedProject.id}/outputs/${encodeURIComponent(artifact.name)}`)
      if (!response.ok) {
        throw new Error('Failed to load artifact')
      }
      const content = await response.text()
      setViewingArtifact({
        name: artifact.name,
        label: artifact.label,
        content,
        isJson: artifact.name.endsWith('.json'),
      })
    } catch (err) {
      console.error('Failed to load artifact:', err)
    } finally {
      setLoadingArtifact(false)
    }
  }

  const closeModal = () => {
    setViewingArtifact(null)
    // Return focus to trigger button
    setTimeout(() => {
      triggerRef.current?.focus()
    }, 0)
  }

  // Handle Escape key to close modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && viewingArtifact) {
        closeModal()
      }
    }

    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [viewingArtifact])

  // Map phase names to artifact keys for lookup
  const PHASE_TO_ARTIFACT_KEY: Record<string, string> = {
    analyst: 'analysis',
    formatter: 'formatted_transcript',
    seo: 'seo_metadata',
    validator: 'qa_review',
    copy_editor: 'copy_edited',
    investigation: 'investigation',
  }

  const getExpectedArtifacts = (job: CompletedJob): ProjectArtifact[] => {
    // Generate expected artifacts based on completed phases
    const artifacts: ProjectArtifact[] = []

    job.phases?.forEach(phase => {
      if (phase.status === 'completed') {
        const artifactKey = PHASE_TO_ARTIFACT_KEY[phase.name]
        const artifactInfo = artifactKey ? ARTIFACT_LABELS[artifactKey] : null
        if (artifactInfo) {
          artifacts.push({
            name: artifactInfo.filename,
            label: artifactInfo.label,
            path: artifactInfo.filename,
            type: 'file'
          })
        }
      }
    })

    // Always include manifest if job completed
    if (job.status === 'completed') {
      const manifestInfo = ARTIFACT_LABELS.manifest
      artifacts.unshift({
        name: manifestInfo.filename,
        label: manifestInfo.label,
        path: manifestInfo.filename,
        type: 'file'
      })
    }

    return artifacts
  }

  const formatCost = (cost: number) => `$${cost.toFixed(4)}`

  const getPhaseIcon = (status: string) => {
    switch (status) {
      case 'completed': return <span className="text-green-400">✓</span>
      case 'failed': return <span className="text-red-400">✗</span>
      case 'in_progress': return <span className="text-blue-400 animate-pulse">●</span>
      default: return <span className="text-gray-400">○</span>
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-300">Loading projects...</div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header with Search */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Completed Projects</h1>
          <span className="text-gray-400 text-sm">
            {total} project{total !== 1 ? 's' : ''}
            {search && ` matching "${search}"`}
          </span>
        </div>

        {/* Search Form */}
        <form onSubmit={handleSearch} className="flex items-center space-x-2">
          <div className="relative">
            <label htmlFor="projects-search" className="sr-only">Search completed projects by filename</label>
            <input
              id="projects-search"
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by filename..."
              className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-400 focus:outline-none focus:border-blue-500 w-64"
              aria-describedby="projects-search-desc"
            />
            {search && (
              <button
                type="button"
                onClick={clearSearch}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                aria-label="Clear search"
              >
                ✕
              </button>
            )}
            <span id="projects-search-desc" className="sr-only">
              Search for completed projects by their transcript filename
            </span>
          </div>
          <button
            type="submit"
            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg transition-colors"
          >
            Search
          </button>
        </form>
      </div>

      {jobs.length === 0 ? (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-8 text-center">
          <p className="text-gray-300">No completed projects yet.</p>
          <p className="text-gray-400 text-sm mt-2">
            Projects will appear here once jobs finish processing.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Project List */}
          <div className="space-y-3">
            <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide">
              Projects
            </h2>
            <div className="space-y-2">
              {jobs.map(job => (
                <button
                  key={job.id}
                  onClick={() => selectProject(job)}
                  className={`w-full text-left p-4 rounded-lg border transition-colors ${
                    selectedProject?.id === job.id
                      ? 'bg-blue-900/30 border-blue-500/50'
                      : 'bg-gray-800 border-gray-700 hover:border-gray-600'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-medium text-white">{job.project_name}</div>
                      <div
                        className="text-sm text-gray-400"
                        title={job.completed_at ? formatTimestamp(job.completed_at) : undefined}
                      >
                        {job.completed_at ? formatRelativeTime(job.completed_at) : 'Processing...'}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-green-400 font-mono text-sm">
                        {formatCost(job.actual_cost || 0)}
                      </div>
                      <div className="text-xs text-gray-400">total cost</div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Project Details */}
          <div className="space-y-4">
            {selectedProject ? (
              <>
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide">
                    Project Details
                  </h2>
                  <Link
                    to={`/jobs/${selectedProject.id}`}
                    className="text-sm text-blue-400 hover:text-blue-300"
                  >
                    View full job →
                  </Link>
                </div>

                {/* SST Context from Airtable */}
                {loadingSst ? (
                  <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
                    <div className="text-gray-400 text-sm">Loading metadata...</div>
                  </div>
                ) : sstMetadata && (
                  <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
                    {/* Title Row */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        {sstMetadata.release_title && (
                          <h3 className="text-white font-medium truncate" title={sstMetadata.release_title}>
                            {sstMetadata.release_title}
                          </h3>
                        )}
                        {sstMetadata.media_id && (
                          <span className="text-xs text-gray-400 font-mono">
                            {sstMetadata.media_id}
                          </span>
                        )}
                      </div>
                      {sstMetadata.airtable_url && (
                        <a
                          href={sstMetadata.airtable_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap"
                        >
                          SST →
                        </a>
                      )}
                    </div>

                    {/* Description */}
                    {sstMetadata.short_description && (
                      <p className="text-sm text-gray-300 line-clamp-3">
                        {sstMetadata.short_description}
                      </p>
                    )}

                    {/* Links */}
                    {(sstMetadata.media_manager_url || sstMetadata.youtube_url) && (
                      <div className="flex gap-3 text-xs">
                        {sstMetadata.media_manager_url && (
                          <a
                            href={sstMetadata.media_manager_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-400 hover:text-blue-300"
                          >
                            PBS Website
                          </a>
                        )}
                        {sstMetadata.youtube_url && (
                          <a
                            href={sstMetadata.youtube_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-red-400 hover:text-red-300"
                          >
                            YouTube
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Phase Stats */}
                <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
                  <h3 className="text-sm font-medium text-gray-400 mb-3">
                    Agent Phases
                  </h3>
                  <div className="space-y-2">
                    {selectedProject.phases?.map((phase, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between py-2 border-b border-gray-700 last:border-0"
                      >
                        <div className="flex items-center space-x-3">
                          {getPhaseIcon(phase.status)}
                          <span className="text-white capitalize">{phase.name}</span>
                        </div>
                        <div className="flex items-center space-x-4 text-sm">
                          <span className="text-green-400 font-mono">
                            {formatCost(phase.cost || 0)}
                          </span>
                          <span className="text-gray-400">
                            {(phase.tokens || 0).toLocaleString()} tokens
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Artifacts */}
                <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
                  <h3 className="text-sm font-medium text-gray-400 mb-3">
                    Artifacts
                  </h3>
                  {artifacts.length === 0 ? (
                    <div className="text-gray-300 text-sm">No artifacts found</div>
                  ) : (
                    <div className="space-y-1">
                      {artifacts.map((artifact, idx) => (
                        <div
                          key={idx}
                          className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-700/50"
                        >
                          <div className="flex items-center space-x-3">
                            <span className="text-gray-400">
                              {artifact.type === 'directory' ? '📁' : '📄'}
                            </span>
                            <span className="text-white text-sm">
                              {artifact.label}
                            </span>
                          </div>
                          <button
                            onClick={(e) => handleViewArtifact(artifact, e)}
                            disabled={loadingArtifact}
                            className="text-blue-400 hover:text-blue-300 text-sm disabled:opacity-50"
                            aria-label={`View ${artifact.label}`}
                          >
                            {loadingArtifact ? 'Loading...' : 'View →'}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Screengrabs */}
                {sstMetadata?.media_id && (
                  <ScreengrabsBox mediaId={sstMetadata.media_id} />
                )}

                {/* Project Path */}
                <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
                  <h3 className="text-sm font-medium text-gray-400 mb-2">
                    Output Location
                  </h3>
                  <code className="text-sm text-emerald-400 font-mono break-all">
                    {selectedProject.project_path}
                  </code>
                </div>
              </>
            ) : (
              <div className="bg-gray-800 rounded-lg border border-gray-700 p-8 text-center">
                <p className="text-gray-300">
                  Select a project to view details and artifacts
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center space-x-4 py-4">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-700 transition-colors"
          >
            ← Previous
          </button>
          <span className="text-gray-400">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-700 transition-colors"
          >
            Next →
          </button>
        </div>
      )}

      {/* Artifact Viewer Modal */}
      {viewingArtifact && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={closeModal}
        >
          <div
            ref={modalRef}
            className="bg-gray-900 rounded-lg border border-gray-700 w-full max-w-4xl max-h-[90vh] flex flex-col"
            role="dialog"
            aria-modal="true"
            aria-labelledby="artifact-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <h3 id="artifact-modal-title" className="text-lg font-medium text-white">
                {viewingArtifact.label}
                <span className="ml-2 text-sm text-gray-400 font-mono font-normal">
                  {viewingArtifact.name}
                </span>
              </h3>
              <button
                onClick={closeModal}
                className="text-gray-400 hover:text-white text-2xl leading-none"
                aria-label="Close artifact viewer"
              >
                &times;
              </button>
            </div>
            {/* Modal Content */}
            <div className="flex-1 overflow-auto p-4">
              {viewingArtifact.isJson ? (
                <pre className="text-sm text-gray-300 whitespace-pre-wrap font-mono">
                  {JSON.stringify(JSON.parse(viewingArtifact.content), null, 2)}
                </pre>
              ) : (
                <ProseContainer content={viewingArtifact.content} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
