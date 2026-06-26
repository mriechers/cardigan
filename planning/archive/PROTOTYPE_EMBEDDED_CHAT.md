# Embedded Chat Prototype - Sprint Specification

**Goal:** Validate the embedded chat interaction pattern with a minimal implementation before committing to the full 3-4 sprint build.

**Status:** Prototype specification
**Estimated Effort:** 1 sprint (5-7 days)
**Last Updated:** January 2026

---

## Prototype Philosophy

> "Build the smallest thing that proves the concept works."

This prototype intentionally omits production features (streaming, persistence, attachments) to focus on validating:
1. **User experience** - Does in-dashboard chat feel natural for editing workflows?
2. **Context injection** - Does the LLM have enough context to be useful?
3. **Integration points** - Do our existing tools work well for chat context?
4. **Performance** - Is response latency acceptable?

---

## Scope: What's IN the Prototype

### Backend (2-3 days)

**File: `api/routers/chat_prototype.py`**

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.services.llm import LLMClient
from mcp_server.server import load_project_for_editing, get_sst_metadata

router = APIRouter(prefix="/api/chat", tags=["chat-prototype"])

class ChatRequest(BaseModel):
    message: str
    project_name: str | None = None
    conversation_history: list[dict] = []

class ChatResponse(BaseModel):
    response: str
    tokens_used: int
    cost: float

@router.post("/message", response_model=ChatResponse)
async def send_message(request: ChatRequest):
    """
    Simple REST endpoint for chat.
    No streaming, no persistence - just validate the interaction pattern.
    """
    # Build context from project if provided
    system_context = build_chat_context(request.project_name)

    # Build messages array
    messages = [{"role": "system", "content": system_context}]
    messages.extend(request.conversation_history)
    messages.append({"role": "user", "content": request.message})

    # Call LLM
    llm = LLMClient()
    response = await llm.chat_completion(messages)

    return ChatResponse(
        response=response.content,
        tokens_used=response.usage.total_tokens,
        cost=response.cost
    )

def build_chat_context(project_name: str | None) -> str:
    """Build system prompt with project context."""
    base_prompt = load_editor_personality()

    if not project_name:
        return base_prompt

    # Load project context using existing MCP tools
    try:
        project_data = load_project_for_editing(project_name)
        sst_data = get_sst_metadata(project_name)

        context_parts = [
            base_prompt,
            "\n\n## Current Project Context\n",
            f"**Project:** {project_name}\n",
            f"**Title:** {sst_data.get('title', 'Unknown')}\n",
            f"**Description:** {sst_data.get('description', 'Not set')}\n",
            "\n### Transcript Excerpt\n",
            project_data.get('transcript', '')[:10000],  # First 10k chars
            "\n### Brainstorming Notes\n",
            project_data.get('brainstorming', '')[:5000],
        ]
        return "\n".join(context_parts)
    except Exception as e:
        return f"{base_prompt}\n\n[Error loading project: {e}]"

def load_editor_personality() -> str:
    """Load Cardigan editor personality from agent instructions."""
    try:
        with open("claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md") as f:
            return f.read()
    except:
        return "You are a helpful editorial assistant for PBS Wisconsin."
```

**Additions to `api/services/llm.py`:**

```python
async def chat_completion(self, messages: list[dict]) -> LLMResponse:
    """
    Simple chat completion without streaming.
    Uses existing OpenRouter infrastructure.
    """
    # Use the chat model from config
    model = self.config.get("chat_model", "anthropic/claude-sonnet-4")

    response = await self._call_openrouter(
        model=model,
        messages=messages,
        stream=False
    )

    return LLMResponse(
        content=response.choices[0].message.content,
        usage=response.usage,
        cost=self._calculate_cost(model, response.usage)
    )
```

### Frontend (3-4 days)

**File: `web/src/components/chat/ChatPrototype.tsx`**

```tsx
import { useState } from 'react';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface ChatPrototypeProps {
  projectName?: string;
}

