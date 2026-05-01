import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface ProseContainerProps {
  content: string
  className?: string
}

export default function ProseContainer({ content, className = '' }: ProseContainerProps) {
  return (
    <div className={`prose prose-invert prose-sm max-w-none
      prose-headings:text-white
      prose-h1:text-xl prose-h1:font-bold prose-h1:border-b prose-h1:border-gray-700 prose-h1:pb-2 prose-h1:mb-4
      prose-h2:text-lg prose-h2:font-semibold prose-h2:mt-6 prose-h2:mb-3
      prose-h3:text-base prose-h3:font-medium prose-h3:text-gray-200
      prose-p:text-gray-300 prose-p:leading-relaxed
      prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
      prose-strong:text-white
      prose-code:text-blue-300 prose-code:bg-gray-900 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-sm
      prose-table:border-collapse
      prose-th:bg-gray-900 prose-th:text-gray-200 prose-th:text-left prose-th:px-3 prose-th:py-2 prose-th:border prose-th:border-gray-700 prose-th:text-sm
      prose-td:px-3 prose-td:py-2 prose-td:border prose-td:border-gray-700 prose-td:text-sm prose-td:text-gray-300
      prose-li:text-gray-300
      prose-hr:border-gray-700
      ${className}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}
