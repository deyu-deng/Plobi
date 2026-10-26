// Always-hidden noise for every filesystem listing/index walk: VCS internals,
// dependency/build/cache dirs. Keep renderer `ALWAYS_EXCLUDED` in sync when
// adding a name that should also disappear from the IDE-style tree.
export const FS_HIDDEN_NAMES = new Set([
  '.git',
  '.hg',
  '.svn',
  '.cache',
  '.next',
  '.turbo',
  '.venv',
  '__pycache__',
  'build',
  'dist',
  'node_modules',
  'target',
  'venv'
])
