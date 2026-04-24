import { useState, useEffect, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import guideContent from '../../../docs/WEB_UI_GUIDE.md?raw'

interface TocEntry {
  id: string
  text: string
  level: number
}

function extractToc(markdown: string): TocEntry[] {
  const headingRegex = /^(#{1,3})\s+(.+)$/gm
  const entries: TocEntry[] = []
  let match

  while ((match = headingRegex.exec(markdown)) !== null) {
    const level = match[1].length
    const text = match[2]
    const id = text
      .toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .replace(/\s+/g, '-')
    entries.push({ id, text, level })
  }

  return entries
}

export default function Help() {
  const [activeSection, setActiveSection] = useState('')
  const toc = useMemo(() => extractToc(guideContent), [])

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id)
          }
        }
      },
      { rootMargin: '-80px 0px -60% 0px' }
    )

    const timer = setTimeout(() => {
      toc.forEach(({ id }) => {
        const el = document.getElementById(id)
        if (el) observer.observe(el)
      })
    }, 100)

    return () => {
      clearTimeout(timer)
      observer.disconnect()
    }
  }, [toc])

  const scrollTo = (id: string) => {
    const el = document.getElementById(id)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  return (
    <div className="lg:flex lg:gap-8">
      {/* Mobile TOC — collapsible, shown below lg: */}
      <details className="lg:hidden mb-6">
        <summary className="flex items-center justify-between px-4 py-3 bg-surface-800 rounded-lg border border-surface-700 cursor-pointer text-sm font-display font-semibold text-surface-300 select-none">
          <span>Contents</span>
          <svg className="w-4 h-4 text-surface-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </summary>
        <nav className="mt-2 px-4 py-3 bg-surface-800 rounded-lg border border-surface-700" aria-label="Table of contents">
          <ul className="space-y-1">
            {toc.filter(e => e.level <= 2).map((entry) => (
              <li key={entry.id}>
                <button
                  onClick={() => {
                    scrollTo(entry.id)
                    const details = document.querySelector('details.lg\\:hidden') as HTMLDetailsElement
                    if (details) details.open = false
                  }}
                  className={`block w-full text-left text-sm px-2 py-1 rounded transition-colors ${
                    entry.level === 2 ? 'pl-4' : ''
                  } ${
                    activeSection === entry.id
                      ? 'text-pbs-400 bg-surface-700'
                      : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
                  }`}
                >
                  {entry.text}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </details>

      {/* Table of Contents Sidebar */}
      <nav
        className="hidden lg:block w-56 flex-shrink-0 sticky top-20 self-start max-h-[calc(100vh-6rem)] overflow-y-auto"
        aria-label="Table of contents"
      >
        <h2 className="text-sm font-display font-semibold text-surface-400 uppercase tracking-wider mb-3">
          Contents
        </h2>
        <ul className="space-y-1">
          {toc.filter(e => e.level <= 2).map((entry) => (
            <li key={entry.id}>
              <button
                onClick={() => scrollTo(entry.id)}
                className={`block w-full text-left text-sm px-2 py-1 rounded transition-colors ${
                  entry.level === 2 ? 'pl-4' : ''
                } ${
                  activeSection === entry.id
                    ? 'text-pbs-400 bg-surface-800'
                    : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
                }`}
              >
                {entry.text}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* Main Content */}
      <article className="flex-1 min-w-0">
        <div className="bg-surface-800 rounded-lg border border-surface-700 p-4 sm:p-6 lg:p-8">
          <div className="prose prose-invert max-w-none
            prose-headings:font-display prose-headings:scroll-mt-20
            prose-h1:text-[var(--text-3xl)] prose-h1:font-bold prose-h1:text-white prose-h1:border-b prose-h1:border-surface-700 prose-h1:pb-3 prose-h1:mb-6
            prose-h2:text-[var(--text-2xl)] prose-h2:font-semibold prose-h2:text-white prose-h2:mt-10 prose-h2:mb-4
            prose-h3:text-[var(--text-xl)] prose-h3:font-medium prose-h3:text-surface-200
            prose-p:text-surface-300 prose-p:leading-relaxed prose-p:max-w-[75ch]
            prose-a:text-pbs-400 prose-a:no-underline hover:prose-a:underline
            prose-strong:text-white
            prose-code:text-pbs-300 prose-code:bg-surface-800 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-sm prose-code:font-mono
            prose-table:border-collapse
            prose-th:bg-surface-850 prose-th:text-surface-200 prose-th:text-left prose-th:px-3 prose-th:py-2 prose-th:border prose-th:border-surface-700 prose-th:text-sm
            prose-td:px-3 prose-td:py-2 prose-td:border prose-td:border-surface-700 prose-td:text-sm prose-td:text-surface-300
            prose-li:text-surface-300
            prose-hr:border-surface-700
          ">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({ children, ...props }) => {
                  const text = String(children)
                  const id = text.toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-')
                  return <h1 id={id} {...props}>{children}</h1>
                },
                h2: ({ children, ...props }) => {
                  const text = String(children)
                  const id = text.toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-')
                  return <h2 id={id} {...props}>{children}</h2>
                },
                h3: ({ children, ...props }) => {
                  const text = String(children)
                  const id = text.toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-')
                  return <h3 id={id} {...props}>{children}</h3>
                },
              }}
            >
              {guideContent}
            </ReactMarkdown>
          </div>
        </div>
      </article>
    </div>
  )
}
