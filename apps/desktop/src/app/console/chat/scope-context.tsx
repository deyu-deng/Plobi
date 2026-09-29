/**
 * ScopeChatContext — the console back-end for {@link ChatContext}.
 *
 * Wires the SAME ChatBar the original chat uses, but its data is the base
 * session layer (per ARCH-RULINGS 2026-09-02 裁定 1): the center reuses the
 * `$messages` / `$busy` / `$activeSessionAwaitingInput` / `$composerAttachments`
 * atoms the full-screen chat uses, and `onSubmit` / `onCancel` are injected by
 * the shell (the real `submitText` / `cancelRun` from the desktop controller),
 * so the console never owns a second session or a mock transport.
 *
 * - `messages` is the global `$messages` atom (passed BY REFERENCE; the base
 *   ChatBar subscribes to it directly, exactly like `SessionChatContext`).
 * - `submit` / `cancel` are supplied by the owner (`ChatSurface` ← controller),
 *   keeping this module free of any send-side mock.
 * - `modelMenuContent` is the live model.options dropdown the status-bar /
 *   composer pill used to host; without it the pill falls back to the full
 *   picker dialog (the L1/L2 regression the QA caught).
 * - the attach surface (`composer` prop) is the controller's ONE
 *   `useComposerActions` instance, injected through here so the "+" menu items,
 *   image paste and Finder drops reach the base upload pipeline.
 */

import { useStore } from '@nanostores/react'
import { type ReactNode, useMemo } from 'react'

import { $composerAttachments } from '@/store/composer'
import { $activeSessionAwaitingInput } from '@/store/prompts'
import { $busy, $currentCwd, $currentModel, $currentProvider, $gatewayState, $messages } from '@/store/session'

import type { ChatBarState } from '../../chat/composer/types'
import { ChatContext, type ChatContextValue } from '../../chat/context'
import type { useComposerActions } from '../../chat/hooks/use-composer-actions'

export type SubmitFn = ChatContextValue['onSubmit']
export type CancelFn = ChatContextValue['onCancel']

/**
 * The attach slice of the controller's `useComposerActions` (files / folders /
 * images / clipboard image / OS drops). Passed IN by the shell, never re-derived
 * here — calling the hook twice would give the surface a second set of state and
 * listeners.
 *
 * Its absence is the "greyed-out +" bug: the "+" button is gated only on
 * `state.tools.enabled` (`chat/composer/context-menu.tsx:54`), which is
 * hardcoded true below, while EVERY item inside is gated on its own callback
 * (`:67-82`), and `handlePaste` (`chat/composer/index.tsx:324`) only attaches
 * when `onAttachImageBlob` is supplied.
 */
export type ScopeComposerActions = Partial<
  Pick<
    ReturnType<typeof useComposerActions>,
    | 'addContextRefAttachment'
    | 'attachDroppedItems'
    | 'attachImageBlob'
    | 'pasteClipboardImage'
    | 'pickContextPaths'
    | 'pickImages'
  >
>

const NO_COMPOSER_ACTIONS: ScopeComposerActions = {}

export function ScopeChatContext({
  children,
  composer = NO_COMPOSER_ACTIONS,
  submit,
  cancel,
  modelMenuContent
}: {
  children: ReactNode
  composer?: ScopeComposerActions
  submit?: SubmitFn
  cancel?: CancelFn
  modelMenuContent?: ReactNode
}) {
  const awaitingInput = useStore($activeSessionAwaitingInput)
  const busy = useStore($busy)
  const currentCwd = useStore($currentCwd)
  const currentModel = useStore($currentModel)
  const currentProvider = useStore($currentProvider)
  const gatewayState = useStore($gatewayState)

  const gatewayOpen = gatewayState === 'open'

  const state: ChatBarState = useMemo(
    () => ({
      model: {
        model: currentModel,
        provider: currentProvider,
        canSwitch: gatewayOpen,
        loading: !gatewayOpen || (!currentModel && !currentProvider),
        modelMenuContent
      },
      tools: { enabled: true, label: 'Add context' },
      voice: { enabled: true, active: false }
    }),
    [currentModel, currentProvider, gatewayOpen, modelMenuContent]
  )

  const {
    addContextRefAttachment,
    attachDroppedItems,
    attachImageBlob,
    pasteClipboardImage,
    pickContextPaths,
    pickImages
  } = composer

  const value: ChatContextValue = useMemo(
    () => ({
      messages: $messages,
      awaitingInput,
      attachments: $composerAttachments,
      busy,
      cwd: currentCwd || null,
      disabled: !gatewayOpen,
      state,
      onAddContextRef: addContextRefAttachment,
      onAttachDroppedItems: attachDroppedItems,
      onAttachImageBlob: attachImageBlob,
      onPasteClipboardImage: pasteClipboardImage,
      // `pickContextPaths` takes the context kind as its first argument, and the
      // menu hands its item callback a select event — so bind the kind here
      // instead of passing the action straight through.
      onPickFiles: pickContextPaths ? () => void pickContextPaths('file') : undefined,
      onPickFolders: pickContextPaths ? () => void pickContextPaths('folder') : undefined,
      onPickImages: pickImages ? () => void pickImages() : undefined,
      onSubmit: async (text, options) => {
        if (!submit) {
          return false
        }

        return await submit(text, options)
      },
      onCancel: () => {
        void cancel?.()
      },
      onRemoveAttachment: (id: string) => {
        $composerAttachments.set($composerAttachments.get().filter(attachment => attachment.id !== id))
      }
    }),
    [
      awaitingInput,
      busy,
      gatewayOpen,
      state,
      submit,
      cancel,
      addContextRefAttachment,
      attachDroppedItems,
      attachImageBlob,
      currentCwd,
      pasteClipboardImage,
      pickContextPaths,
      pickImages
    ]
  )

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}
