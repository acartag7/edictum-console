import { useState, useEffect, useCallback, useRef, useMemo } from "react"
import { useSearchParams, useLocation } from "react-router"
import {
  Plus, Upload, Search, AlertCircle,
  ShieldCheck, Eye, Timer, Box,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { listContracts, type LibraryContractSummary } from "@/lib/api/contracts"
import { subscribeDashboardSSE } from "@/lib/sse"
import { AiGeneratorHero } from "./ai-generator-hero"
import { ContractCard } from "./contract-card"
import { ContractEditorDialog, type FromEventContext } from "./contract-editor-dialog"
import { ImportDialog } from "./import-dialog"
import { DeleteDialog } from "./delete-dialog"
import { ContractDetailSheet } from "./contract-detail-sheet"
import { useLibraryActions } from "./use-library-actions"

const TYPE_OPTIONS = ["all", "pre", "post", "session", "sandbox"] as const

const TYPE_SECTIONS = [
  {
    type: "pre",
    label: "Preconditions",
    description: "Checked before a tool executes. Block dangerous calls before they happen.",
    icon: ShieldCheck,
    color: "text-amber-600 dark:text-amber-400",
    borderColor: "border-amber-500/40",
  },
  {
    type: "post",
    label: "Postconditions",
    description: "Checked after execution. Detect PII, credentials, or errors in output.",
    icon: Eye,
    color: "text-emerald-600 dark:text-emerald-400",
    borderColor: "border-emerald-500/40",
  },
  {
    type: "session",
    label: "Session Limits",
    description: "Enforce limits across an entire session. Rate limiting and tool caps.",
    icon: Timer,
    color: "text-blue-600 dark:text-blue-400",
    borderColor: "border-blue-500/40",
  },
  {
    type: "sandbox",
    label: "Sandboxes",
    description: "Boundary enforcement. Restrict file paths, commands, and domains.",
    icon: Box,
    color: "text-orange-600 dark:text-orange-400",
    borderColor: "border-orange-500/40",
  },
] as const

export function LibraryTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const search = searchParams.get("search") ?? ""
  const typeFilter = searchParams.get("type") ?? ""

  const [contracts, setContracts] = useState<LibraryContractSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchInput, setSearchInput] = useState(search)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const fetchGenRef = useRef(0)

  const fetchContracts = useCallback(async () => {
    const gen = ++fetchGenRef.current
    setError(null)
    try {
      const data = await listContracts({
        search: search || undefined,
        type: typeFilter || undefined,
      })
      if (gen !== fetchGenRef.current) return
      setContracts(data)
    } catch (e) {
      if (gen !== fetchGenRef.current) return
      setError(e instanceof Error ? e.message : "Failed to load contracts")
    } finally {
      if (gen === fetchGenRef.current) setLoading(false)
    }
  }, [search, typeFilter])

  const actions = useLibraryActions(contracts, fetchContracts)

  useEffect(() => { setLoading(true); fetchContracts() }, [fetchContracts])

  useEffect(() => {
    return subscribeDashboardSSE({
      contract_created: () => fetchContracts(),
      contract_updated: () => fetchContracts(),
    })
  }, [fetchContracts])

  // Debounced search → URL sync
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (searchInput) next.set("search", searchInput)
        else next.delete("search")
        return next
      })
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [searchInput, setSearchParams])

  // "Create from Event" context
  const [fromEvent, setFromEvent] = useState<FromEventContext | undefined>()

  useEffect(() => {
    if (searchParams.get("new") === "true") {
      const toolName = searchParams.get("from_tool")
      const verdict = searchParams.get("from_verdict")
      const stateArgs = (location.state as { fromArgs?: Record<string, unknown> } | null)?.fromArgs
      if (toolName && verdict) {
        setFromEvent({ tool_name: toolName, verdict, tool_args: stateArgs })
      }
      actions.openNewContract()
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.delete("new")
        next.delete("from_tool")
        next.delete("from_verdict")
        return next
      })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleTypeChange = (val: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (val && val !== "all") next.set("type", val)
      else next.delete("type")
      return next
    })
  }

  // Group contracts by type
  const grouped = useMemo(() => {
    const map: Record<string, LibraryContractSummary[]> = {}
    for (const c of contracts) {
      ;(map[c.type] ??= []).push(c)
    }
    return map
  }, [contracts])

  // Which sections have contracts (for filtered view)
  const activeSections = typeFilter
    ? TYPE_SECTIONS.filter((s) => s.type === typeFilter)
    : TYPE_SECTIONS.filter((s) => (grouped[s.type]?.length ?? 0) > 0)

  return (
    <div className="space-y-6">
      <AiGeneratorHero onContractCreated={fetchContracts} />

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search contracts…"
            className="pl-9"
          />
        </div>
        <Select value={typeFilter || "all"} onValueChange={handleTypeChange}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            {TYPE_OPTIONS.map((t) => (
              <SelectItem key={t} value={t}>
                {t === "all" ? "All types" : t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" onClick={() => actions.setImportOpen(true)}>
            <Upload className="size-4 mr-1.5" /> Import
          </Button>
          <Button onClick={() => actions.openNewContract()}>
            <Plus className="size-4 mr-1.5" /> New Contract
          </Button>
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertDescription>
            {error}{" "}
            <Button variant="outline" size="sm" className="ml-2" onClick={fetchContracts}>Retry</Button>
          </AlertDescription>
        </Alert>
      ) : contracts.length === 0 ? (
        <EmptyState search={search} />
      ) : (
        <div className="space-y-8">
          {activeSections.map((section) => {
            const sectionContracts = grouped[section.type] ?? []
            if (sectionContracts.length === 0) return null
            return (
              <div key={section.type}>
                <div className={`flex items-center gap-2 mb-3 pb-2 border-b ${section.borderColor}`}>
                  <section.icon className={`size-4 ${section.color}`} />
                  <h3 className={`text-sm font-semibold ${section.color}`}>{section.label}</h3>
                  <span className="text-xs text-muted-foreground">
                    {section.description}
                  </span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {sectionContracts.map((c) => (
                    <ContractCard
                      key={c.contract_id} contract={c}
                      onEdit={actions.handleEdit} onDelete={actions.handleDeleteRequest}
                      onDuplicate={actions.handleDuplicate} onClick={actions.openDetail}
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Dialogs */}
      <ContractEditorDialog
        open={actions.editorOpen} onOpenChange={(v) => { actions.setEditorOpen(v); if (!v) setFromEvent(undefined) }}
        contract={actions.editingContract} initialDefinition={actions.initialDef}
        fromEvent={fromEvent}
        onSaved={fetchContracts}
      />
      <ImportDialog open={actions.importOpen} onOpenChange={actions.setImportOpen} onImported={fetchContracts} />
      <DeleteDialog
        open={actions.deleteOpen} onOpenChange={actions.setDeleteOpen}
        contractName={actions.deleteTarget?.name ?? ""}
        onConfirm={actions.handleDeleteConfirm} deleting={actions.deleting}
        usageCount={actions.deleteUsage.length}
        usedByBundles={actions.deleteUsage.map((u) => u.composition_name)}
      />
      <ContractDetailSheet
        open={actions.detailOpen} onOpenChange={actions.setDetailOpen}
        contractId={actions.detailId} onEdit={actions.handleEdit}
        onDuplicate={actions.handleDuplicate} onDelete={actions.handleDeleteRequest}
      />
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-5 w-40" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-40 rounded-lg" />
        ))}
      </div>
    </div>
  )
}

function EmptyState({ search }: { search: string }) {
  return (
    <div className="flex h-32 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border">
      {search ? (
        <p className="text-sm text-muted-foreground">No contracts match &ldquo;{search}&rdquo;</p>
      ) : (
        <>
          <p className="text-sm font-medium text-foreground">No contracts yet</p>
          <p className="text-xs text-muted-foreground">
            Use the AI generator above, or create your first contract manually.
          </p>
        </>
      )}
    </div>
  )
}
