import { useState } from "react"
import yaml from "js-yaml"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Pencil, Copy, Trash2, ClipboardCopy, Package, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getContract, type LibraryContractSummary } from "@/lib/api/contracts"
import { CONTRACT_TYPE_COLORS } from "@/lib/contract-colors"

interface ContractCardProps {
  contract: LibraryContractSummary
  onEdit: (contractId: string) => void
  onDelete: (contractId: string) => void
  onDuplicate: (contractId: string) => void
  onClick: (contractId: string) => void
}

const TYPE_BORDER: Record<string, string> = {
  pre: "border-l-amber-500",
  post: "border-l-emerald-500",
  session: "border-l-blue-500",
  sandbox: "border-l-orange-500",
}

export function ContractCard({
  contract, onEdit, onDelete, onDuplicate, onClick,
}: ContractCardProps) {
  const [copying, setCopying] = useState(false)
  const typeColor =
    CONTRACT_TYPE_COLORS[contract.type] ??
    "bg-gray-500/15 text-gray-600 dark:text-gray-400 border-gray-500/30"
  const borderColor = TYPE_BORDER[contract.type] ?? "border-l-gray-500"

  const handleCopyYaml = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setCopying(true)
    try {
      const full = await getContract(contract.contract_id)
      const yamlStr = yaml.dump(full.definition, { lineWidth: 80, noRefs: true })
      await navigator.clipboard.writeText(yamlStr)
      toast.success("YAML copied to clipboard")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to copy")
    } finally {
      setCopying(false)
    }
  }

  return (
    <Card
      className={`group cursor-pointer border-l-4 ${borderColor} py-4 transition-all hover:border-l-4 hover:shadow-md hover:shadow-black/5 dark:hover:shadow-black/20`}
      onClick={() => onClick(contract.contract_id)}
    >
      <CardContent className="space-y-3 px-4">
        {/* Header: name + actions */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">
              {contract.name}
            </p>
            <p className="truncate text-[11px] font-mono text-muted-foreground/70">
              {contract.contract_id}
            </p>
          </div>
          <div
            className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100"
            onClick={(e) => e.stopPropagation()}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-xs" onClick={() => onEdit(contract.contract_id)}>
                  <Pencil />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-xs" onClick={() => onDuplicate(contract.contract_id)}>
                  <Copy />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Duplicate</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive"
                  onClick={() => onDelete(contract.contract_id)}>
                  <Trash2 />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {/* Description */}
        <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground min-h-[2lh]">
          {contract.description || "No description"}
        </p>

        {/* Tags */}
        {contract.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {contract.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
                {tag}
              </Badge>
            ))}
          </div>
        )}

        {/* Footer: type + version + usage + copy */}
        <div className="flex items-center gap-1.5 pt-1">
          <Badge className={`text-[10px] ${typeColor}`}>{contract.type}</Badge>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">v{contract.version}</Badge>
          {contract.usage_count > 0 && (
            <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground ml-1">
              <Package className="size-2.5" />
              {contract.usage_count}
            </span>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost" size="icon-xs"
                className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={handleCopyYaml}
                disabled={copying}
              >
                {copying ? <Loader2 className="size-3 animate-spin" /> : <ClipboardCopy className="size-3" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy YAML</TooltipContent>
          </Tooltip>
        </div>
      </CardContent>
    </Card>
  )
}
