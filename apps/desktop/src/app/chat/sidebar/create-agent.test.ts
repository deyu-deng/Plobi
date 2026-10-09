import { describe, expect, it } from 'vitest'

import {
  AGENT_CATEGORY_VALUES,
  DEFAULT_AGENT_CATEGORY,
  agentIdFromName,
  buildCreateAgentBody,
  categoryNeedsProjectPath,
  createAgentErrorMessage,
  isCreateAgentCategoryBlocked,
  isCreateAgentSubmitDisabled,
  type AgentCategory
} from './create-agent'

describe('agentIdFromName', () => {
  it('lowerCases and hyphenates', () => {
    expect(agentIdFromName('My Project')).toBe('my-project')
    expect(agentIdFromName('plobi Code')).toBe('plobi-code')
  })

  it('falls back to an l2- timestamped slug for empty/CJK-only input', () => {
    expect(agentIdFromName('').startsWith('l2-')).toBe(true)
    expect(agentIdFromName('   ').startsWith('l2-')).toBe(true)
  })
})

describe('buildCreateAgentBody', () => {
  it('forwards category into the POST body with the l2_project role', () => {
    const body = buildCreateAgentBody('My Project', 'research')

    expect(body).toEqual({
      id: 'my-project',
      name: 'My Project',
      role: 'l2_project',
      category: 'research'
    })
  })

  it('keeps the default category when none is overridden', () => {
    const body = buildCreateAgentBody('Butler Bot', DEFAULT_AGENT_CATEGORY)

    expect(body.category).toBe(DEFAULT_AGENT_CATEGORY)
    expect(body.role).toBe('l2_project')
  })

  it('sends the typed name as-is while the id stays the lowercased slug', () => {
    const body = buildCreateAgentBody('Aura', 'projects', 'D:/x')

    expect(body.name).toBe('Aura')
    expect(body.id).toBe('aura')
  })

  it('trims the name it sends', () => {
    expect(buildCreateAgentBody('  Aura  ', 'butler').name).toBe('Aura')
  })
})

describe('isCreateAgentCategoryBlocked', () => {
  it('blocks only the events category', () => {
    expect(isCreateAgentCategoryBlocked('events')).toBe(true)

    for (const cat of AGENT_CATEGORY_VALUES.filter(c => c !== 'events')) {
      expect(isCreateAgentCategoryBlocked(cat as AgentCategory)).toBe(false)
    }
  })
})

describe('categoryNeedsProjectPath (WP-STUDIO, 裁定 36.1)', () => {
  it('asks for a folder for projects and research only', () => {
    expect(categoryNeedsProjectPath('projects')).toBe(true)
    expect(categoryNeedsProjectPath('research')).toBe(true)
    expect(categoryNeedsProjectPath('butler')).toBe(false)
    expect(categoryNeedsProjectPath('events')).toBe(false)
  })
})

describe('buildCreateAgentBody projectPath (WP-STUDIO, 裁定 36.1)', () => {
  it('forwards the picked folder for a projects agent', () => {
    const body = buildCreateAgentBody('Code', 'projects', 'D:/Projects/Plobi/Code')

    expect(body).toEqual({
      id: 'code',
      name: 'Code',
      role: 'l2_project',
      category: 'projects',
      projectPath: 'D:/Projects/Plobi/Code'
    })
  })

  it('never sends projectPath for a butler agent', () => {
    const body = buildCreateAgentBody('Butler', 'butler')

    expect('projectPath' in body).toBe(false)
  })

  it('drops an empty / whitespace path instead of sending it', () => {
    expect('projectPath' in buildCreateAgentBody('Code', 'projects', '')).toBe(false)
    expect('projectPath' in buildCreateAgentBody('Code', 'projects', '   ')).toBe(false)
    expect('projectPath' in buildCreateAgentBody('Code', 'projects', null)).toBe(false)
  })

  it('trims the path it does send', () => {
    expect(buildCreateAgentBody('Code', 'projects', '  D:/x  ').projectPath).toBe('D:/x')
  })
})

describe('isCreateAgentSubmitDisabled (WP-STUDIO, 裁定 36.1)', () => {
  it('keeps submit disabled until a workspace agent has a folder', () => {
    expect(isCreateAgentSubmitDisabled('Code', 'projects', '')).toBe(true)
    expect(isCreateAgentSubmitDisabled('Code', 'projects', 'D:/x')).toBe(false)
    expect(isCreateAgentSubmitDisabled('Study', 'research', '')).toBe(true)
    expect(isCreateAgentSubmitDisabled('Study', 'research', 'D:/y')).toBe(false)
  })

  it('does not require a folder for butler', () => {
    expect(isCreateAgentSubmitDisabled('Butler', 'butler')).toBe(false)
  })

  it('always keeps submit disabled for events and for an empty name', () => {
    expect(isCreateAgentSubmitDisabled('Whatever', 'events', 'D:/x')).toBe(true)
    expect(isCreateAgentSubmitDisabled('   ', 'butler')).toBe(true)
  })
})

// R-013 扩写（2026-10-06）：重名在这道门就被后端拒，弹窗必须把后端那句话显示出来。
// 判据在 `plobi.agents.registry.registration_blocker`，前端不许另算一套——这里只钉
// 「摊平显示」这一件事：两种到达形态都得落到后端写的原话上，且永不返回空串。
describe('createAgentErrorMessage', () => {
  const refusal = '「nymo」和已有的「Nymo」只差大小写——要么直接用「Nymo」那条'

  it('unwraps the envelope the desktop main bridge prefixes with the status code', () => {
    // electron/main.ts 的 fetchJson 对非 2xx 抛的是 `400: {"ok":false,"error":"…"}`。
    const raw = `409: ${JSON.stringify({ error: refusal, ok: false })}`

    expect(createAgentErrorMessage(new Error(raw))).toBe(refusal)
  })

  it('keeps the sentence when the bridge already unwrapped it', () => {
    expect(createAgentErrorMessage(new Error(refusal))).toBe(refusal)
  })

  it('leaves a non-JSON message alone instead of inventing one', () => {
    expect(createAgentErrorMessage('backend offline')).toBe('backend offline')
  })

  it('never returns an empty string — empty means the dialog shows nothing', () => {
    expect(createAgentErrorMessage(new Error('   '))).not.toBe('')
    expect(createAgentErrorMessage(null)).not.toBe('')
  })
})
