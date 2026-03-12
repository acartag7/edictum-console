import { useState, useCallback, useRef, useEffect } from "react"
import * as yaml from "js-yaml"
import { toast } from "sonner"
import { Loader2, Upload, Check } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { YamlEditor } from "@/components/yaml-editor"
import { importContracts, type ImportResult } from "@/lib/api/contracts"

interface ImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => void
}

interface ParsedPreview {
  format: "bundle" | "single" | "list"
  bundleName?: string
  count: number
  contracts: Array<{ id: string; type?: string }>
}

export function ImportDialog({ open, onOpenChange, onImported }: ImportDialogProps) {
  const [yamlContent, setYamlContent] = useState("")
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ParsedPreview | null>(null)
  const [validation, setValidation] = useState<{ valid: boolean; error?: string; line?: number }>({ valid: true })
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    if (!open) return
    setYamlContent(""); setResult(null); setError(null)
    setPreview(null); setValidation({ valid: true }); setImporting(false)
  }, [open])

  useEffect(() => () => clearTimeout(debounceRef.current), [])

  const parseContent = useCallback((val: string) => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      if (!val.trim()) { setValidation({ valid: true }); setPreview(null); return }
      try {
        const doc = yaml.load(val)

        // Format 1: Full bundle
        if (doc && typeof doc === "object" && !Array.isArray(doc)) {
          const obj = doc as Record<string, unknown>
          if (obj.kind === "ContractBundle" || (obj.apiVersion && Array.isArray(obj.contracts))) {
            const contracts = obj.contracts as Array<Record<string, unknown>>
            if (!Array.isArray(contracts) || contracts.length === 0) {
              setValidation({ valid: false, error: "Bundle has no contracts" }); setPreview(null); return
            }
            setPreview({
              format: "bundle",
              bundleName: (obj.metadata as Record<string, unknown>)?.name as string | undefined,
              count: contracts.length,
              contracts: contracts.map(c => ({ id: String(c.id ?? "unknown"), type: c.type as string | undefined })),
            })
            setValidation({ valid: true })
            return
          }

          // Format 2: Single contract (has `id` field)
          if (typeof obj.id === "string") {
            setPreview({
              format: "single",
              count: 1,
              contracts: [{ id: obj.id, type: obj.type as string | undefined }],
            })
            setValidation({ valid: true })
            return
          }

          setValidation({ valid: false, error: "Not a recognized contract format. Expected a contract with 'id' field, a list of contracts, or a ContractBundle." })
          setPreview(null)
          return
        }

        // Format 3: List of contracts
        if (Array.isArray(doc)) {
          const items = doc.filter((item): item is Record<string, unknown> =>
            item && typeof item === "object" && typeof (item as Record<string, unknown>).id === "string"
          )
          if (items.length === 0) {
            setValidation({ valid: false, error: "List contains no items with 'id' field" }); setPreview(null); return
          }
          setPreview({
            format: "list",
            count: items.length,
            contracts: items.map(c => ({ id: String(c.id), type: c.type as string | undefined })),
          })
          setValidation({ valid: true })
          return
        }

        setValidation({ valid: false, error: "Expected a YAML mapping or list" })
        setPreview(null)
      } catch (e) {
        const msg = e instanceof yaml.YAMLException ? e.message : "Invalid YAML"
        const line = e instanceof yaml.YAMLException ? (e.mark?.line ?? 0) + 1 : undefined
        setValidation({ valid: false, error: msg, line }); setPreview(null)
      }
    }, 300)
  }, [])

  const handleChange = (val: string) => {
    setYamlContent(val); setResult(null); setError(null); parseContent(val)
  }

  const handleImport = async () => {
    if (!yamlContent.trim() || !preview) return
    setImporting(true); setError(null)
    try {
      let contentToSend = yamlContent

      if (preview.format === "single") {
        const doc = yaml.load(yamlContent) as Record<string, unknown>
        const bundleWrapper = {
          apiVersion: "edictum/v1",
          kind: "ContractBundle",
          metadata: { name: `imported-${doc.id || "contract"}` },
          contracts: [doc],
        }
        contentToSend = yaml.dump(bundleWrapper, { lineWidth: -1 })
      } else if (preview.format === "list") {
        const doc = yaml.load(yamlContent) as Array<Record<string, unknown>>
        const bundleWrapper = {
          apiVersion: "edictum/v1",
          kind: "ContractBundle",
          metadata: { name: `imported-${doc.length}-contracts` },
          contracts: doc,
        }
        contentToSend = yaml.dump(bundleWrapper, { lineWidth: -1 })
      }

      const res = await importContracts(contentToSend)
      setResult(res)
      const total = res.contracts_created.length + res.contracts_updated.length
      toast.success(`Imported ${total} contract${total !== 1 ? "s" : ""}`)
      onImported()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed.")
    } finally { setImporting(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Import Contracts</DialogTitle>
          <DialogDescription>Paste a contract, a list of contracts, or a full bundle YAML.</DialogDescription>
        </DialogHeader>

        <YamlEditor value={yamlContent} onChange={handleChange} validation={validation} height="220px" placeholder="# Paste contract bundle YAML here..." />

        {preview && !result && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <Badge variant="outline" className="text-xs">
                {preview.format === "bundle" ? "Bundle" : preview.format === "single" ? "Single contract" : "Contract list"}
              </Badge>
              {preview.bundleName && (
                <span className="text-muted-foreground">{preview.bundleName}</span>
              )}
              <span className="text-zinc-600 dark:text-zinc-400">
                {preview.count} contract{preview.count !== 1 ? "s" : ""} detected
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {preview.contracts.map((c) => (
                <Badge key={c.id} variant="outline" className="text-xs gap-1">
                  {c.id}
                  {c.type && <span className="text-muted-foreground">({c.type})</span>}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {result && (
          <>
            <Separator />
            <div className="space-y-2 text-sm">
              {result.contracts_created.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <Check className="size-4 text-emerald-600 dark:text-emerald-400" />
                  <span className="text-emerald-600 dark:text-emerald-400 font-medium">Created:</span>
                  {result.contracts_created.map((id) => <Badge key={id} variant="outline" className="text-xs">{id}</Badge>)}
                </div>
              )}
              {result.contracts_updated.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <Check className="size-4 text-blue-600 dark:text-blue-400" />
                  <span className="text-blue-600 dark:text-blue-400 font-medium">Updated:</span>
                  {result.contracts_updated.map((id) => <Badge key={id} variant="outline" className="text-xs">{id}</Badge>)}
                </div>
              )}
            </div>
          </>
        )}

        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{result ? "Done" : "Cancel"}</Button>
          {!result && (
            <Button onClick={handleImport} disabled={importing || !validation.valid || !yamlContent.trim() || !preview}>
              {importing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Upload className="mr-2 size-4" />}
              Import
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
