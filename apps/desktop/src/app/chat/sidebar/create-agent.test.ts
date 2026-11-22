import { describe, expect, it } from 'vitest'

import {
  DEFAULT_AGENT_CATEGORY,
  agentIdFromEnglish,
  buildCreateAgentBody,
  categoryNeedsProjectPath,
  createAgentErrorMessage,
  isCreateAgentSubmitDisabled,
  isValidEnglishName
} from './create-agent'

// 2026-11-16（i18n 定名刀）：门牌（agent_id）不再是「名字的 slug + 短哈希」，而是
// **英文名规范化**的结果——中文名只当显示名。这组用例钉住三件事：规则本身、
// 「旧 l2- / 哈希」不许复活、以及空/中文输入不给一个看着像 id 的残骸。
describe('agentIdFromEnglish', () => {
  it('lowerCases and hyphenates', () => {
    expect(agentIdFromEnglish('My Project')).toBe('my-project')
    expect(agentIdFromEnglish('plobi Code')).toBe('plobi-code')
    expect(agentIdFromEnglish('Agenda Secretary')).toBe('agenda-secretary')
  })

  it('folds punctuation and repeated separators into a single hyphen', () => {
    expect(agentIdFromEnglish('  College  Physics A  ')).toBe('college-physics-a')
    expect(agentIdFromEnglish('Plobi!!Code')).toBe('plobi-code')
    expect(agentIdFromEnglish('--Aura--')).toBe('aura')
  })

  it('returns an empty key when nothing usable is left', () => {
    expect(agentIdFromEnglish('')).toBe('')
    expect(agentIdFromEnglish('   ')).toBe('')
    expect(agentIdFromEnglish('!!')).toBe('')
  })

  // 旧规则留下的两个坑：中文名被 slug 洗成 'ii'、以及 md5 短哈希当门牌（用户
  // 11-12 22:32「怎么机器记录的是 ii」）。中文进英文名框必须一个字符都不认。
  it('drops CJK entirely instead of keeping a stray suffix', () => {
    expect(agentIdFromEnglish('大学物理（甲）II')).toBe('ii')
    expect(agentIdFromEnglish('普洛比')).toBe('')
  })

  it('never grows an l2- prefix or a hash tail', () => {
    expect(agentIdFromEnglish('Office')).toBe('office')
    expect(agentIdFromEnglish('Office')).not.toMatch(/^l2-/)
    expect(agentIdFromEnglish('Office')).not.toMatch(/[0-9a-f]{6}$/)
  })

  it('is stable for the same input (a clash is the backend’s call)', () => {
    expect(agentIdFromEnglish('Research Assistant')).toBe(agentIdFromEnglish('Research Assistant'))
  })
})

describe('isValidEnglishName', () => {
  it('accepts plain ASCII words', () => {
    expect(isValidEnglishName('Aura')).toBe(true)
    expect(isValidEnglishName('Agenda Secretary')).toBe(true)
    expect(isValidEnglishName('  College Physics A ')).toBe(true)
  })

  it('rejects an empty box', () => {
    expect(isValidEnglishName('')).toBe(false)
    expect(isValidEnglishName('   ')).toBe(false)
  })

  // 中文名进英文名框 ⇒ 门牌要么空、要么退化成哈希，两种都比「重填一个词」糟。
  it('rejects CJK and punctuation', () => {
    expect(isValidEnglishName('普洛比')).toBe(false)
    expect(isValidEnglishName('大学物理（甲）II')).toBe(false)
    expect(isValidEnglishName('!!!')).toBe(false)
  })

  // 人写英文名会带连字符和撇号（「x-1」「Aura's Notes」），这些在门牌里本来就会被
  // 折成连字符，拦下来只会让人反复试错。
  it('accepts inner hyphens, underscores and apostrophes', () => {
    expect(isValidEnglishName('Aura-New')).toBe(true)
    expect(isValidEnglishName("Aura's Notes")).toBe(true)
    expect(isValidEnglishName('note_2')).toBe(true)
    expect(isValidEnglishName('-Aura')).toBe(false)
  })
})

