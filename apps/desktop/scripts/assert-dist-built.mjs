"use strict"

// Build-time guard: refuse to hand a half-built renderer to electron-builder.
//
// `npm run pack` / `npm run dist*` are `npm run build && npm run builder`.
// If the `build` step (tsc -b && vite build) fails but packaging proceeds
// anyway — a stale checkout that fails typecheck, an interrupted vite build,
// or npm not short-circuiting `&&` in some shells — electron-builder happily
// packages an app with an empty or missing `dist/`. The result launches but
// blank-pages with `ERR_FILE_NOT_FOUND` for dist/index.html, with no clue why.
//
// This runs at the tail of `build`, after vite build, so any packaging path
// inherits it. It fails loud and early instead of shipping a broken bundle.
// See issues #39484 (renderer blank page) and #41327 / #39472 (dashboard 404).

import { existsSync, statSync, readdirSync, openSync, readSync, closeSync, readFileSync } from "fs"
import { join, resolve } from "path"
import { isMain } from "./utils.mjs"
import { looksRunnable } from "./build-chatlog.mjs"

/**
 * Binaries that MUST be inside `build/backend` for the packaged app to work.
 *
 * They are build artefacts of projects vendored as source with `bin/`
 * gitignored, so a fresh checkout (and every CI runner) has none of them:
 * nothing breaks the build, the feature just silently cannot run on the user's
 * machine. The renderer check below has the same job for `dist/`.
 */
export function requiredBackendBinaries(platform = process.platform) {
  const chatlog = platform === "win32" ? "chatlog.exe" : "chatlog"
  return [
    {
      rel: join("plobi", "collectors", "chatlog", "vendor", "chatlog", "bin", chatlog),
      label: "WeChat message collector engine (vendored chatlog)",
      build: "npm run build:chatlog",
      why: "without it the WeChat collector can never start, and the packaged app shows it as unavailable",
    },
  ]
}

function readMagic(file) {
  try {
    const fd = openSync(file, "r")
    const head = Buffer.alloc(4)
    readSync(fd, head, 0, 4, 0)
    closeSync(fd)
    return head.toString("latin1")
  } catch {
    return ""
  }
}

// Pure check of the backend bundle — same contract as checkDistBuilt: returns
// { ok: true } or { ok: false, error, problems: [...] }.
export function checkBackendBinaries(backendDir, platform = process.platform) {
  const problems = []

  for (const item of requiredBackendBinaries(platform)) {
    const full = join(backendDir, item.rel)
    if (!existsSync(full)) {
      problems.push({ ...item, full, reason: "missing" })
      continue
    }
    const size = statSync(full).size
    // `go build -o` writes a Go archive and exits 0 when the target package is
    // not `main`; the magic bytes are what separates that from a real binary.
    //
    // The judgement lives in build-chatlog.mjs (looksRunnable) — one source of
    // truth, and it knows all three executable formats (PE/ELF/Mach-O).  The old
    // platform-branched check here knew only MZ and ELF, so a perfectly good
    // **Mach-O** (the macOS build) was rejected as "not a runnable binary" and
    // the mac packaging died over a binary that was right there.
    const magic = readMagic(full)
    if (!looksRunnable(magic) || size <= 1048576) {
      problems.push({
        ...item,
        full,
        reason: `not a runnable binary (magic ${JSON.stringify(magic)}, ${(size / 1048576).toFixed(1)} MB)`,
      })
    }
  }

  if (problems.length) {
    return {
      ok: false,
      problems,
      error: `backend binaries missing from ${backendDir}: ${problems.map(p => `${p.rel} (${p.reason})`).join("; ")}`,
    }
  }
  return { ok: true, problems: [] }
}

