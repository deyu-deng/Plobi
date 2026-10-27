import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'
import { openExternalLink } from '@/lib/external-link'
import { mediaExternalUrl, mediaName } from '@/lib/media'

import { readFileBytes } from './file-bytes'
import { isModelPath } from './model-paths'
import type { RichPreviewLabels } from './media-preview'
import { useFileRevision } from './use-file-revision'

function loadScene(path: string, bytes: Uint8Array): Promise<THREE.Object3D> {
  const ext = path.split(/[?#]/u)[0]?.split('.').pop()?.toLowerCase() ?? ''

  return new Promise((resolve, reject) => {
    try {
      if (ext === 'glb' || ext === 'gltf') {
        const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
        new GLTFLoader().parse(buffer, '', gltf => resolve(gltf.scene), reject)

        return
      }

      if (ext === 'stl') {
        const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
        const geometry = new STLLoader().parse(buffer)
        const material = new THREE.MeshStandardMaterial({ color: 0x9ca3af, metalness: 0.1, roughness: 0.8 })
        resolve(new THREE.Mesh(geometry, material))

        return
      }

      const text = new TextDecoder().decode(bytes)
      resolve(new OBJLoader().parse(text))
    } catch (error) {
      reject(error)
    }
  })
}

export function ThreePreview({ labels, path }: { labels: RichPreviewLabels; path: string }) {
  const { change } = useFileRevision(path)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    let active = true
    let frame = 0
    let renderer: THREE.WebGLRenderer | null = null
    let controls: OrbitControls | null = null

    setLoading(true)
    setFailed(false)

    void readFileBytes(path)
      .then(bytes => loadScene(path, bytes))
      .then(scene => {
        if (!active || !hostRef.current) {
          return
        }

        const container = hostRef.current
        const width = Math.max(container.clientWidth, 320)
        const height = Math.max(container.clientHeight, 240)

        const scene3d = new THREE.Scene()
        scene3d.background = new THREE.Color(0x111827)
        scene3d.add(scene)
        scene3d.add(new THREE.AmbientLight(0xffffff, 1.1))
        const key = new THREE.DirectionalLight(0xffffff, 1.6)
        key.position.set(3, 5, 4)
        scene3d.add(key)
        scene3d.add(new THREE.GridHelper(20, 20, 0x4b5563, 0x1f2937))

        const box = new THREE.Box3().setFromObject(scene)
        const sphere = box.getBoundingSphere(new THREE.Sphere())
        const radius = Math.max(sphere.radius, 0.5)

        const camera = new THREE.PerspectiveCamera(50, width / height, 0.01, radius * 100)
        camera.position.set(sphere.center.x, sphere.center.y, sphere.center.z + radius * 2.6)

        renderer = new THREE.WebGLRenderer({ antialias: true })
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
        renderer.setSize(width, height)
        container.replaceChildren(renderer.domElement)
        renderer.domElement.className = 'block h-full w-full'

        controls = new OrbitControls(camera, renderer.domElement)
        controls.enableDamping = true
        controls.target.copy(sphere.center)
        controls.update()

        const resize = () => {
          if (!renderer || !hostRef.current) {
            return
          }

          const nextWidth = Math.max(hostRef.current.clientWidth, 320)
          const nextHeight = Math.max(hostRef.current.clientHeight, 240)

          renderer.setSize(nextWidth, nextHeight)
          camera.aspect = nextWidth / nextHeight
          camera.updateProjectionMatrix()
        }

        const observer = new ResizeObserver(resize)
        observer.observe(container)

        const loop = () => {
          if (!active) {
            return
          }

          controls?.update()
          renderer?.render(scene3d, camera)
          frame = requestAnimationFrame(loop)
        }

        loop()
        setLoading(false)

        return () => observer.disconnect()
      })
      .catch(() => {
        if (active) {
          setFailed(true)
          setLoading(false)
        }
      })

    return () => {
      active = false
      cancelAnimationFrame(frame)
      controls?.dispose()
      renderer?.dispose()
      host.replaceChildren()
    }
  }, [change, path])

  const open = () => openExternalLink(mediaExternalUrl(path))

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col bg-(--ui-bg-primary)">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2">
        <span className="min-w-0 flex-1 truncate text-[0.66rem] text-(--ui-text-secondary)">
          {mediaName(path)}
        </span>
        <button
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={open}
          title={labels.openExternal}
          type="button"
        >
          <Codicon name="link-external" size="0.72rem" />
          <span>{labels.openExternal}</span>
        </button>
        <span className="shrink-0 text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>

      <div className="relative min-h-0 flex-1">
        <div className="h-full w-full" ref={hostRef} />
        {loading ? (
          <div className="absolute inset-0 grid place-items-center bg-(--ui-bg-primary)">
            <PageLoader label={labels.loading} />
          </div>
        ) : null}
        {failed ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-(--ui-bg-primary) text-center">
            <Codicon name="warning" size="1.25rem" />
            <p className="text-[0.8rem] font-medium text-foreground">{labels.unavailable}</p>
            <button
              className="rounded border border-(--ui-stroke-secondary) px-2 py-1 text-[0.66rem] hover:bg-(--ui-row-hover-background)"
              onClick={open}
              type="button"
            >
              {labels.openExternal}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
