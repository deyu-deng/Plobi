import { describe, expect, it } from 'vitest'

import { buildToolView } from '@/components/assistant-ui/tool/fallback-model'
import type { SessionMessage } from '@/types/plobi'

import type { ChatMessage, ChatMessagePart } from './chat-messages'
import {
  appendAssistantTextPart,
  appendReasoningPart,
  chatMessageText,
  preserveLocalAssistantErrors,
  renderMediaTags,
  toChatMessages,
  upsertToolPart
} from './chat-messages'

describe('toChatMessages', () => {
  it('shows the pre-rewrite sentence for a hard-routed user turn (WP-UI-NO-INTERNALS)', () => {
    const [message] = toChatMessages([
      {
        role: 'user',
        content:
          '明天的日常安排是什么\n\n[§8.2 硬路由 · 本回合强制] 上面这句话命中总秘书冻结话术，必须严格按序执行：\n1. 第一动作只能是调用工具',
        timestamp: 1
      }
    ])

    expect(chatMessageText(message)).toBe('明天的日常安排是什么')
  })

  it('leaves an ordinary user turn untouched', () => {
    const [message] = toChatMessages([{ role: 'user', content: '明天几点开会？', timestamp: 1 }])

    expect(chatMessageText(message)).toBe('明天几点开会？')
  })

  it('keeps a turn with interleaved tool-only rows in a single bubble', () => {
    const messages = toChatMessages([
      { role: 'assistant', content: 'Planning.', timestamp: 1 },
      {
        role: 'assistant',
        content: '',
        timestamp: 2,
        tool_calls: [{ id: 'tc', function: { name: 'terminal', arguments: '{}' } }]
      },
      { role: 'assistant', content: 'Done.', timestamp: 3 }
    ])

    expect(messages).toHaveLength(1)
    expect(messages[0].parts.map(p => p.type)).toEqual(['text', 'tool-call', 'text'])
    expect(chatMessageText(messages[0])).toBe('Planning.Done.')
  })

  it('keeps assistant tool-call iterations in one loaded assistant bubble', () => {
    const messages = toChatMessages([
      { role: 'user', content: 'check this repo', timestamp: 1 },
      {
        role: 'assistant',
        content: "Let me also check if there's a top-level lint workflow.",
        timestamp: 2,
        tool_calls: [{ id: 'tc-1', function: { name: 'search_files', arguments: '{"path":".github"}' } }]
      },
      {
        role: 'tool',
        tool_call_id: 'tc-1',
        tool_name: 'search_files',
        content: '{"error":"Path not found: /repo/.github"}',
        timestamp: 3
      },
      {
        role: 'assistant',
        content: 'No CI in this repo. Build is enough.',
        timestamp: 4,
        tool_calls: [{ id: 'tc-2', function: { name: 'terminal', arguments: '{"command":"git status --short"}' } }]
      },
      {
        role: 'tool',
        tool_call_id: 'tc-2',
        tool_name: 'terminal',
        content: '{"output":"M src/ui/components/image-distortion.tsx\\n","exit_code":0}',
        timestamp: 5
      },
      { role: 'assistant', content: 'Now let me check git status and commit.', timestamp: 6 }
    ])

    const assistantMessages = messages.filter(message => message.role === 'assistant')

    expect(assistantMessages).toHaveLength(1)
    expect(assistantMessages[0].parts.filter(part => part.type === 'tool-call')).toHaveLength(2)
    expect(chatMessageText(assistantMessages[0])).toContain("Let me also check if there's a top-level lint workflow.")
    expect(chatMessageText(assistantMessages[0])).toContain('Now let me check git status and commit.')
  })

  it('hides attached context payloads from user message display', () => {
    const [message] = toChatMessages([
      {
        role: 'user',
        content:
          'what is this file\n\n--- Attached Context ---\n\n📄 @file:tsconfig.tsbuildinfo (981 tokens)\n```json\n{"root":["./src/main.tsx"]}\n```',
        timestamp: 1
      }
    ])

    expect(chatMessageText(message)).toBe('@file:tsconfig.tsbuildinfo\n\nwhat is this file')
  })

  it('renders MEDIA tags as assistant attachment links', () => {
    const [message] = toChatMessages([
      {
        role: 'assistant',
        content: "MEDIA:/Users/brooklyn/.plobi/cache/audio/tts_20260501_222725.mp3\n\nhow's that sound?",
        timestamp: 1
      }
    ])

    expect(chatMessageText(message)).toBe(
      "[Audio: tts_20260501_222725.mp3](#media:%2FUsers%2Fbrooklyn%2F.plobi%2Fcache%2Faudio%2Ftts_20260501_222725.mp3)\n\nhow's that sound?"
    )
  })

  it('keeps the generated image on the tool row while preserving agent prose', () => {
    const [message] = toChatMessages([
      {
        content: '',
        role: 'assistant',
        timestamp: 1,
        tool_calls: [{ id: 'img-1', function: { name: 'image_generate', arguments: '{"prompt":"draw a cat"}' } }]
      },
      {
        content: '{"success":true,"image":"https://cdn.example/cat.png"}',
        role: 'tool',
        timestamp: 2,
        tool_call_id: 'img-1',
        tool_name: 'image_generate'
      },
      {
        content: 'Here you go.\n\n![Generated image](https://cdn.example/cat.png)',
        role: 'assistant',
        timestamp: 3
      }
    ])

    const toolPart = message.parts.find(
      (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
        part.type === 'tool-call' && part.toolName === 'image_generate'
    )

    expect(toolPart?.result).toMatchObject({ image: 'https://cdn.example/cat.png', success: true })
    // The duplicated image is stripped, but the agent's words survive.
    expect(chatMessageText(message)).toBe('Here you go.')
  })

  it('coerces non-string message content without throwing', () => {
    const [message] = toChatMessages([
      {
        content: {
          text: 'hello from object content'
        },
        role: 'assistant',
        timestamp: 1
      }
    ])

    expect(chatMessageText(message)).toBe('hello from object content')
  })

  it('applies attached-context filtering when user content is object-shaped', () => {
    const [message] = toChatMessages([
      {
        content: {
          text: 'look\n\n--- Attached Context ---\n\n📄 @file:foo.ts (10 tokens)\n```ts\nconst x = 1\n```'
        },
        role: 'user',
        timestamp: 1
      }
    ])

    expect(chatMessageText(message)).toBe('@file:foo.ts\n\nlook')
  })
})

describe('renderMediaTags', () => {
  it('renders standalone and inline MEDIA tags as links', () => {
    expect(renderMediaTags('here\nMEDIA:/tmp/voice.mp3\nthere')).toBe(
      'here\n[Audio: voice.mp3](#media:%2Ftmp%2Fvoice.mp3)\nthere'
    )
    expect(renderMediaTags('audio: MEDIA:/tmp/voice.mp3 done')).toBe(
      'audio: [Audio: voice.mp3](#media:%2Ftmp%2Fvoice.mp3) done'
    )
    expect(renderMediaTags('MEDIA:/tmp/demo.mp4')).toBe('[Video: demo.mp4](#media:%2Ftmp%2Fdemo.mp4)')
  })

  it('renders streamed assistant media once the tag is complete', () => {
    const parts = appendAssistantTextPart(appendAssistantTextPart([], 'ok\nMEDIA:'), '/tmp/voice.mp3')
    const text = chatMessageText({ id: 'a', role: 'assistant', parts })

    expect(text).toBe('ok\n[Audio: voice.mp3](#media:%2Ftmp%2Fvoice.mp3)')
  })
})

describe('interleaved reasoning/text coalescing', () => {
  it('keeps narration contiguous when reasoning interrupts mid-sentence', () => {
    // Models that interleave reasoning_content + content deltas emit
    // text → reasoning → text within one tool-bounded segment. The two text
    // fragments are really one sentence and must not be split by the
    // "Thinking" block between them.
    let parts: ChatMessagePart[] = appendAssistantTextPart([], 'Let me ')
    parts = appendReasoningPart(parts, 'checking the file...')
    parts = appendAssistantTextPart(parts, 'verify the full file is correct:')

    expect(parts.map(p => p.type)).toEqual(['text', 'reasoning'])
    expect((parts[0] as { text: string }).text).toBe('Let me verify the full file is correct:')
    expect((parts[1] as { text: string }).text).toBe('checking the file...')
  })

  it('merges reasoning bursts that straddle a narration fragment', () => {
    let parts: ChatMessagePart[] = appendReasoningPart([], 'first thought ')
    parts = appendAssistantTextPart(parts, 'Working on it.')
    parts = appendReasoningPart(parts, 'second thought')

    expect(parts.map(p => p.type)).toEqual(['reasoning', 'text'])
    expect((parts[0] as { text: string }).text).toBe('first thought second thought')
    expect((parts[1] as { text: string }).text).toBe('Working on it.')
  })

  it('starts a fresh text part after a tool call (segment boundary)', () => {
    let parts: ChatMessagePart[] = appendAssistantTextPart([], 'Let me check.')
    parts = upsertToolPart(parts, { name: 'read_file', tool_id: 'tc-1' }, 'running')
    parts = appendAssistantTextPart(parts, 'Now editing.')

    expect(parts.map(p => p.type)).toEqual(['text', 'tool-call', 'text'])
    expect((parts[0] as { text: string }).text).toBe('Let me check.')
    expect((parts[2] as { text: string }).text).toBe('Now editing.')
  })

  it('does not merge reasoning across a tool call', () => {
    let parts: ChatMessagePart[] = appendReasoningPart([], 'before tool')
    parts = upsertToolPart(parts, { name: 'read_file', tool_id: 'tc-1' }, 'running')
    parts = appendReasoningPart(parts, 'after tool')

    expect(parts.map(p => p.type)).toEqual(['reasoning', 'tool-call', 'reasoning'])
    expect((parts[0] as { text: string }).text).toBe('before tool')
    expect((parts[2] as { text: string }).text).toBe('after tool')
  })
})

describe('preserveLocalAssistantErrors', () => {
  it('preserves a local user+error pair when hydration omits the failed turn', () => {
    const nextMessages: ChatMessage[] = [
      {
        id: 'stored-user',
        parts: [{ text: 'earlier', type: 'text' }],
        role: 'user'
      }
    ]

    const currentMessages: ChatMessage[] = [
      {
        id: 'stored-user',
        parts: [{ text: 'earlier', type: 'text' }],
        role: 'user'
      },
      {
        id: 'user-123',
        parts: [{ text: 'new prompt', type: 'text' }],
        role: 'user'
      },
      {
        error: 'OpenRouter 403',
        id: 'assistant-error-1',
        parts: [],
        role: 'assistant'
      }
    ]

    const merged = preserveLocalAssistantErrors(nextMessages, currentMessages)

    expect(merged.map(message => message.id)).toEqual(['stored-user', 'user-123', 'assistant-error-1'])
    expect(merged[2]?.error).toBe('OpenRouter 403')
  })

  it('does not keep orphan local user turns when there is no inline assistant error', () => {
    const nextMessages: ChatMessage[] = [
      {
        id: 'stored-user',
        parts: [{ text: 'earlier', type: 'text' }],
        role: 'user'
      }
    ]

    const currentMessages: ChatMessage[] = [
      ...nextMessages,
      {
        id: 'user-123',
        parts: [{ text: 'new prompt', type: 'text' }],
        role: 'user'
      }
    ]

    const merged = preserveLocalAssistantErrors(nextMessages, currentMessages)

    expect(merged.map(message => message.id)).toEqual(['stored-user'])
  })

  it('does not duplicate local user when stored history already has equivalent text', () => {
    const nextMessages: ChatMessage[] = [
      {
        id: 'stored-user',
        parts: [{ text: 'hi', type: 'text' }],
        role: 'user'
      }
    ]

    const currentMessages: ChatMessage[] = [
      {
        id: 'optimistic-user',
        parts: [{ text: 'hi', type: 'text' }],
        role: 'user'
      },
      {
        error: 'OpenRouter 403',
        id: 'assistant-error-1',
        parts: [],
        role: 'assistant'
      }
    ]

    const merged = preserveLocalAssistantErrors(nextMessages, currentMessages)

    expect(merged.map(message => message.id)).toEqual(['stored-user', 'assistant-error-1'])
  })

  it('keeps local user when only older history has equivalent text', () => {
    const nextMessages: ChatMessage[] = [
      {
        id: 'older-user',
        parts: [{ text: 'hi', type: 'text' }],
        role: 'user'
      },
      {
        id: 'older-assistant',
        parts: [{ text: 'hello', type: 'text' }],
        role: 'assistant'
      },
      {
        id: 'tail-user',
        parts: [{ text: 'different prompt', type: 'text' }],
        role: 'user'
      }
    ]

    const currentMessages: ChatMessage[] = [
      {
        id: 'optimistic-user',
        parts: [{ text: 'hi', type: 'text' }],
        role: 'user'
      },
      {
        error: 'OpenRouter 403',
        id: 'assistant-error-1',
        parts: [],
        role: 'assistant'
      }
    ]

    const merged = preserveLocalAssistantErrors(nextMessages, currentMessages)

    expect(merged.map(message => message.id)).toEqual([
      'older-user',
      'older-assistant',
      'tail-user',
      'optimistic-user',
      'assistant-error-1'
    ])
  })

  it('keeps local assistant error when hydrated message reuses same id', () => {
    const nextMessages: ChatMessage[] = [
      {
        id: 'user-1',
        parts: [{ text: 'new prompt', type: 'text' }],
        role: 'user'
      },
      {
        id: 'assistant-stream-1',
        parts: [{ text: '', type: 'text' }],
        role: 'assistant'
      }
    ]

    const currentMessages: ChatMessage[] = [
      {
        id: 'user-1',
        parts: [{ text: 'new prompt', type: 'text' }],
        role: 'user'
      },
      {
        error: 'OpenRouter 403',
        id: 'assistant-stream-1',
        parts: [],
        role: 'assistant'
      }
    ]

    const merged = preserveLocalAssistantErrors(nextMessages, currentMessages)

    const assistant = merged.find(message => message.id === 'assistant-stream-1')

    expect(assistant?.error).toBe('OpenRouter 403')
    expect(assistant?.pending).toBe(false)
  })
})

describe('upsertToolPart', () => {
  it('preserves inline diffs from tool completion events', () => {
    const parts = upsertToolPart(
      [],
      {
        inline_diff: '--- a/foo.ts\n+++ b/foo.ts\n@@\n-old\n+new',
        name: 'patch',
        tool_id: 'tool-1'
      },
      'complete'
    )

    const [part] = parts

    expect(part?.type).toBe('tool-call')
    expect(part && 'result' in part ? part.result : undefined).toMatchObject({
      inline_diff: '--- a/foo.ts\n+++ b/foo.ts\n@@\n-old\n+new'
    })
  })

  it('keeps live todo rows stable across sparse progress payloads', () => {
    const first = upsertToolPart(
      [],
      {
        name: 'todo',
        todos: [{ content: 'Boil water', id: 'boil', status: 'in_progress' }],
        tool_id: 'todo-1'
      },
      'running'
    )

    const progressed = upsertToolPart(
      first,
      {
        name: 'todo',
        preview: 'updating plan',
        tool_id: 'todo-1'
      },
      'running'
    )

    const [part] = progressed
    const args = part && 'args' in part ? (part.args as Record<string, unknown>) : {}

    expect(args.todos).toEqual([{ content: 'Boil water', id: 'boil', status: 'in_progress' }])
  })

  it('archives todo state on completion and accepts explicit empty clears', () => {
    const started = upsertToolPart(
      [],
      {
        name: 'todo',
        todos: [{ content: 'Boil water', id: 'boil', status: 'in_progress' }],
        tool_id: 'todo-1'
      },
      'running'
    )

    const completed = upsertToolPart(
      started,
      {
        name: 'todo',
        tool_id: 'todo-1'
      },
      'complete'
    )

    const cleared = upsertToolPart(
      completed,
      {
        name: 'todo',
        todos: [],
        tool_id: 'todo-1'
      },
      'complete'
    )

    const completedResult =
      completed[0] && 'result' in completed[0] ? (completed[0].result as Record<string, unknown>) : {}

    const clearedResult = cleared[0] && 'result' in cleared[0] ? (cleared[0].result as Record<string, unknown>) : {}

    expect(completedResult.todos).toEqual([{ content: 'Boil water', id: 'boil', status: 'in_progress' }])
    expect(clearedResult.todos).toEqual([])
  })

  it('keeps parallel same-name tools distinct without explicit ids', () => {
    const startedTokyo = upsertToolPart(
      [],
      {
        context: 'tokyo weather',
        name: 'web_search'
      },
      'running'
    )

    const startedReykjavik = upsertToolPart(
      startedTokyo,
      {
        context: 'reykjavik weather',
        name: 'web_search'
      },
      'running'
    )

    const completedTokyo = upsertToolPart(
      startedReykjavik,
      {
        context: 'tokyo weather',
        message: 'tokyo done',
        name: 'web_search',
        summary: 'Did 5 searches'
      },
      'complete'
    )

    const completedBoth = upsertToolPart(
      completedTokyo,
      {
        context: 'reykjavik weather',
        message: 'reykjavik done',
        name: 'web_search',
        summary: 'Did 5 searches'
      },
      'complete'
    )

    const webParts = completedBoth.filter(
      (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
        part.type === 'tool-call' && part.toolName === 'web_search'
    )

    const contexts = webParts.map(part => String((part.args as Record<string, unknown>)?.context || ''))

    const summaries = webParts.map(part => {
      if (!('result' in part) || !part.result || typeof part.result !== 'object') {
        return ''
      }

      return String((part.result as Record<string, unknown>).summary || '')
    })

    expect(webParts).toHaveLength(2)
    expect(contexts).toEqual(['tokyo weather', 'reykjavik weather'])
    expect(summaries).toEqual(['Did 5 searches', 'Did 5 searches'])
  })

  it('preserves query args when completion payload omits context', () => {
    const started = upsertToolPart(
      [],
      {
        context: 'auckland weather today and tomorrow forecast',
        name: 'web_search',
        tool_id: 'search-1'
      },
      'running'
    )

    const completed = upsertToolPart(
      started,
      {
        duration_s: 1.1,
        name: 'web_search',
        summary: 'Did 5 searches in 1.1s',
        tool_id: 'search-1'
      },
      'complete'
    )

    const [part] = completed

    expect(part?.type).toBe('tool-call')
    expect((part as Extract<ChatMessagePart, { type: 'tool-call' }>).args).toMatchObject({
      context: 'auckland weather today and tomorrow forecast'
    })
    expect((part as Extract<ChatMessagePart, { type: 'tool-call' }>).result).toMatchObject({
      summary: 'Did 5 searches in 1.1s'
    })
  })

  it('does not append phantom same-name tool rows for id-less progress updates', () => {
    const startedA = upsertToolPart(
      [],
      {
        context: 'reykjavik weather today and tomorrow forecast',
        name: 'web_search'
      },
      'running'
    )

    const startedB = upsertToolPart(
      startedA,
      {
        context: 'kathmandu weather today and tomorrow forecast',
        name: 'web_search'
      },
      'running'
    )

    const progressed = upsertToolPart(
      startedB,
      {
        name: 'web_search'
      },
      'running'
    )

    const webParts = progressed.filter(
      (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
        part.type === 'tool-call' && part.toolName === 'web_search'
    )

    expect(webParts).toHaveLength(2)
  })

  it('matches id-less live starts with later identified completions', () => {
    const started = upsertToolPart(
      [],
      {
        context: 'asuncion paraguay weather today and tomorrow forecast',
        name: 'web_search'
      },
      'running'
    )

    const completed = upsertToolPart(
      started,
      {
        context: 'asuncion paraguay weather today and tomorrow forecast',
        duration_s: 1.1,
        name: 'web_search',
        summary: 'Did 5 searches in 1.1s',
        tool_id: 'search-asuncion'
      },
      'complete'
    )

    const webParts = completed.filter(
      (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
        part.type === 'tool-call' && part.toolName === 'web_search'
    )

    expect(webParts).toHaveLength(1)
    expect(webParts[0].toolCallId).toBe('search-asuncion')
    expect(webParts[0].result).toMatchObject({ summary: 'Did 5 searches in 1.1s' })
  })

  it('matches id-less live starts with later identified progress updates', () => {
    const started = upsertToolPart(
      [],
      {
        context: 'reykjavik tashkent uzbekistan weather today and tomorrow forecast',
        name: 'web_search'
      },
      'running'
    )

    const progressed = upsertToolPart(
      started,
      {
        context: 'reykjavik tashkent uzbekistan weather today and tomorrow forecast',
        name: 'web_search',
        tool_id: 'search-reykjavik'
      },
      'running'
    )

    const webParts = progressed.filter(
      (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
        part.type === 'tool-call' && part.toolName === 'web_search'
    )

    expect(webParts).toHaveLength(1)
    expect(webParts[0].toolCallId).toBe('search-reykjavik')
  })

  it('reconciles preview-first progress rows with later stable-id starts', () => {
    const progressA = upsertToolPart(
      [],
      {
        name: 'web_search',
        preview: 'tokyo weather'
      },
      'running'
    )

    const progressB = upsertToolPart(
      progressA,
      {
        name: 'web_search',
        preview: 'reykjavik weather'
      },
      'running'
    )

    const startedA = upsertToolPart(
      progressB,
      {
        args: { query: 'tokyo weather' },
        name: 'web_search',
        tool_id: 'search-tokyo'
      },
      'running'
    )

    const startedB = upsertToolPart(
      startedA,
      {
        args: { query: 'reykjavik weather' },
        name: 'web_search',
        tool_id: 'search-reykjavik'
      },
      'running'
    )

    const completedA = upsertToolPart(
      startedB,
      {
        name: 'web_search',
        summary: 'Did 5 searches',
        tool_id: 'search-tokyo'
      },
      'complete'
    )

    const completedB = upsertToolPart(
      completedA,
      {
        name: 'web_search',
        summary: 'Did 5 searches',
        tool_id: 'search-reykjavik'
      },
      'complete'
    )

    const webParts = completedB
      .filter(
        (part): part is Extract<ChatMessagePart, { type: 'tool-call' }> =>
          part.type === 'tool-call' && part.toolName === 'web_search'
      )
      .map(part => ({
        id: part.toolCallId,
        query: String((part.args as Record<string, unknown>)?.query || ''),
        summary:
          part.result && typeof part.result === 'object'
            ? String((part.result as Record<string, unknown>).summary || '')
            : ''
      }))

    expect(webParts).toEqual([
      { id: 'search-tokyo', query: 'tokyo weather', summary: 'Did 5 searches' },
      { id: 'search-reykjavik', query: 'reykjavik weather', summary: 'Did 5 searches' }
    ])
  })

  it('uses structured live tool args for titles before hydrate', () => {
    const started = upsertToolPart(
      [],
      {
        args: { search_term: 'reykjavik bishkek kyrgyzstan weather today and tomorrow forecast' },
        name: 'web_search',
        tool_id: 'search-bishkek'
      },
      'running'
    )

    const [part] = started

    expect(part?.type).toBe('tool-call')
    expect((part as Extract<ChatMessagePart, { type: 'tool-call' }>).args).toMatchObject({
      search_term: 'reykjavik bishkek kyrgyzstan weather today and tomorrow forecast'
    })
  })

  it('keeps structured live tool results before hydrate', () => {
    const completed = upsertToolPart(
      [],
      {
        args: { query: 'suva weather' },
        name: 'web_search',
        result: { data: { web: [{ title: 'Suva forecast', url: 'https://example.test', description: 'Sunny' }] } },
        summary: 'Did 1 search in 0.5s',
        tool_id: 'search-suva'
      },
      'complete'
    )

    const [part] = completed

    expect(part?.type).toBe('tool-call')
    expect((part as Extract<ChatMessagePart, { type: 'tool-call' }>).result).toMatchObject({
      data: { web: [{ title: 'Suva forecast' }] },
      summary: 'Did 1 search in 0.5s'
    })
  })
})

// WP-G6 (裁定 21.2): sentence ② real payload shape — SessionDB stores
// tool_name=plobi_secretary_ask + intent=write_briefing; the transcript must
// rebuild a tool-call part whose card carries 派给谁 / intent / route / briefing.
const SECRETARY_RESULT = {
  ok: true,
  intent: 'write_briefing',
  user_text: '根据明天的日程写一段早报',
  agent: { id: 'agenda-secretary', role: 'secretary', profile: 'agenda', spawned: false },
  agenda: { events: [{ id: 'e1', title: 'Standup', start_at: '2026-09-08T09:00:00+08:00' }] },
  sessionId: 'sess-n3',
  briefing: 'Tomorrow: 09:00 standup, 14:00 review.',
  route: 'workbuddy',
  model: 'workbuddy/default'
}

function secretaryToolRow(overrides: Partial<SessionMessage> = {}): SessionMessage {
  return {
    role: 'tool',
    tool_name: 'plobi_secretary_ask',
    tool_call_id: 'call_abc',
    content: JSON.stringify(SECRETARY_RESULT),
    timestamp: 3,
    ...overrides
  }
}

function secretaryToolPart(messages: ChatMessage[]): Extract<ChatMessagePart, { type: 'tool-call' }> {
  const parts = messages.flatMap(m => m.parts).filter((part): part is Extract<ChatMessagePart, { type: 'tool-call' }> => part.type === 'tool-call')

  expect(parts.length).toBe(1)
  expect(parts[0].toolName).toBe('plobi_secretary_ask')

  return parts[0]
}

describe('toChatMessages plobi_secretary_ask (WP-G6 transcript → tool-call part)', () => {
  it('rebuilds the C3 card fields from a full assistant tool_calls + tool row transcript', () => {
    const messages = toChatMessages([
      { role: 'user', content: '根据明天的日程写一段早报', timestamp: 1 },
      {
        role: 'assistant',
        content: '',
        timestamp: 2,
        tool_calls: [
          {
            id: 'call_abc',
            type: 'function',
            function: {
              name: 'plobi_secretary_ask',
              arguments: JSON.stringify({ intent: 'write_briefing', user_text: '根据明天的日程写一段早报' })
            }
          }
        ]
      },
      secretaryToolRow(),
      { role: 'assistant', content: '这是早报。', timestamp: 4 }
    ])

    const part = secretaryToolPart(messages)
    const view = buildToolView(part, '')

    expect(view.title).toBe('Asked 日程秘书')
    expect(view.subtitle).toContain('日程秘书')
    expect(view.subtitle).toContain('write briefing')
    expect(view.subtitle).toContain('route=workbuddy')
    expect(view.detail).toContain('09:00 standup')
  })

  it('still rebuilds the card when the assistant tool_calls row is gone (orphan tool row)', () => {
    const messages = toChatMessages([
      { role: 'user', content: '根据明天的日程写一段早报', timestamp: 1 },
      secretaryToolRow(),
      { role: 'assistant', content: '这是早报。', timestamp: 4 }
    ])

    const part = secretaryToolPart(messages)
    const view = buildToolView(part, '')

    expect(view.title).toBe('Asked 日程秘书')
    expect(view.subtitle).toContain('write briefing')
    expect(view.subtitle).toContain('route=workbuddy')
    expect(view.detail).toContain('09:00 standup')
  })

  it('lands the result on the existing call when the stored call lost its id (no duplicate row)', () => {
    const messages = toChatMessages([
      { role: 'user', content: '根据明天的日程写一段早报', timestamp: 1 },
      {
        role: 'assistant',
        content: '',
        timestamp: 2,
        tool_calls: [
          {
            function: {
              name: 'plobi_secretary_ask',
              arguments: JSON.stringify({ intent: 'write_briefing', user_text: '根据明天的日程写一段早报' })
            }
          }
        ]
      },
      secretaryToolRow(),
      { role: 'assistant', content: '这是早报。', timestamp: 4 }
    ])

    const part = secretaryToolPart(messages)
    const view = buildToolView(part, '')

    // The result lands on the ORIGINAL call part (which keeps its own synthetic
    // id) — one part, real args, real payload. No duplicate degraded row.
    expect(part.args).toMatchObject({ intent: 'write_briefing' })
    expect(part.result).toMatchObject({ intent: 'write_briefing', route: 'workbuddy' })
    expect(view.subtitle).toContain('route=workbuddy')
  })

  it('keeps a plain-text tool row readable (non-JSON content still renders as context)', () => {
    const messages = toChatMessages([
      { role: 'user', content: 'hi', timestamp: 1 },
      secretaryToolRow({ tool_name: 'terminal', content: 'Found 3 matches' }),
      { role: 'assistant', content: 'done', timestamp: 4 }
    ])

    const parts = messages.flatMap(m => m.parts).filter((part): part is Extract<ChatMessagePart, { type: 'tool-call' }> => part.type === 'tool-call')

    expect(parts).toHaveLength(1)
    expect(parts[0].toolName).toBe('terminal')
    expect(parts[0].result).toMatchObject({ context: 'Found 3 matches' })
  })
})
