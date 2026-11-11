import { cn } from '@/lib/utils'

interface SuggestionChipsProps {
  suggestions: string[]
  onSelect: (suggestion: string) => void
  maxLength?: number
  className?: string
}

export function SuggestionChips({
  suggestions,
  onSelect,
  maxLength = 60,
  className,
}: SuggestionChipsProps) {
  return (
    <div className={cn("mb-2 flex gap-2 flex-wrap", className)}>
      {suggestions.map((suggestion, index) => (
        <button
          key={index}
          data-testid={`button-suggestion-${index}`}
          onClick={() => onSelect(suggestion)}
          className="stagger-enter rounded-full bg-secondary/80 px-3.5 py-1.5 text-[13px] font-normal text-muted-foreground transition-all duration-200 cursor-pointer border border-border/50 hover:border-primary/30 hover:bg-primary/5 hover:text-foreground hover:shadow-sm active:scale-[0.97]"
          style={{ animationDelay: `${index * 60}ms` }}
        >
          {suggestion.length > maxLength
            ? `${suggestion.slice(0, maxLength)}...`
            : suggestion}
        </button>
      ))}
    </div>
  )
}
