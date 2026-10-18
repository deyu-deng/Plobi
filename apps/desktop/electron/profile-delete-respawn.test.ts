'use strict'

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const ELECTRON_DIR = import.meta.dirname

function readElectronFile(name) {
  return fs.readFileSync(path.join(ELECTRON_DIR, name), 'utf8').replace(/\r\n/g, '\n')
}

// ---------------------------------------------------------------------------
// prepareProfileDeleteRequest must return the torn-down profile name so the
// caller can skip ensureBackend for that profile (issue #52279).
// ---------------------------------------------------------------------------

test('prepareProfileDeleteRequest returns the torn-down profile name', () => {
  const source = readElectronFile('main.ts')

  // Locate the function definition and its closing brace.
  const fnStart = source.indexOf('async function prepareProfileDeleteRequest(')
  assert.notEqual(fnStart, -1, 'prepareProfileDeleteRequest function not found')

  // The function must contain "return profile" (pool and primary paths).
  const fnBody = source.slice(fnStart, fnStart + 800)
  const returnProfileCount = (fnBody.match(/return profile/g) || []).length
  assert.ok(
    returnProfileCount >= 2,
    `expected at least 2 "return profile" statements (primary + pool paths), found ${returnProfileCount}`
  )

  // The early-exit guard must return null (not void/undefined).
  assert.match(fnBody, /return null/, 'early-exit guard should return null, not undefined')
})

test('plobi:api handler never spawns a pool backend (single gateway)', () => {
  const source = readElectronFile('main.ts')

  // WP-AGENT-SINGLE-GATEWAY §3.3：桌面一个后端服务所有 Agent。handler 恒路由
  // 主后端（ensureBackend(null)）——profile-delete 的特判（issue #52279：
  // 删除后不许起新池后端）被**结构性**取代：再也不存在"为该 profile 起新
  // 进程"这条路，比条件特判更强。
  assert.match(
    source,
    /const connection = await ensureBackend\(null\)/,
    'handler should always route to the primary backend'
  )

  // 身份从"选进程"改成"带下去当参数"：X-Plobi-Agent 请求头，后端
  // request_profile() 读（plobi/console/router.py）。
  assert.match(
    source,
    /headers: \{ 'X-Plobi-Agent': String\(profile\) \}/,
    'handler should carry the request-level identity as a header'
  );

  // prepareProfileDeleteRequest 的其余副作用保留（删除前的准备仍在跑），
  // 只是返回值不再参与路由。
  assert.match(
    source,
    /await prepareProfileDeleteRequest\(request\)/,
    'profile-delete preparation side effects must be preserved'
  )
})
