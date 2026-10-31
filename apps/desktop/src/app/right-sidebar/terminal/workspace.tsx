import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $agentScope } from '@/store/agent-scope'
import { $backgroundStatusBySession, $sessionAgentScope } from '@/store/composer-status'

import { seedAgentTerminalCommand, syncAgentTerminalSnapshot } from './agent-terminal-stream'
import { setActiveTerminalId } from './buffer'
import { AgentTerminalInstance, TerminalInstance } from './instance'
import { $activeTerminalId, $terminals, ensureAgentTerminal } from './terminals'

interface TerminalWorkspaceProps {
  onAddSelectionToChat: (text: string, label?: string) => void
}

/** The persistent-overlay layer: the stack of live xterm instances (only these
 *  must stay in the fixed overlay, for the WebGL host). Mount/visibility is owned
 *  by PersistentTerminal (latched so shells survive hiding); the tab rail and
 *  new-terminal control live in the pane DOM — see TerminalPaneChrome. */
export function TerminalWorkspace({ onAddSelectionToChat }: TerminalWorkspaceProps) {
  const terminals = useStore($terminals)
  const activeId = useStore($activeTerminalId)
  const scope = useStore($agentScope)
  const background = useStore($backgroundStatusBySession)
  const sessionScope = useStore($sessionAgentScope)

  // Mirror the tab selection into the agent reader (read_terminal reads it).
  useEffect(() => {
    const unsubscribe = $activeTerminalId.subscribe(setActiveTerminalId)

    return () => {
      unsubscribe()
      setActiveTerminalId(null)
    }
  }, [])

  // Surface the agent's background processes as read-only tabs (once each).
  // Live chunks stream via agent.terminal.output; the process-list snapshot also
  // seeds/falls back so the tab never stays blank if the stream races startup.
  //
  // Scoped to the Agent on screen: this loop used to walk *every* session's
  // processes, which put another Agent's background tabs on this Agent's rail.
  // A session with no stamp yet (reconcile hasn't run since it started) counts
  // as the current Agent — it is running here, right now.
  useEffect(() => {
    for (const [sid, list] of Object.entries(background)) {
      const owner = sessionScope[sid] || scope

      if (owner !== scope) {
        continue
      }

      for (const item of list) {
        ensureAgentTerminal(item.id, item.title, scope)
        seedAgentTerminalCommand(item.id, item.title)
        syncAgentTerminalSnapshot(item.id, item.output ?? '')
      }
    }
  }, [background, scope, sessionScope])

  return (
    <>
      {terminals.map(term =>
        term.kind === 'agent' ? (
          <AgentTerminalInstance active={term.id === activeId} id={term.id} key={term.id} procId={term.procId!} />
        ) : (
          <TerminalInstance
            active={term.id === activeId}
            cwd={term.cwd}
            id={term.id}
            key={term.id}
            onAddSelectionToChat={onAddSelectionToChat}
            reviveBuffer={term.reviveBuffer}
          />
        )
      )}
    </>
  )
}
