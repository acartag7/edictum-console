import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { useSearchParams } from "react-router"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { ChevronRight, GitBranch, History, Users } from "lucide-react"
import { listDeployments, listBundles } from "@/lib/api"
import type { DeploymentResponse, BundleSummary } from "@/lib/api"
import {
  getAgentStatus,
  getAgentRegistrations,
  type AgentStatusEntry,
  type AgentRegistration,
} from "@/lib/api/agents"
import { subscribeDashboardSSE } from "@/lib/sse"
import { EnvBundleCards } from "./env-status-cards"
import { DeployHistoryTable } from "./deploy-history-table"
import { DeploymentsAgentsSection } from "./deployments-agents-section"
import { AssignmentRulesSection } from "./assignment-rules-section"

/** Agent data merged from registrations + live SSE status. */
export interface MergedAgent {
  agent_id: string
  display_name: string | null
  tags: Record<string, string>
  bundle_name: string | null
  resolved_bundle: string | null
  last_seen_at: string | null
  env: string | null
  status: "current" | "drift" | "unknown" | "offline"
}

const ENV_ORDER = ["production", "staging", "development"]

function envSort(a: string, b: string) {
  const ai = ENV_ORDER.indexOf(a)
  const bi = ENV_ORDER.indexOf(b)
  if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  return a.localeCompare(b)
}

