// P11 -- `/clear` keeps the live model/provider (NUMBERS-CLEAR hooks).
// Copied by apply_overlay.ps1 step 1b to ui-tui/src/__tests__/.
//
// /clear forwards the running model/provider so session.create pins them as a
// per-session override; /new forwards nothing and starts on the config default.
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createSlashHandler } from '../app/createSlashHandler.js'
import type { SlashHandlerContext } from '../app/interfaces.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'

const buildCtx = () =>
  ({
    composer: { enqueue: vi.fn(), queueRef: { current: [] }, setInput: vi.fn() },
    gateway: { gw: { request: vi.fn(() => Promise.resolve({})) }, rpc: vi.fn(() => Promise.resolve({})) },
    local: { catalog: null },
    session: { guardBusySessionSwitch: vi.fn(() => false), newSession: vi.fn() },
    slashFlightRef: { current: 0 },
    transcript: { page: vi.fn(), panel: vi.fn(), send: vi.fn(), sys: vi.fn() }
  }) as unknown as SlashHandlerContext

const live = { model: 'picked/model', provider: 'picked-provider', skills: {}, tools: {} }

describe('NUMBERS-CLEAR: /clear keeps the live model', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    patchUiState({ destructiveSlashConfirm: false, info: live })
  })

  it('/clear forwards the live model and provider', () => {
    const ctx = buildCtx()

    createSlashHandler(ctx)('/clear')

    expect(ctx.session.newSession).toHaveBeenCalledWith(undefined, undefined, {
      model: 'picked/model',
      provider: 'picked-provider'
    })
  })

  it('/new still resets to the config default', () => {
    const ctx = buildCtx()

    createSlashHandler(ctx)('/new')

    expect(ctx.session.newSession).toHaveBeenCalledWith('new session started', undefined)
  })
})