// Pure check — returns { ok: true } or { ok: false, error: "..." }.
// Kept side-effect-free so it can be unit tested without spawning a process.
export function checkDistBuilt(distDir) {
  if (!existsSync(distDir) || !statSync(distDir).isDirectory()) {
    return { ok: false, error: `no dist directory at ${distDir}` }
  }

  const indexHtml = join(distDir, "index.html")
  if (!existsSync(indexHtml) || !statSync(indexHtml).isFile()) {
    return { ok: false, error: `dist/index.html is missing at ${indexHtml}` }
  }
  if (statSync(indexHtml).size === 0) {
    return { ok: false, error: `dist/index.html is empty at ${indexHtml}` }
  }

  // index.html alone isn't enough — vite emits hashed JS into dist/assets.
  // An index.html with no script bundle still blank-pages.
  const assetsDir = join(distDir, "assets")
  const hasAssets =
    existsSync(assetsDir) &&
    statSync(assetsDir).isDirectory() &&
    readdirSync(assetsDir).some(name => name.endsWith(".js"))
  if (!hasAssets) {
    return { ok: false, error: `dist/assets has no built JS bundle (expected vite output under ${assetsDir})` }
  }

  return { ok: true }
}

/**
 * The magpie binary that ships beside the app (`build/magpie/`).
 *
 * Same disease as the backend binaries, one step worse: this one is *downloaded*
 * by `scripts/fetch-magpie.mjs`, so its absence (offline build, failed fetch) is
 * invisible until the packaged app offers a vendor layer that cannot start. And
 * because the pin used to carry only a Windows asset, a macOS build happily
 * packaged a **Windows PE** named `magpie-cli` — present, plausible, unrunnable.
 * So this checks three things: it is here, it is big enough to be the real thing,
 * and its magic bytes belong to the platform we are packaging for.
 */
export function requiredMagpieBinary(platform = process.platform) {
  const name = platform === "win32" ? "magpie-cli.exe" : "magpie-cli"
  return {
    rel: join("magpie", name),
    label: "magpie vendor layer (subscription legs behind the gateway)",
    build: "npm run fetch:magpie",
    why: "without it the quota gateway has no vendor providers at all, and every subscription card goes dark",
  }
}

/** Do these 4 magic bytes belong to an executable this platform can run? */
export function magicMatchesPlatform(magic, platform = process.platform) {
  if (platform === "win32") return magic.startsWith("MZ")
  if (platform === "linux") return magic === "\x7fELF"
  if (platform === "darwin") {
    // Mach-O 64/32, both endiannesses, plus fat/universal binaries.
    return ["\xcf\xfa\xed\xfe", "\xce\xfa\xed\xfe", "\xfe\xed\xfa\xcf", "\xca\xfe\xba\xbe"].some(m => magic.startsWith(m))
  }
  return true
}

export function checkMagpieBinary(buildDir, platform = process.platform) {
  const item = requiredMagpieBinary(platform)
  const full = join(buildDir, item.rel)
  if (!existsSync(full)) {
    return { ok: false, problems: [{ ...item, full, reason: "missing" }], error: `magpie binary missing from ${buildDir}: ${item.rel}` }
  }
  const size = statSync(full).size
  const magic = readMagic(full)
  if (!looksRunnable(magic)) {
    return {
      ok: false,
      problems: [{ ...item, full, reason: `not a runnable binary (magic ${JSON.stringify(magic)})` }],
      error: `magpie binary at ${full} is not an executable`,
    }
  }
  if (!magicMatchesPlatform(magic, platform)) {
    return {
      ok: false,
      problems: [{ ...item, full, reason: `built for another platform (magic ${JSON.stringify(magic)}, packaging for ${platform})` }],
      error: `magpie binary at ${full} is not a ${platform} executable — the pin's asset for this platform is wrong`,
    }
  }
  if (size <= 20 * 1024 * 1024) {
    return {
      ok: false,
      problems: [{ ...item, full, reason: `too small to be the real binary (${(size / 1048576).toFixed(1)} MB)` }],
      error: `magpie binary at ${full} is only ${(size / 1048576).toFixed(1)} MB — the pin fetched the wrong thing`,
    }
  }

  // `magpie/version.json` names the copy this installer carries; the runtime
  // compares it against the pointer in the user's data directory, so a package
  // shipped without it quietly loses the "a fresh installer beats a stale
  // download" rule (apps/desktop/electron/magpie-channel.ts, rule 6). The only
  // way to get here nameless is a build tree fetched before the file existed —
  // which is exactly the case a rebuild fixes, so say that instead of shipping.
  const versionFile = join(buildDir, "magpie", "version.json")
  let named = null

  try {
    named = JSON.parse(readFileSync(versionFile, "utf8"))
  } catch {
    return {
      ok: false,
      problems: [{ ...item, full: versionFile, reason: "version.json missing or unreadable" }],
      error: `magpie/version.json is missing in ${buildDir} — run \`npm run fetch:magpie\` to name the packaged copy`,
    }
  }

  if (!named || typeof named.version !== "string" || !/^v\d+\.\d+\.\d+$/.test(named.version)) {
    return {
      ok: false,
      problems: [{ ...item, full: versionFile, reason: `no usable version (${JSON.stringify(named && named.version)})` }],
      error: `magpie/version.json in ${buildDir} carries no usable version — the app cannot tell a fresh installer from a stale download`,
    }
  }

  return { ok: true, problems: [] }
}

