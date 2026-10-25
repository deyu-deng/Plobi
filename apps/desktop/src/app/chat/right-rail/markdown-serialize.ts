// ProseMirror JSON -> markdown for the diary editor's fixed schema.
//
// The diary file stays plain markdown (the 18 entries already on disk are
// markdown, and git diffs should stay readable), while the editor is a
// WYSIWYG surface. Only the node/mark types enabled in DiaryEditor are handled
// here; anything unknown falls back to rendering its content so a schema
// addition degrades to text instead of silently dropping it.
//
// Blocks are joined with a blank line — consecutive lines in markdown collapse
// into one paragraph, which is not what two separate paragraphs in the editor
// mean.
export interface PmMark {
  type: string
  attrs?: Record<string, unknown>
}

export interface PmNode {
  type?: string
  text?: string
  content?: PmNode[]
  marks?: PmMark[]
  attrs?: Record<string, unknown>
}

function inline(nodes: PmNode[] = []): string {
  return nodes
    .map(node => {
      if (node.type === 'hardBreak') return '  \n'
      let out = node.text ?? inline(node.content)
      for (const mark of node.marks ?? []) {
        if (mark.type === 'bold') out = `**${out}**`
        else if (mark.type === 'italic') out = `*${out}*`
        else if (mark.type === 'strike') out = `~~${out}~~`
        else if (mark.type === 'code') out = `\`${out}\``
        else if (mark.type === 'link') out = `[${out}](${String(mark.attrs?.href ?? '')})`
      }
      return out
    })
    .join('')
}

function pad(text: string, spaces: number): string {
  const prefix = ' '.repeat(spaces)
  return text
    .split('\n')
    .map(line => (line ? prefix + line : line))
    .join('\n')
}

function listItemText(item: PmNode, prefix: string, indent: number): string {
  const children = item.content ?? []
  const [first, ...rest] = children
  const head = first ? blockText(first, 0) : ''
  const lines = [' '.repeat(indent) + prefix + head]
  for (const child of rest) {
    lines.push(blockText(child, indent + 2))
  }
  return lines.join('\n')
}

function blockText(node: PmNode, indent: number): string {
  switch (node.type) {
    case 'paragraph':
      return pad(inline(node.content), indent)
    case 'heading': {
      const level = Math.min(6, Math.max(1, Number(node.attrs?.level ?? 1)))
      return pad(`${'#'.repeat(level)} ${inline(node.content)}`, indent)
    }
    case 'bulletList':
      return (node.content ?? [])
        .map(item => listItemText(item, '- ', indent))
        .join('\n')
    case 'orderedList': {
      const start = Number(node.attrs?.start ?? 1)
      return (node.content ?? [])
        .map((item, index) => listItemText(item, `${start + index}. `, indent))
        .join('\n')
    }
    case 'blockquote': {
      const inner = (node.content ?? [])
        .map(child => blockText(child, 0))
        .filter(Boolean)
        .join('\n\n')
      return pad(
        inner
          .split('\n')
          .map(line => (line ? `> ${line}` : '>'))
          .join('\n'),
        indent
      )
    }
    case 'codeBlock': {
      const language = String(node.attrs?.language ?? '')
      const body = (node.content ?? []).map(child => child.text ?? '').join('')
      return pad([`\`\`\`${language}`, ...body.split('\n'), '```'].join('\n'), indent)
    }
    case 'horizontalRule':
      return pad('---', indent)
    default:
      return (node.content ?? [])
        .map(child => blockText(child, indent))
        .filter(Boolean)
        .join('\n\n')
  }
}

export function pmToMarkdown(doc: PmNode | null | undefined): string {
  const blocks = (doc?.content ?? [])
    .map(node => blockText(node, 0))
    .filter(block => block.trim())
  return blocks.length ? `${blocks.join('\n\n')}\n` : ''
}
