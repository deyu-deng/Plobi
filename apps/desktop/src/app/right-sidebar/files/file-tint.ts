const TINT_BY_EXTENSION: Record<string, string> = {
  bash: 'text-(--ui-green)',
  cjs: 'text-(--ui-yellow)',
  css: 'text-(--ui-purple)',
  go: 'text-(--ui-green)',
  htm: 'text-(--ui-orange)',
  html: 'text-(--ui-orange)',
  java: 'text-(--ui-green)',
  js: 'text-(--ui-yellow)',
  json: 'text-(--ui-orange)',
  json5: 'text-(--ui-orange)',
  jsonc: 'text-(--ui-orange)',
  jsx: 'text-(--ui-yellow)',
  md: 'text-(--ui-cyan)',
  mdx: 'text-(--ui-cyan)',
  mjs: 'text-(--ui-yellow)',
  markdown: 'text-(--ui-cyan)',
  py: 'text-(--ui-green)',
  rs: 'text-(--ui-green)',
  scss: 'text-(--ui-purple)',
  sh: 'text-(--ui-green)',
  svg: 'text-(--ui-orange)',
  ts: 'text-(--ui-blue)',
  tsx: 'text-(--ui-blue)',
  vue: 'text-(--ui-blue)',
  xml: 'text-(--ui-orange)',
  yaml: 'text-(--ui-orange)',
  yml: 'text-(--ui-orange)',
  zsh: 'text-(--ui-green)'
}

export function fileIconTint(path: string) {
  const name = path.replace(/\\/g, '/').split('/').pop()?.toLowerCase() ?? ''
  const dot = name.lastIndexOf('.')

  return TINT_BY_EXTENSION[dot > 0 ? name.slice(dot + 1) : name] ?? 'text-(--ui-text-tertiary)'
}