function main() {
  const desktopRoot = resolve(import.meta.dirname, "..")
  const distDir = join(desktopRoot, "dist")
  const result = checkDistBuilt(distDir)

  if (!result.ok) {
    console.error(`\n✗ assert-dist-built: ${result.error}`)
    console.error("  The renderer bundle is missing or incomplete, so packaging")
    console.error("  would produce an app that launches to a blank page.")
    console.error("  Re-run the build and check the tsc/vite output above for the")
    console.error("  real failure, then package again:")
    console.error(`    cd ${desktopRoot} && npm run build\n`)
    process.exit(1)
  }

  console.log("✓ assert-dist-built: dist/index.html + assets present")

  // Same idea for the backend bundle: these are compiled artefacts that a
  // checkout never contains, so their absence is invisible at build time and
  // only shows up as a dead feature for the person who installed the app.
  const backendDir = join(desktopRoot, "build", "backend")
  const backend = checkBackendBinaries(backendDir)

  if (!backend.ok) {
    const skip = process.env.PLOBI_SKIP_CHATLOG_ASSERT === "1"
    const lines = [
      "",
      `${skip ? "!" : "✗"} assert-dist-built: the backend bundle is missing required binaries.`,
      ...backend.problems.map(p => `    - ${p.rel}\n      what: ${p.label}\n      why:  ${p.why}\n      fix:  ${p.build}`),
      "",
      "  These are build artefacts of vendored projects (their bin/ is gitignored),",
      "  so a fresh checkout has none of them; they must be built before packaging.",
    ]
    if (skip) {
      console.warn(lines.join("\n"))
      console.warn("  PLOBI_SKIP_CHATLOG_ASSERT=1 is set -- continuing anyway (the package WILL be degraded).\n")
    } else {
      console.error(lines.join("\n"))
      console.error("  Emergency only (ships a knowingly broken package):")
      console.error("    PLOBI_SKIP_CHATLOG_ASSERT=1 npm run dist\n")
      process.exit(1)
    }
  } else {
    console.log("✓ assert-dist-built: backend binary manifest present")
  }

  // And the downloaded one. `npm run build` fetches it before this point; if the
  // fetch failed (offline, 404, hash mismatch) packaging must not go on, because
  // the package would look complete and behave like a vendored layer that is
  // simply absent.
  const magpie = checkMagpieBinary(join(desktopRoot, "build"), process.platform)
  if (!magpie.ok) {
    const skip = process.env.PLOBI_SKIP_MAGPIE_ASSERT === "1"
    const lines = [
      "",
      `${skip ? "!" : "✗"} assert-dist-built: ${magpie.error}`,
      ...magpie.problems.map(p => `    - ${p.rel}\n      what: ${p.label}\n      why:  ${p.why}\n      fix:  ${p.build}`),
    ]
    if (skip) {
      console.warn(lines.join("\n"))
      console.warn("  PLOBI_SKIP_MAGPIE_ASSERT=1 is set -- continuing anyway (the package WILL be degraded).\n")
    } else {
      console.error(lines.join("\n"))
      console.error("  Emergency only (ships a knowingly degraded package):")
      console.error("    PLOBI_SKIP_MAGPIE_ASSERT=1 npm run dist\n")
      process.exit(1)
    }
  } else {
    console.log("✓ assert-dist-built: magpie binary present and built for this platform")
  }
}

if (isMain(import.meta.url)) {
  main()
}

export default { checkDistBuilt, checkBackendBinaries, requiredBackendBinaries, checkMagpieBinary, requiredMagpieBinary }