export function DeploymentsTab() {
  const [searchParams] = useSearchParams()

  const [deployments, setDeployments] = useState<DeploymentResponse[]>([])
  const [bundles, setBundles] = useState<BundleSummary[]>([])
  const [fleet, setFleet] = useState<AgentStatusEntry[]>([])
  const [registrations, setRegistrations] = useState<AgentRegistration[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const fetchGenRef = useRef(0)

  // Env tab — default from URL or "production"
  const [selectedEnv, setSelectedEnv] = useState(
    searchParams.get("env") || "production"
  )
  const [bundleFilter, setBundleFilter] = useState("all")

  // Reset bundle filter when switching envs
  const handleEnvChange = useCallback((env: string) => {
    setSelectedEnv(env)
    setBundleFilter("all")
  }, [])

  const fetchData = useCallback(async () => {
    const gen = ++fetchGenRef.current
    setError(null)
    try {
      const [deps, bndls, status, regs] = await Promise.all([
        listDeployments(undefined, undefined, 100),
        listBundles(),
        getAgentStatus(),
        getAgentRegistrations(),
      ])
      if (gen !== fetchGenRef.current) return
      setDeployments(deps)
      setBundles(bndls)
      setFleet(status.agents)
      setRegistrations(regs)
    } catch (e) {
      if (gen !== fetchGenRef.current) return
      setError(e instanceof Error ? e.message : "Failed to load deployments")
    } finally {
      if (gen === fetchGenRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    fetchData()
  }, [fetchData])

  useEffect(() => {
    return subscribeDashboardSSE({
      bundle_deployed: () => fetchData(),
      composition_changed: () => fetchData(),
      assignment_changed: () => fetchData(),
    })
  }, [fetchData])

  // Derive all known environments
  const allEnvs = useMemo(() => {
    const envs = new Set<string>()
    for (const d of deployments) envs.add(d.env)
    for (const b of bundles) b.deployed_envs.forEach((e) => envs.add(e))
    for (const a of fleet) envs.add(a.env)
    return [...envs].sort(envSort)
  }, [deployments, bundles, fleet])

  // If selectedEnv isn't in the list, fall back to first
  const activeEnv = allEnvs.includes(selectedEnv)
    ? selectedEnv
    : allEnvs[0] ?? "production"

  // Bundles deployed to the active env
  const envBundles = useMemo(
    () => bundles.filter((b) => b.deployed_envs.includes(activeEnv)).map((b) => b.name),
    [bundles, activeEnv]
  )

  const allBundleNames = useMemo(() => bundles.map((b) => b.name), [bundles])

  // Merge registrations + live status
  const mergedAgents = useMemo(() => {
    const statusMap = new Map<string, AgentStatusEntry>()
    for (const a of fleet) {
      // If an agent has multiple connections, keep the one matching activeEnv
      const existing = statusMap.get(a.agent_id)
      if (!existing || a.env === activeEnv) statusMap.set(a.agent_id, a)
    }

    const regMap = new Map<string, AgentRegistration>()
    for (const r of registrations) regMap.set(r.agent_id, r)

    const allIds = new Set([
      ...registrations.map((r) => r.agent_id),
      ...fleet.map((a) => a.agent_id),
    ])

    const result: MergedAgent[] = []
    for (const agentId of allIds) {
      const reg = regMap.get(agentId)
      const live = statusMap.get(agentId)
      result.push({
        agent_id: agentId,
        display_name: reg?.display_name ?? null,
        tags: reg?.tags ?? {},
        bundle_name: reg?.bundle_name ?? null,
        resolved_bundle: reg?.resolved_bundle ?? null,
        last_seen_at: reg?.last_seen_at ?? live?.connected_at ?? null,
        env: live?.env ?? null,
        status: live
          ? (["current", "drift", "unknown"].includes(live.status)
              ? (live.status as "current" | "drift" | "unknown")
              : "unknown")
          : "offline",
      })
    }
    return result
  }, [registrations, fleet, activeEnv])

  // Agents filtered to the active env
  const envAgents = useMemo(
    () => mergedAgents.filter((a) => a.env === activeEnv),
    [mergedAgents, activeEnv]
  )

  // Deployments filtered for history
  const filteredDeployments = useMemo(() => {
    return deployments.filter((d) => {
      if (d.env !== activeEnv) return false
      if (bundleFilter !== "all" && d.bundle_name !== bundleFilter) return false
      return true
    })
  }, [deployments, activeEnv, bundleFilter])

  if (loading && allEnvs.length === 0) {
    return (
      <div className="space-y-4 pt-4">
        <Skeleton className="h-9 w-64 rounded-lg" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-[88px] rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-40 rounded-lg" />
      </div>
    )
  }

  return (
    <Tabs value={activeEnv} onValueChange={handleEnvChange}>
      <TabsList variant="line">
        {allEnvs.map((env) => (
          <TabsTrigger key={env} value={env} className="capitalize">
            {env}
          </TabsTrigger>
        ))}
      </TabsList>

      {allEnvs.map((env) => (
        <TabsContent key={env} value={env}>
          <div className="space-y-6 pt-2">
            <EnvBundleCards
              bundles={bundles}
              deployments={deployments}
              mergedAgents={mergedAgents}
              selectedEnv={env}
              loading={loading}
            />

            <Separator />

            <div>
              <div className="mb-1 flex items-center gap-2">
                <Users className="size-4 text-muted-foreground" />
                <h3 className="text-sm font-medium text-foreground">
                  Agents in {env}
                </h3>
              </div>
              <p className="mb-3 text-xs text-muted-foreground">
                Assign a bundle to each agent. Only assigned agents receive
                updates when you deploy a new version.
              </p>
              <DeploymentsAgentsSection
                agents={envAgents}
                envBundles={envBundles}
                allBundleNames={allBundleNames}
                selectedEnv={env}
                onAgentUpdated={fetchData}
              />
            </div>

            <Separator />

            <CollapsibleSection
              icon={<GitBranch className="size-4" />}
              title="Assignment Rules"
            >
              <AssignmentRulesSection bundleNames={allBundleNames} />
            </CollapsibleSection>

            <Separator />

            <CollapsibleSection
              icon={<History className="size-4" />}
              title="Deploy History"
            >
              <DeployHistoryTable
                deployments={filteredDeployments}
                bundleNames={envBundles}
                selectedEnv={env}
                bundleFilter={bundleFilter}
                onBundleFilterChange={setBundleFilter}
                loading={loading}
                error={error}
                onRetry={() => {
                  setLoading(true)
                  fetchData()
                }}
              />
            </CollapsibleSection>
          </div>
        </TabsContent>
      ))}
    </Tabs>
  )
}

function CollapsibleSection({
  icon,
  title,
  defaultOpen = false,
  children,
}: {
  icon: React.ReactNode
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex w-full items-center gap-2 py-1 text-sm font-medium text-foreground hover:text-foreground/80">
        <ChevronRight
          className={`size-4 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {icon}
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-3">{children}</CollapsibleContent>
    </Collapsible>
  )
}
