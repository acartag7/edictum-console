import { useMemo } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Package, Users } from "lucide-react"
import type { DeploymentResponse, BundleSummary } from "@/lib/api"
import type { MergedAgent } from "./deployments-tab"
import { formatRelativeTime } from "@/lib/format"

interface EnvBundleCardsProps {
  bundles: BundleSummary[]
  deployments: DeploymentResponse[]
  mergedAgents: MergedAgent[]
  selectedEnv: string
  loading: boolean
}

/** Show one card per bundle deployed to the selected environment. */
export function EnvBundleCards({
  bundles,
  deployments,
  mergedAgents,
  selectedEnv,
  loading,
}: EnvBundleCardsProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 2 }).map((_, i) => (
          <Skeleton key={i} className="h-[88px] rounded-lg" />
        ))}
      </div>
    )
  }

  // Bundles deployed to this env
  const envBundles = useMemo(
    () => bundles.filter((b) => b.deployed_envs.includes(selectedEnv)),
    [bundles, selectedEnv]
  )

  if (envBundles.length === 0) {
    return (
      <div className="flex h-20 items-center justify-center rounded-lg border border-dashed border-border">
        <p className="text-sm text-muted-foreground">
          No bundles deployed to {selectedEnv}. Deploy a bundle from the
          Bundles tab.
        </p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {envBundles.map((bundle) => (
        <BundleEnvCard
          key={bundle.name}
          bundle={bundle}
          deployments={deployments}
          mergedAgents={mergedAgents}
          env={selectedEnv}
        />
      ))}
    </div>
  )
}

function BundleEnvCard({
  bundle,
  deployments,
  mergedAgents,
  env,
}: {
  bundle: BundleSummary
  deployments: DeploymentResponse[]
  mergedAgents: MergedAgent[]
  env: string
}) {
  // Latest deployment of this bundle in this env
  const latestDeploy = useMemo(() => {
    const matches = deployments
      .filter((d) => d.bundle_name === bundle.name && d.env === env)
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
    return matches[0] ?? null
  }, [deployments, bundle.name, env])

  // Count of agents in this env assigned to this bundle
  const assignedCount = useMemo(
    () =>
      mergedAgents.filter(
        (a) =>
          a.env === env &&
          (a.bundle_name === bundle.name ||
            a.resolved_bundle === bundle.name)
      ).length,
    [mergedAgents, bundle.name, env]
  )

  return (
    <Card>
      <CardContent className="space-y-1.5 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Package className="size-3.5 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">
              {bundle.name}
            </span>
          </div>
          {latestDeploy && (
            <Badge variant="outline" className="text-[10px]">
              v{latestDeploy.bundle_version}
            </Badge>
          )}
        </div>
        {latestDeploy && (
          <p className="text-xs text-muted-foreground">
            Deployed {formatRelativeTime(latestDeploy.created_at)} by{" "}
            {latestDeploy.deployed_by}
          </p>
        )}
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Users className="size-3" />
          <span>
            {assignedCount} agent{assignedCount !== 1 ? "s" : ""} assigned
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
