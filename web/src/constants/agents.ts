/**
 * Agent metadata constants for consistent display across the application.
 *
 * This is the single source of truth for agent IDs, display names, icons,
 * and descriptions used in Settings, System, and other pages.
 */

export interface AgentInfo {
  id: string
  name: string
  icon: string
  description: string
}

/**
 * Complete list of AI agents in the editorial pipeline.
 * The order here determines display order in the UI.
 */
export const AGENT_INFO: AgentInfo[] = [
  {
    id: 'analyst',
    name: 'Analyst',
    icon: '🔍',
    description:
      'Analyzes raw transcripts to identify key topics, themes, speakers and structural elements. Produces a detailed analysis document that guides downstream agents.',
  },
  {
    id: 'formatter',
    name: 'Formatter',
    icon: '📝',
    description:
      'Transforms raw transcripts into clean, readable markdown. Handles speaker attribution, paragraph breaks, timestamps and basic structural formatting.',
  },
  {
    id: 'seo',
    name: 'SEO Specialist',
    icon: '🎯',
    description:
      'Generates search-optimized metadata including titles, descriptions, tags and keywords. Optimizes for streaming platform discovery and search rankings.',
  },
  {
    id: 'manager',
    name: 'QA Manager',
    icon: '✅',
    description:
      'Reviews all automated outputs for quality before completion. Audits cheaper model work and flags issues. Always runs on big-brain tier for oversight.',
  },
  {
    id: 'timestamp',
    name: 'Timestamp',
    icon: '⏱️',
    description:
      'Generates chapter timestamps with descriptive labels for long-form content. Uses SRT captions to identify topic transitions and segment boundaries.',
  },
  {
    id: 'copy_editor',
    name: 'Copy Editor',
    icon: '✏️',
    description:
      'Reviews and refines formatted content for clarity, grammar and PBS style guidelines. Ensures broadcast-quality prose while preserving speaker voice.',
  },
]
