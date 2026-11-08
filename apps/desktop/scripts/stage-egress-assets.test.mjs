import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import stageEgressAssets, { EGRESS_ASSETS, planStaging } from './stage-egress-assets.mjs'

function tempRoot(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `plobi-egress-assets-${label}-`))
}

test('staging copies every shipped file by name from any layout, newest wins, and leaves no marker', async () => {
  const root = tempRoot('copy')
  try {
    const src = path.join(root, 'src')
    // A vendor dir and the engine's own workdir both hold copies; the stale one is older.
    fs.mkdirSync(path.join(src, 'vendor', 'bin'), { recursive: true })
    fs.mkdirSync(path.join(src, 'home-egress'), { recursive: true })
    fs.writeFileSync(path.join(src, 'vendor', 'bin', 'mihomo.exe'), 'n'.repeat(4096), 'utf8')
    fs.writeFileSync(path.join(src, 'home-egress', 'GeoSite.dat'), 'fresh'.repeat(20), 'utf8')
    fs.writeFileSync(path.join(src, 'vendor', 'geoip.metadb'), 'i'.repeat(1024), 'utf8')
    fs.writeFileSync(path.join(src, 'vendor', 'bin', 'wintun.dll'), 'w'.repeat(512), 'utf8')
    const dest = path.join(root, 'build', 'egress')
    const stale = path.join(dest, 'GeoSite.dat')
    fs.mkdirSync(dest, { recursive: true })
    fs.writeFileSync(stale, 'x')
    fs.utimesSync(stale, new Date(1_700_000_000_000), new Date(1_700_000_000_000))
    const oldCopy = path.join(src, 'home-egress', 'mihomo.exe')
    fs.writeFileSync(oldCopy, 'o'.repeat(10))
    fs.utimesSync(oldCopy, new Date(1_600_000_000_000), new Date(1_600_000_000_000))

    const plan = await stageEgressAssets({ srcDir: src, destDir: dest, log: () => {} })

    assert.deepEqual(plan.missing, [], plan.missing)
    assert.equal(plan.copy.length, EGRESS_ASSETS.length, plan.copy)
    assert.equal(fs.readFileSync(path.join(dest, 'bin', 'mihomo.exe'), 'utf8'), 'n'.repeat(4096), 'the newer binary wins')
    assert.equal(fs.readFileSync(path.join(dest, 'GeoSite.dat'), 'utf8'), 'fresh'.repeat(20))
    assert.equal(fs.readFileSync(path.join(dest, 'geoip.metadb'), 'utf8'), 'i'.repeat(1024))
    assert.equal(fs.readFileSync(path.join(dest, 'bin', 'wintun.dll'), 'utf8'), 'w'.repeat(512), 'TUN 的驱动必须与 mihomo.exe 同在 bin/')
    assert.equal(fs.existsSync(path.join(dest, 'MISSING.txt')), false, 'a complete bundle must not leave a marker')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a bundle without the engine says so in the package instead of failing the build', async () => {
  const root = tempRoot('missing')
  try {
    const src = path.join(root, 'src')
    fs.mkdirSync(path.join(src, 'bin'), { recursive: true })
    fs.writeFileSync(path.join(src, 'bin', 'mihomo.exe'), 'b'.repeat(64), 'utf8')
    const dest = path.join(root, 'build', 'egress')

    const partial = await stageEgressAssets({ srcDir: src, destDir: dest, log: () => {} })

    assert.deepEqual(partial.copy.map((item) => item.name), ['mihomo.exe'], partial.copy)
    assert.deepEqual(partial.missing.sort(), ['GeoSite.dat', 'geoip.metadb', 'wintun.dll'], partial.missing)
    const marker = fs.readFileSync(path.join(dest, 'MISSING.txt'), 'utf8')

    assert.match(marker, /GeoSite\.dat/, marker)
    assert.match(marker, /geoip\.metadb/, marker)
    assert.match(marker, /wintun\.dll/, 'a bundle without the TUN driver cannot do TUN — say it')
    assert.match(marker, /PLOBI_EGRESS_SRC/, 'the marker has to name the build-side fix')

    // No source at all (the state of a machine that simply never set the variable).
    const none = planStaging({ srcDir: '', destDir: dest })

    assert.deepEqual(none.copy, [], none.copy)
    assert.equal(none.missing.length, EGRESS_ASSETS.length, none.missing)
    assert.deepEqual(planStaging({ srcDir: path.join(root, 'nope'), destDir: dest }).missing.length, EGRESS_ASSETS.length)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a second pass with no source does not un-ship what the first pass already put there', async () => {
  const root = tempRoot('twice')
  try {
    const src = path.join(root, 'src')
    for (const file of EGRESS_ASSETS) {
      fs.mkdirSync(path.join(src, file.subdir), { recursive: true })
      fs.writeFileSync(path.join(src, file.subdir, file.name), `${file.name}-payload`, 'utf8')
    }
    const dest = path.join(root, 'build', 'egress')

    const first = await stageEgressAssets({ srcDir: src, destDir: dest, log: () => {} })

    assert.deepEqual(first.missing, [])
    assert.equal(fs.existsSync(path.join(dest, 'MISSING.txt')), false, 'a complete pass leaves no marker')

    // electron-builder 在多 target / 多 arch 下会把 before-pack 调上不止一次，其中一次可能
    // 拿不到源。这一趟的 missing 是四件全缺，但目录里其实四件齐着 —— 不能因此写一张
    // 「这版没带」的纸，把上一次的成果在纸面上注销掉（11-08 那个包就是这么被误读的）。
    const second = await stageEgressAssets({ srcDir: '', destDir: dest, log: () => {} })

    assert.equal(second.missing.length, EGRESS_ASSETS.length, 'the pass reports what it could not find')
    assert.equal(fs.existsSync(path.join(dest, 'MISSING.txt')), false, 'the marker describes the directory, not the pass')

    for (const file of EGRESS_ASSETS) {
      assert.equal(fs.existsSync(path.join(dest, file.subdir, file.name)), true, `${file.name} survived`)
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a directory that really is short gets a marker naming only what is absent', async () => {
  const root = tempRoot('partial')
  try {
    const src = path.join(root, 'src')
    const [kept, ...dropped] = EGRESS_ASSETS
    fs.mkdirSync(path.join(src, kept.subdir), { recursive: true })
    fs.writeFileSync(path.join(src, kept.subdir, kept.name), 'only-one', 'utf8')
    const dest = path.join(root, 'build', 'egress')

    await stageEgressAssets({ srcDir: src, destDir: dest, log: () => {} })

    const marker = fs.readFileSync(path.join(dest, 'MISSING.txt'), 'utf8')

    assert.doesNotMatch(marker, new RegExp(`missing: ${kept.name}$`, 'm'), 'the file that made it is not on the list')
    for (const file of dropped) {
      assert.match(marker, new RegExp(`missing: ${file.name}$`, 'm'), `${file.name} has to be named`)
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
