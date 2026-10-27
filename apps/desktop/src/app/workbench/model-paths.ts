const MODEL_EXTENSIONS = ['glb', 'gltf', 'obj', 'stl']

export function isModelPath(path: string) {
  const ext = path.split(/[?#]/u)[0]?.split('.').pop()?.toLowerCase() ?? ''

  return MODEL_EXTENSIONS.includes(ext)
}
