/**
 * Centralized artifact label mapping
 *
 * Maps technical filenames to user-friendly display labels.
 * Used across the web dashboard for consistent artifact naming.
 */

export interface ArtifactInfo {
  filename: string
  label: string
  description?: string
}

/**
 * Map of artifact keys to their display info.
 * Keys are semantic identifiers used in the API (e.g., 'analysis', 'formatted_transcript')
 */
export const ARTIFACT_LABELS: Record<string, ArtifactInfo> = {
  // Core processing outputs
  analysis: {
    filename: 'analyst_output.md',
    label: 'Analysis',
    description: 'AI-generated brainstorming with title suggestions, descriptions, and keywords'
  },
  formatted_transcript: {
    filename: 'formatter_output.md',
    label: 'Formatted Transcript',
    description: 'AP Style formatted transcript with speaker labels'
  },
  seo_metadata: {
    filename: 'seo_output.md',
    label: 'SEO Metadata',
    description: 'Search-optimized titles, descriptions, and tags'
  },
  qa_review: {
    filename: 'validator_output.md',
    label: 'QA Review',
    description: 'Quality assurance review and final recommendations'
  },
  timestamp_report: {
    filename: 'timestamp_output.md',
    label: 'Timestamps',
    description: 'Chapter markers and key moments'
  },
  copy_edited: {
    filename: 'copy_editor_output.md',
    label: 'Copy Edited',
    description: 'Human-reviewed and polished metadata'
  },

  // Recovery/diagnostic outputs
  recovery_analysis: {
    filename: 'recovery_analysis.md',
    label: 'Recovery Analysis',
    description: 'Analysis from failed job recovery attempt'
  },
  investigation: {
    filename: 'investigation_report.md',
    label: 'Failure Investigation',
    description: 'Diagnostic report for failed processing'
  },

  // System files
  manifest: {
    filename: 'manifest.json',
    label: 'Job Manifest',
    description: 'Processing metadata and phase history'
  }
}

/**
 * Reverse lookup: get artifact info by filename
 */
export const FILENAME_TO_ARTIFACT: Record<string, ArtifactInfo & { key: string }> =
  Object.entries(ARTIFACT_LABELS).reduce((acc, [key, info]) => {
    acc[info.filename] = { ...info, key }
    return acc
  }, {} as Record<string, ArtifactInfo & { key: string }>)

/**
 * Get a friendly label for a filename, falling back to the filename if unknown
 */
export function getArtifactLabel(filename: string): string {
  return FILENAME_TO_ARTIFACT[filename]?.label || filename
}

/**
 * Get full artifact info for a filename
 */
export function getArtifactInfo(filename: string): ArtifactInfo | null {
  return FILENAME_TO_ARTIFACT[filename] || null
}