describe('buildCreateAgentBody', () => {
  it('forwards category into the POST body with the principal role', () => {
    const body = buildCreateAgentBody('我的项目', 'My Project', 'research')

    expect(body).toEqual({
      id: 'my-project',
      name: '我的项目',
      displayNameEn: 'My Project',
      role: 'principal',
      category: 'research'
    })
  })

  it('keeps the default category when none is overridden', () => {
    const body = buildCreateAgentBody('Butler Bot', 'Butler Bot', DEFAULT_AGENT_CATEGORY)

    expect(body.category).toBe(DEFAULT_AGENT_CATEGORY)
    expect(body.role).toBe('principal')
  })

  // 中文名只当显示名：它原样进 name，一个字都不参与 id。
  it('sends the Chinese name as-is and takes the id from the English one', () => {
    const body = buildCreateAgentBody('光环', 'Aura', 'projects', 'D:/x')

    expect(body.name).toBe('光环')
    expect(body.displayNameEn).toBe('Aura')
    expect(body.id).toBe('aura')
  })

  it('trims both names before sending', () => {
    const body = buildCreateAgentBody('  Aura  ', '  Aura  ', 'butler')

    expect(body.name).toBe('Aura')
    expect(body.displayNameEn).toBe('Aura')
  })
})

describe('events 类不再被封禁（用户 2026-11-22）', () => {
  it('事件类只要名字与英文名齐了就能提交，不需要先有日程', () => {
    expect(isCreateAgentSubmitDisabled('校运会', 'Sports-Meet', 'events')).toBe(false)
    expect(isCreateAgentSubmitDisabled('校运会', 'Sports-Meet', 'events', [])).toBe(false)
  })

  it('见不得人的名字照样拦住（拦的是名字，不是类别）', () => {
    expect(isCreateAgentSubmitDisabled('', 'Sports-Meet', 'events')).toBe(true)
    expect(isCreateAgentSubmitDisabled('校运会', '校运会', 'events')).toBe(true)
  })
})

describe('categoryNeedsProjectPath (WP-STUDIO, 裁定 36.1)', () => {
  it('asks for a folder for projects and research only', () => {
    expect(categoryNeedsProjectPath('projects')).toBe(true)
    expect(categoryNeedsProjectPath('research')).toBe(true)
    // WP-AGENT-CREATE-UPGRADE（用户 10-16）：课程 Agent 也要绑 Cloud/Courses 文件夹。
    expect(categoryNeedsProjectPath('courses')).toBe(true)
    expect(categoryNeedsProjectPath('butler')).toBe(false)
    expect(categoryNeedsProjectPath('events')).toBe(false)
  })
})

describe('buildCreateAgentBody projectPath (WP-STUDIO, 裁定 36.1)', () => {
  it('forwards the picked folder for a projects agent', () => {
    const body = buildCreateAgentBody('Code', 'Code', 'projects', ['D:/Projects/Plobi/Code'])

    expect(body).toEqual({
      id: 'code',
      name: 'Code',
      displayNameEn: 'Code',
      role: 'principal',
      category: 'projects',
      projectPath: 'D:/Projects/Plobi/Code'
    })
  })

  it('never sends projectPath for a butler agent', () => {
    const body = buildCreateAgentBody('Butler', 'Butler', 'butler')

    expect('projectPath' in body).toBe(false)
  })

  it('drops an empty / whitespace path instead of sending it', () => {
    expect('projectPath' in buildCreateAgentBody('Code', 'Code', 'projects', [''])).toBe(false)
    expect('projectPath' in buildCreateAgentBody('Code', 'Code', 'projects', ['   '])).toBe(false)
    expect('projectPath' in buildCreateAgentBody('Code', 'Code', 'projects', [])).toBe(false)
    expect('projectPath' in buildCreateAgentBody('Code', 'Code', 'projects', null)).toBe(false)
  })

  it('trims the path it does send', () => {
    expect(buildCreateAgentBody('Code', 'Code', 'projects', ['  D:/x  ']).projectPath).toBe('D:/x')
  })
})

