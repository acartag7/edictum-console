import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { TableCell, TableRow } from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { AlertTriangle, Loader2 } from "lucide-react"
import type { MergedAgent } from "./deployments-tab"
import { formatRelativeTime } from "@/lib/format"

const STATUS_STYLES: Record<
  MergedAgent["status"],
  { dot: string; label: string }
> = {
  current: { dot: "bg-emerald-500", label: "Current" },
  drift: { dot: "bg-amber-500", label: "Drift" },
  unknown: { dot: "bg-zinc-400", label: "Unknown" },
  offline: { dot: "border-2 border-zinc-400 bg-transparent", label: "Offline" },
}

const SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  explicit: {
    label: "Explicit",
    color:
      "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  },
  rule: {
    label: "Rule",
    color:
      "bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/30",
  },
}

interface AgentRegistrationRowProps {
  agent: MergedAgent
  selected: boolean
  onToggleSelect: () => void
  bundleNames: string[]
  updating: boolean
  onAssignBundle: (bundleName: string | null) => void
}

export function AgentRegistrationRow({
  agent,
  selected,
  onToggleSelect,
  bundleNames,
  updating,
  onAssignBundle,
}: AgentRegistrationRowProps) {
  const isUnassigned = !agent.bundle_name && !agent.resolved_bundle
  const statusStyle = STATUS_STYLES[agent.status] ?? STATUS_STYLES.unknown
  const source = agent.bundle_name
    ? "explicit"
    : agent.resolved_bundle
      ? "rule"
      : null
  const sourceInfo = source ? SOURCE_LABELS[source] : null

  return (
    <TableRow>
      <TableCell>
        <Checkbox checked={selected} onCheckedChange={onToggleSelect} />
      </TableCell>

      {/* Agent ID + unassigned warning */}
      <TableCell>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-sm">{agent.agent_id}</span>
          {isUnassigned && (
            <Tooltip>
              <TooltipTrigger>
                <AlertTriangle className="size-3.5 text-amber-500" />
              </TooltipTrigger>
              <TooltipContent>
                No bundle assigned — assign a bundle so this agent receives
                contract updates
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </TableCell>

      {/* Assigned Bundle dropdown */}
      <TableCell>
        {updating ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : (
          <div className="flex items-center gap-1.5">
            <Select
              value={agent.bundle_name ?? "none"}
              onValueChange={(v) => onAssignBundle(v)}
            >
              <SelectTrigger
                className={`h-8 w-40 text-xs ${isUnassigned ? "border-amber-500/50" : ""}`}
              >
                <SelectValue placeholder="Not assigned" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">
                  <span className="text-muted-foreground">Not assigned</span>
                </SelectItem>
                {bundleNames.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {sourceInfo && (
              <Badge
                variant="outline"
                className={`text-[10px] ${sourceInfo.color}`}
              >
                {sourceInfo.label}
              </Badge>
            )}
          </div>
        )}
      </TableCell>

      {/* Status dot */}
      <TableCell>
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-block size-2 rounded-full ${statusStyle.dot}`}
          />
          <span className="text-xs text-muted-foreground">
            {statusStyle.label}
          </span>
        </div>
      </TableCell>

      {/* Last Seen */}
      <TableCell>
        <Tooltip>
          <TooltipTrigger className="text-sm text-muted-foreground">
            {agent.last_seen_at
              ? formatRelativeTime(agent.last_seen_at)
              : "never"}
          </TooltipTrigger>
          <TooltipContent>
            {agent.last_seen_at
              ? new Date(agent.last_seen_at).toLocaleString()
              : "Never connected"}
          </TooltipContent>
        </Tooltip>
      </TableCell>
    </TableRow>
  )
}
