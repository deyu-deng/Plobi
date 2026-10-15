import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import stageEgressAssets, { EGRESS_ASSETS, planStaging } from './stage-egress-assets.mjs'

function tempRoot(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `plobi-egress-assets-${label}-`))
}

test('staging copies the three files by name from any layout, newest wins, and leaves no marker', async () => {
  const root = tempRoot('copy')
  try {
    const src = path.join(root, 'src')
    // A vendor dir and the engine's own workdir both hold copies; the stale one is older.
    fs.mkdirSync(path.join(src, 'vendor', 'bin'), { recursive: true })
    fs.mkdirSync(path.join(src, 'home-egress'), { recursive: true })
    fs.writeFileSync(path.join(src, 'vendor', 'bin', 'mihomo.exe'), 'n'.repeat(4096), 'utf8')
    fs.writeFileSync(path.join(src, 'home-egress', 'GeoSite.dat'), 'fresh'.repeat(20), 'utf8')
    fs.writeFileSync(path.join(src, 'vendor', 'geoip.metadb'), 'i'.repeat(1024), 'utf8')
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
    assert.deepEqual(partial.missing.sort(), ['GeoSite.dat', 'geoip.metadb'], partial.missing)
    const marker = fs.readFileSync(path.join(dest, 'MISSING.txt'), 'utf8')

    assert.match(marker, /GeoSite\.dat/, marker)
    assert.match(marker, /geoip\.metadb/, marker)
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
