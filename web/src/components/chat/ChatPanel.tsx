import { useState, useRef, useEffect, useCallback } from 'react'
import { useToast } from '../ui/Toast'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface ChatPanelProps {
  projectName: string
  onClose: () => void
}

interface ChatResponse {
  response: string
  tokens_used: number
  cost: number
  model: string
}

export default function ChatPanel({ projectName, onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [totalCost, setTotalCost] = useState(0)
  const [totalTokens, setTotalTokens] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = useCallback(async () => {
    if (!input.trim() || loading) return

    const userMessage = input.trim()
    setInput('')
    setError(null)

    // Add user message immediately
    const newMessages: Message[] = [...messages, { role: 'user', content: userMessage }]
    setMessages(newMessages)
    setLoading(true)

    try {
      const response = await fetch('/api/chat/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          project_name: projectName,
          conversation_history: newMessages
        })
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }))
        throw new Error(errorData.detail || 'Failed to send message')
      }

      const data: ChatResponse = await response.json()

      // Add assistant response
      setMessages([...newMessages, { role: 'assistant', content: data.response }])

      // Update totals
      setTotalCost(prev => prev + data.cost)
      setTotalTokens(prev => prev + data.tokens_used)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to send message'
      setError(errorMessage)
      toast(errorMessage, 'error')
    } finally {
      setLoading(false)
    }
  }, [input, messages, projectName, loading, toast])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const handleRetry = () => {
    setError(null)
    if (messages.length > 0 && messages[messages.length - 1].role === 'user') {
      // Retry the last user message
      const lastUserMessage = messages[messages.length - 1].content
      setMessages(messages.slice(0, -1))
      setInput(lastUserMessage)
    }
  }

  return (
    <div className="h-full flex flex-col bg-surface-900 border-l border-surface-700">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-surface-800 border-b border-surface-700">
        <div className="flex items-center space-x-3">
          <h2 className="text-lg font-semibold text-white">Chat Assistant</h2>
          <div className="text-xs text-surface-400">
            ${totalCost.toFixed(4)} • {totalTokens.toLocaleString()} tokens
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-surface-400 hover:text-white transition-colors p-1"
          aria-label="Close chat"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Message List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-surface-400">
              <p className="text-lg mb-2">No messages yet</p>
              <p className="text-sm">Ask a question about {projectName}</p>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message, index) => (
              <div
                key={index}
                className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[80%] rounded-lg px-4 py-2 ${
                    message.role === 'user'
                      ? 'bg-pbs-500 text-white'
                      : 'bg-surface-700 text-white'
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </>
        )}

        {/* Loading Indicator */}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-surface-700 rounded-lg px-4 py-2">
              <p className="text-surface-400 animate-pulse">Thinking...</p>
            </div>
          </div>
        )}
      </div>

      {/* Error Message */}
      {error && (
        <div className="px-4 py-2 bg-red-900/20 border-t border-red-500/30">
          <div className="flex items-center justify-between">
            <p className="text-red-400 text-sm">{error}</p>
            <button
              onClick={handleRetry}
              className="text-sm text-red-400 hover:text-red-300 underline"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="p-4 bg-surface-800 border-t border-surface-700">
        <div className="flex space-x-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
            className="flex-1 bg-surface-900 text-white border border-surface-700 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pbs-400 disabled:opacity-50 resize-none"
            rows={3}
            aria-label="Chat message input"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || loading}
            className="px-4 py-2 bg-pbs-500 hover:bg-pbs-400 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors self-end"
            aria-label="Send message"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
        <p className="text-xs text-surface-400 mt-2">
          Project: <span className="text-surface-400 font-mono">{projectName}</span>
        </p>
      </div>
    </div>
  )
}
