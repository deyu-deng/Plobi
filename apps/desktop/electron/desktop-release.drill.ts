import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * Drill the one-click path WITHOUT publishing anything:
 *   fetch the latest release → pick this platform's installer → download it
 *   → write the helper → print it.
 *
 * The install itself is NOT run here (that would replace the running app);
 * the helper's command sequence is printed so it can be read.
 */
import { pickInstallerAsset, writeUpdateHelper } from './desktop-release'
import { downloadAsset, releaseLookup } from './desktop-release'

const lookup = await releaseLookup.lookup()
const release = lookup.release

if (!release) {
  console.log(`REACH: failed (${lookup.reason ?? 'unreachable'}${lookup.status ? `, HTTP ${lookup.status}` : ''})`)
  process.exit(2)
}

console.log(`RELEASE: ${release.tag} (version ${release.version})`)
console.log(`  assets: ${release.assets.map((a) => a.name).join(', ')}`)

const asset = pickInstallerAsset(release)
console.log(`PICKED: ${asset ? asset.name : '(none for this platform)'} (${asset ? (asset.size / 1048576).toFixed(1) : '-'} MB)`)

if (asset) {
  const dest = path.join(os.tmpdir(), 'drill-' + asset.name)
  const ok = await downloadAsset(asset.browser_download_url, dest)
  const size = ok ? fs.statSync(dest).size : 0
  console.log(`DOWNLOAD: ${ok ? 'ok' : 'FAILED'} → ${dest} (${(size / 1048576).toFixed(1)} MB)`)

  if (ok) {
    const helper = writeUpdateHelper({
      installerPath: dest,
      installDir: 'D:\\Software\\Plobi',
      appPath: 'D:\\Software\\Plobi\\Plobi.exe',
      plobiHome: 'D:\\Data\\AppData\\plobi',
      tag: release.tag
    })

    console.log(`HELPER: ${helper}`)
    console.log('--- the helper would run ---')
    console.log(fs.readFileSync(helper, 'utf8'))
  }
}