export function ChatPrototype({ projectName }: ChatPrototypeProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [totalCost, setTotalCost] = useState(0);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMessage = { role: 'user' as const, content: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await fetch('/api/chat/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: input,
          project_name: projectName,
          conversation_history: messages,
        }),
      });

      const data = await response.json();

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.response
      }]);
      setTotalCost(prev => prev + data.cost);
    } catch (error) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Error: Failed to get response'
      }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full border rounded-lg">
      {/* Header */}
      <div className="p-3 border-b bg-gray-50 flex justify-between">
        <span className="font-medium">
          Chat {projectName && `- ${projectName}`}
        </span>
        <span className="text-sm text-gray-500">
          Cost: ${totalCost.toFixed(4)}
        </span>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <p className="text-gray-500 text-center">
            Start a conversation about {projectName || 'your project'}
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`p-3 rounded-lg ${
              msg.role === 'user'
                ? 'bg-blue-100 ml-8'
                : 'bg-gray-100 mr-8'
            }`}
          >
            <p className="whitespace-pre-wrap">{msg.content}</p>
          </div>
        ))}
        {loading && (
          <div className="bg-gray-100 mr-8 p-3 rounded-lg animate-pulse">
            Thinking...
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-3 border-t flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Ask about this project..."
          className="flex-1 px-3 py-2 border rounded"
          disabled={loading}
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          className="px-4 py-2 bg-blue-500 text-white rounded disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  );
}
```

**Integration Point: `web/src/pages/JobDetail.tsx`**

Add a chat panel toggle to the job detail page:

```tsx
import { ChatPrototype } from '../components/chat/ChatPrototype';

// In JobDetail component:
const [showChat, setShowChat] = useState(false);

// Add button in header
<button onClick={() => setShowChat(!showChat)}>
  {showChat ? 'Hide Chat' : 'Open Chat'}
</button>

// Add panel (could be sidebar or modal)
{showChat && (
  <div className="w-96 h-[600px]">
    <ChatPrototype projectName={job.project_name} />
  </div>
)}
```

---

## Scope: What's OUT of the Prototype

| Feature | Why Excluded | When to Add |
|---------|--------------|-------------|
| **Token streaming** | Adds WebSocket complexity | Phase 2 |
| **Session persistence** | Requires DB schema | Phase 2 |
| **File attachments** | UI complexity | Phase 2 |
| **Artifact rendering** | Markdown parsing, special UI | Phase 2 |
| **Mobile responsive** | Focus on desktop first | Phase 2 |
| **Keyboard shortcuts** | Nice-to-have | Phase 2 |
| **Cost limits/warnings** | Edge case handling | Phase 2 |

---

## Validation Criteria

### Must Validate (Prototype Success Criteria)

| Criteria | How to Test | Target |
|----------|-------------|--------|
| **Context injection works** | Ask about transcript content | LLM correctly references project |
| **Response quality** | Compare to Claude Desktop MCP | Comparable or better |
| **Latency acceptable** | Time from send to response | <5s for typical queries |
| **Conversation coherent** | Multi-turn dialogue | Maintains context across turns |
| **Cost reasonable** | Track per-conversation cost | <$0.05 for typical session |

### User Experience Questions

After prototype testing, gather feedback on:
1. Does the chat panel placement feel natural?
2. Is project context automatically useful, or do users need to specify?
3. What's missing that would make this the primary editing interface?
4. Would users prefer this over Claude Desktop MCP?

---

## Implementation Tasks

### Day 1-2: Backend
- [ ] Create `api/routers/chat_prototype.py` with REST endpoint
- [ ] Add `chat_completion` method to LLMClient
- [ ] Wire up context building from existing MCP tools
- [ ] Add route to `api/main.py`
- [ ] Test endpoint with curl/httpie

### Day 3-4: Frontend
- [ ] Create `ChatPrototype.tsx` component
- [ ] Add basic styling (can use existing Tailwind classes)
- [ ] Integrate into JobDetail page with toggle
- [ ] Test full flow: open project → open chat → send message

### Day 5: Validation & Documentation
- [ ] Test with real projects (2-3 different types)
- [ ] Measure latency and cost
- [ ] Document findings in this file
- [ ] Decide: proceed to Phase 2 or iterate on prototype?

---

## Configuration

Add to `config/llm-config.json`:

```json
{
  "chat_model": "anthropic/claude-sonnet-4",
  "chat_max_tokens": 4096,
  "chat_temperature": 0.7
}
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| **Context too large** | Truncate transcript to first 10k chars; show warning |
| **High cost per message** | Display running cost; use cheaper model for prototype |
| **Slow responses** | Show "Thinking..." indicator; consider timeout |
| **LLM errors** | Graceful error display; retry button |

---

## Decision Point

After prototype completion, decide:

| If... | Then... |
|-------|---------|
| UX feels good, latency acceptable | Proceed to Phase 2 full implementation |
| Context injection insufficient | Redesign context builder before Phase 2 |
| Users prefer Claude Desktop | Deprioritize embedded chat, enhance MCP instead |
| Cost too high | Investigate cheaper models or caching strategies |

---

## Next Steps After Prototype

If prototype validates the approach, Phase 2 adds:
1. WebSocket streaming for real-time responses
2. SQLite persistence for chat history
3. File upload and attachment support
4. Artifact rendering (revisions inline)
5. Session management (resume, clear, export)
6. Full mobile responsive design

See `planning/DESIGN_3.5.md` Part 1 for full Phase 2 specification.

---

## Document History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-14 | Claude Code | Initial prototype specification |
