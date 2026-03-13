/**
 * AI contract generator — hero section at top of contracts catalog.
 * Prompt input with suggestion chips. Opens AI chat panel on submit.
 */

import { useState } from "react"
import { Sparkles, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent } from "@/components/ui/sheet"
import { AiChatPanel } from "./ai-chat-panel"

const SUGGESTIONS = [
  "Block reading .env and credential files",
  "Rate limit to 30 tool calls per session",
  "Require approval for database writes",
  "Sandbox file access to /app/workspace",
  "Detect PII in tool output",
  "Block destructive bash commands",
]

interface AiGeneratorHeroProps {
  onContractCreated: () => void
}

export function AiGeneratorHero({ onContractCreated }: AiGeneratorHeroProps) {
  const [input, setInput] = useState("")
  const [chatOpen, setChatOpen] = useState(false)
  const [initialMessage, setInitialMessage] = useState<string | undefined>()

  const handleSubmit = (text: string) => {
    if (!text.trim()) return
    setInitialMessage(text.trim())
    setChatOpen(true)
    setInput("")
  }

  const handleApplyYaml = (_yaml: string) => {
    // User applied YAML from AI — contract was created/saved
    onContractCreated()
  }

  return (
    <>
      <div className="rounded-xl border border-violet-500/20 bg-gradient-to-r from-violet-500/5 via-transparent to-violet-500/5 p-6">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles className="size-5 text-violet-600 dark:text-violet-400" />
          <h3 className="text-base font-medium">What contract do you need?</h3>
        </div>

        <form
          className="flex gap-2 mb-4"
          onSubmit={(e) => { e.preventDefault(); handleSubmit(input) }}
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Describe the rule you want to enforce..."
            className="flex-1"
          />
          <Button type="submit" disabled={!input.trim()}>
            <Send className="size-4 mr-1.5" /> Generate
          </Button>
        </form>

        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <Button
              key={s}
              type="button"
              variant="outline"
              size="sm"
              onClick={() => handleSubmit(s)}
              className="rounded-full text-xs text-muted-foreground"
            >
              {s}
            </Button>
          ))}
        </div>
      </div>

      <Sheet open={chatOpen} onOpenChange={(open) => { setChatOpen(open); if (!open) setInitialMessage(undefined) }}>
        <SheetContent side="right" className="w-full sm:max-w-md p-0 flex flex-col">
          <AiChatPanel
            onApplyYaml={handleApplyYaml}
            initialMessage={initialMessage}
            standalone
          />
        </SheetContent>
      </Sheet>
    </>
  )
}
