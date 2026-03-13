import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Package, X } from "lucide-react"
import { updateAgentRegistration } from "@/lib/api/agents"
import { BulkAssignDialog } from "./bulk-assign-dialog"
import { AgentRegistrationRow } from "./agent-registration-row"
import { toast } from "sonner"
import type { MergedAgent } from "./deployments-tab"

interface DeploymentsAgentsSectionProps {
  agents: MergedAgent[]
  envBundles: string[]
  allBundleNames: string[]
  selectedEnv: string
  onAgentUpdated: () => void
}

export function DeploymentsAgentsSection({
  agents,
  envBundles,
  allBundleNames,
  selectedEnv,
  onAgentUpdated,
}: DeploymentsAgentsSectionProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkOpen, setBulkOpen] = useState(false)
  const [updatingAgent, setUpdatingAgent] = useState<string | null>(null)

  // Reset selection when switching envs
  useEffect(() => {
    setSelected(new Set())
  }, [selectedEnv])

  const toggleSelect = (agentId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(agentId)) next.delete(agentId)
      else next.add(agentId)
      return next
    })
  }

  const toggleAll = () => {
    if (selected.size === agents.length) setSelected(new Set())
    else setSelected(new Set(agents.map((a) => a.agent_id)))
  }

  const handleAssignBundle = async (
    agentId: string,
    bundleName: string | null
  ) => {
    setUpdatingAgent(agentId)
    try {
      await updateAgentRegistration(agentId, {
        bundle_name: bundleName === "none" ? "" : bundleName,
      })
      toast.success(
        bundleName && bundleName !== "none"
          ? `Assigned ${bundleName} to ${agentId}`
          : `Cleared assignment for ${agentId}`
      )
      onAgentUpdated()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed")
    } finally {
      setUpdatingAgent(null)
    }
  }

  if (agents.length === 0) {
    return (
      <div className="flex h-20 items-center justify-center rounded-lg border border-dashed border-border">
        <p className="text-sm text-muted-foreground">
          No agents connected to {selectedEnv}. Agents appear here when they
          connect via SSE.
        </p>
      </div>
    )
  }

  // Use env-scoped bundles for the dropdown
  const dropdownBundles = envBundles.length > 0 ? envBundles : allBundleNames

  return (
    <div className="space-y-3">
      {selected.size > 0 && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            {selected.size} selected
          </span>
          <Button size="sm" onClick={() => setBulkOpen(true)}>
            <Package className="mr-1.5 size-3.5" />
            Bulk Assign
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelected(new Set())}
          >
            <X className="mr-1 size-3.5" />
            Clear
          </Button>
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <Checkbox
                checked={agents.length > 0 && selected.size === agents.length}
                onCheckedChange={toggleAll}
              />
            </TableHead>
            <TableHead>Agent ID</TableHead>
            <TableHead className="w-44">Assigned Bundle</TableHead>
            <TableHead className="w-24">Status</TableHead>
            <TableHead className="w-24">Last Seen</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {agents.map((agent) => (
            <AgentRegistrationRow
              key={agent.agent_id}
              agent={agent}
              selected={selected.has(agent.agent_id)}
              onToggleSelect={() => toggleSelect(agent.agent_id)}
              bundleNames={dropdownBundles}
              updating={updatingAgent === agent.agent_id}
              onAssignBundle={(v) => handleAssignBundle(agent.agent_id, v)}
            />
          ))}
        </TableBody>
      </Table>

      <BulkAssignDialog
        open={bulkOpen}
        onOpenChange={setBulkOpen}
        selectedAgentIds={Array.from(selected)}
        bundleNames={dropdownBundles}
        onAssigned={() => {
          setSelected(new Set())
          onAgentUpdated()
        }}
      />
    </div>
  )
}
