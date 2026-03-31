import { useState, useCallback } from 'react'
import { useToast } from './ui/Toast'

interface UploadStatus {
  filename: string
  success: boolean
  job_id?: number
  error?: string
  uploading?: boolean
}

interface UploadResponse {
  uploaded: number
  failed: number
  files: UploadStatus[]
}

interface TranscriptUploaderProps {
  onUploadComplete?: () => void
}

const ALLOWED_EXTENSIONS = ['.txt', '.srt']
const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50 MB
const MAX_BATCH_SIZE = 20

export default function TranscriptUploader({ onUploadComplete }: TranscriptUploaderProps) {
  const [files, setFiles] = useState<File[]>([])
  const [uploadStatuses, setUploadStatuses] = useState<UploadStatus[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const { toast } = useToast()

  const validateFile = (file: File): string | null => {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      return `Invalid file type. Allowed: ${ALLOWED_EXTENSIONS.join(', ')}`
    }
    if (file.size > MAX_FILE_SIZE) {
      return `File too large. Maximum: ${MAX_FILE_SIZE / 1024 / 1024}MB`
    }
    return null
  }

  const handleFiles = useCallback((newFiles: FileList | null) => {
    if (!newFiles) return

    const fileArray = Array.from(newFiles)
    const validFiles: File[] = []
    const errors: string[] = []

    for (const file of fileArray) {
      const error = validateFile(file)
      if (error) {
        errors.push(`${file.name}: ${error}`)
      } else {
        validFiles.push(file)
      }
    }

    if (errors.length > 0) {
      errors.forEach(err => toast(err, 'error'))
    }

    setFiles(prev => {
      const combined = [...prev, ...validFiles]
      if (combined.length > MAX_BATCH_SIZE) {
        toast(`Maximum ${MAX_BATCH_SIZE} files allowed. Extra files ignored.`, 'warning')
        return combined.slice(0, MAX_BATCH_SIZE)
      }
      return combined
    })
  }, [toast])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
    handleFiles(e.dataTransfer.files)
  }, [handleFiles])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files)
  }, [handleFiles])

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  const clearAll = () => {
    setFiles([])
    setUploadStatuses([])
  }

  const uploadFiles = async () => {
    if (files.length === 0) return

    setIsUploading(true)
    setUploadStatuses([])

    const formData = new FormData()
    files.forEach(file => {
      formData.append('files', file)
    })

    try {
      const response = await fetch('/api/upload/transcripts', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        let errorMessage = `Upload failed (${response.status})`
        try {
          const error = await response.json()
          errorMessage = error.detail || errorMessage
        } catch {
          // Response wasn't JSON (e.g. HTML error page)
        }
        throw new Error(errorMessage)
      }

      const result: UploadResponse = await response.json()
      setUploadStatuses(result.files)

      if (result.uploaded > 0) {
        toast(
          `Successfully uploaded ${result.uploaded} file${result.uploaded !== 1 ? 's' : ''}`,
          'success'
        )
        onUploadComplete?.()
      }

      if (result.failed > 0) {
        toast(
          `Failed to upload ${result.failed} file${result.failed !== 1 ? 's' : ''}`,
          'error'
        )
      }

      // Clear successfully uploaded files
      setFiles(prev =>
        prev.filter((_, idx) => {
          const status = result.files[idx]
          return status && !status.success
        })
      )
    } catch (err) {
      console.error('Upload error:', err)
      toast(err instanceof Error ? err.message : 'Upload failed', 'error')
    } finally {
      setIsUploading(false)
    }
  }

  const getStatusIcon = (status: UploadStatus) => {
    if (status.uploading) {
      return <span className="text-blue-400 animate-spin">⟳</span>
    }
    if (status.success) {
      return <span className="text-green-400">✓</span>
    }
    return <span className="text-red-400">✗</span>
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white">Upload Transcripts</h3>
        {files.length > 0 && !isUploading && (
          <button
            onClick={clearAll}
            className="text-sm text-gray-400 hover:text-white transition-colors"
          >
            Clear all
          </button>
        )}
      </div>

      {/* Drop Zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`
          border-2 border-dashed rounded-lg p-8 text-center transition-colors
          ${isDragging
            ? 'border-blue-500 bg-blue-500/10'
            : 'border-gray-600 hover:border-gray-500 bg-gray-900/50'
          }
        `}
      >
        <div className="space-y-2">
          <p className="text-gray-300">
            Drag and drop transcript files here, or
          </p>
          <label className="inline-block">
            <input
              type="file"
              multiple
              accept=".txt,.srt"
              onChange={handleFileInput}
              className="hidden"
              disabled={isUploading}
            />
            <span className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg cursor-pointer transition-colors inline-block">
              Browse files
            </span>
          </label>
          <p className="text-sm text-gray-400">
            .txt or .srt files, up to {MAX_FILE_SIZE / 1024 / 1024}MB each, max {MAX_BATCH_SIZE} files
          </p>
        </div>
      </div>

      {/* File List */}
      {files.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-gray-300">
            {files.length} file{files.length !== 1 ? 's' : ''} ready to upload
          </h4>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {files.map((file, idx) => {
              const status = uploadStatuses[idx]
              return (
                <div
                  key={idx}
                  className="flex items-center justify-between bg-gray-900 rounded px-3 py-2 text-sm"
                >
                  <div className="flex items-center space-x-2 flex-1 min-w-0">
                    {status && getStatusIcon(status)}
                    <span className="text-gray-300 truncate">{file.name}</span>
                    <span className="text-gray-500 text-xs whitespace-nowrap">
                      ({(file.size / 1024).toFixed(1)} KB)
                    </span>
                  </div>
                  <div className="flex items-center space-x-2">
                    {status?.success && status.job_id && (
                      <a
                        href={`/jobs/${status.job_id}`}
                        className="text-blue-400 hover:text-blue-300 text-xs whitespace-nowrap"
                      >
                        Job #{status.job_id}
                      </a>
                    )}
                    {status?.error && (
                      <span className="text-red-400 text-xs max-w-xs truncate" title={status.error}>
                        {status.error}
                      </span>
                    )}
                    {!isUploading && !status && (
                      <button
                        onClick={() => removeFile(idx)}
                        className="text-gray-500 hover:text-red-400 transition-colors"
                        aria-label="Remove file"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Upload Button */}
      {files.length > 0 && (
        <button
          onClick={uploadFiles}
          disabled={isUploading}
          className={`
            w-full py-2 px-4 rounded-lg font-medium transition-colors
            ${isUploading
              ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
              : 'bg-green-600 hover:bg-green-500 text-white'
            }
          `}
        >
          {isUploading ? 'Uploading...' : `Upload ${files.length} file${files.length !== 1 ? 's' : ''}`}
        </button>
      )}
    </div>
  )
}