describe('buildCreateAgentBody projectPaths (WP-AGENT-CREATE-UPGRADE, 用户 10-16)', () => {
  it('sends the full list for a multi-folder agent, primary first', () => {
    const body = buildCreateAgentBody('Code', 'Code', 'projects', ['D:/one', '  ', 'D:/two', 'D:/one'])

    expect(body.projectPaths).toEqual(['D:/one', 'D:/two'])
    expect(body.projectPath).toBe('D:/one') // 主文件夹 = 第一个
  })

  it('keeps the old single-folder shape (no projectPaths key) for one folder', () => {
    const body = buildCreateAgentBody('Code', 'Code', 'projects', ['D:/only'])

    expect('projectPaths' in body).toBe(false)
    expect(body.projectPath).toBe('D:/only')
  })

  it('forwards icon and color when picked, drops them when blank', () => {
    const styled = buildCreateAgentBody('Code', 'Code', 'projects', ['D:/x'], {
      color: 'hsl(210 68% 58%)',
      icon: 'robot'
    })

    expect(styled.icon).toBe('robot')
    expect(styled.color).toBe('hsl(210 68% 58%)')
    expect('icon' in buildCreateAgentBody('Code', 'Code', 'projects', ['D:/x'], { icon: '  ' })).toBe(false)
    expect('icon' in buildCreateAgentBody('Code', 'Code', 'projects', ['D:/x'])).toBe(false)
  })

  it('lets a courses agent bind its course folder', () => {
    const body = buildCreateAgentBody('大学物理实验', '大学物理实验', 'courses', [
      'D:/Cloud/Courses/2026-2027秋冬-大学物理实验'
    ])

    expect(body.category).toBe('courses')
    expect(body.projectPath).toBe('D:/Cloud/Courses/2026-2027秋冬-大学物理实验')
  })
})

describe('isCreateAgentSubmitDisabled (WP-STUDIO, 裁定 36.1)', () => {
  it('keeps submit disabled until a workspace agent has a folder', () => {
    expect(isCreateAgentSubmitDisabled('Code', 'Code', 'projects', [])).toBe(true)
    expect(isCreateAgentSubmitDisabled('Code', 'Code', 'projects', ['D:/x'])).toBe(false)
    expect(isCreateAgentSubmitDisabled('Study', 'Study', 'research', [])).toBe(true)
    expect(isCreateAgentSubmitDisabled('Study', 'Study', 'research', ['D:/y'])).toBe(false)
  })

  it('does not require a folder for butler', () => {
    expect(isCreateAgentSubmitDisabled('Butler', 'Butler', 'butler')).toBe(false)
  })

  it('keeps submit disabled for an empty name (events is no longer special)', () => {
    expect(isCreateAgentSubmitDisabled('Whatever', 'Whatever', 'events', ['D:/x'])).toBe(false)
    expect(isCreateAgentSubmitDisabled('   ', '   ', 'butler')).toBe(true)
  })

  // 门牌从英文名来：这个框空着或塞了中文，agent_id 就是空串/哈希残渣，
  // 所以按钮在发请求之前就得是灰的（后端那句「id is required」不该让人看见）。
  it('keeps submit disabled while the English name is empty or not ASCII', () => {
    expect(isCreateAgentSubmitDisabled('普洛比', '', 'butler')).toBe(true)
    expect(isCreateAgentSubmitDisabled('普洛比', '   ', 'butler')).toBe(true)
    expect(isCreateAgentSubmitDisabled('普洛比', '普洛比', 'butler')).toBe(true)
    expect(isCreateAgentSubmitDisabled('普洛比', 'Plobi', 'butler')).toBe(false)
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
