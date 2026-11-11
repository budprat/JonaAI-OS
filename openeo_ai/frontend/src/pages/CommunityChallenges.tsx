import React, { useState } from "react";
import { challenges } from "@/lib/geo/mock-community";
import { Trophy, Users, FileText, Clock, ChevronDown, ChevronUp } from "lucide-react";
import { Progress } from "@/components/ui/progress";

const categories = ["All", "deforestation", "agriculture", "urban", "climate"] as const;
const statuses = ["All", "active", "upcoming", "completed"] as const;

const CommunityChallenges: React.FC = () => {
  const [categoryFilter, setCategoryFilter] = useState<string>("All");
  const [statusFilter, setStatusFilter] = useState<string>("All");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [joinedIds, setJoinedIds] = useState<Set<string>>(new Set());

  const filtered = challenges
    .filter((c) => categoryFilter === "All" || c.category === categoryFilter)
    .filter((c) => statusFilter === "All" || c.status === statusFilter);

  const daysUntil = (deadline: string) => {
    const diff = new Date(deadline).getTime() - Date.now();
    return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Filter Bar */}
      <div className="flex flex-shrink-0 items-center gap-4 border-b border-border px-6 py-3">
        <span className="mr-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
          Category:
        </span>
        {categories.map((c) => (
          <button
            key={c}
            onClick={() => setCategoryFilter(c)}
            className={`rounded-md px-3 py-1 text-xs capitalize transition-colors ${
              categoryFilter === c
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent"
            }`}
          >
            {c}
          </button>
        ))}
        <div className="mx-2 h-4 w-px bg-border" />
        <span className="mr-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
          Status:
        </span>
        {statuses.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-md px-3 py-1 text-xs capitalize transition-colors ${
              statusFilter === s
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Challenge Grid */}
      <div className="flex-1 overflow-auto p-6">
        <div className="grid grid-cols-2 gap-4">
          {filtered.map((challenge) => {
            const isExpanded = expandedId === challenge.id;
            const isJoined = joinedIds.has(challenge.id);
            const days = daysUntil(challenge.deadline);

            return (
              <div key={challenge.id} className="border border-border">
                <div className="p-4">
                  <div className="mb-2 flex items-start justify-between">
                    <div className="flex-1">
                      <div className="mb-1 flex items-center gap-2">
                        <span
                          className={`px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                            challenge.status === "active"
                              ? "bg-status-good/10 text-status-good"
                              : challenge.status === "upcoming"
                                ? "bg-status-warn/10 text-status-warn"
                                : "bg-muted text-muted-foreground"
                          }`}
                        >
                          {challenge.status}
                        </span>
                        <span className="font-mono text-[10px] uppercase capitalize text-muted-foreground">
                          {challenge.category}
                        </span>
                      </div>
                      <h3 className="text-sm font-semibold leading-tight">{challenge.title}</h3>
                    </div>
                  </div>

                  <p className="mb-3 line-clamp-2 text-xs text-muted-foreground">
                    {challenge.description}
                  </p>

                  <div className="mb-3 flex items-center gap-4">
                    <div className="flex items-center gap-1">
                      <Users className="h-3 w-3 text-muted-foreground" />
                      <span className="font-mono text-xs">{challenge.participants}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <FileText className="h-3 w-3 text-muted-foreground" />
                      <span className="font-mono text-xs">{challenge.submissions} submissions</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Clock className="h-3 w-3 text-muted-foreground" />
                      <span className="font-mono text-xs">{days}d left</span>
                    </div>
                  </div>

                  <div className="mb-3">
                    <div className="mb-1 flex justify-between">
                      <span className="font-mono text-[10px] text-muted-foreground">Progress</span>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {challenge.progress}%
                      </span>
                    </div>
                    <Progress value={challenge.progress} className="h-1" />
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <Trophy className="text-status-warn h-3 w-3" />
                      <span className="font-mono text-xs">{challenge.prize}</span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setExpandedId(isExpanded ? null : challenge.id)}
                        className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent"
                      >
                        {isExpanded ? (
                          <ChevronUp className="h-3 w-3" />
                        ) : (
                          <ChevronDown className="h-3 w-3" />
                        )}
                      </button>
                      <button
                        onClick={() => {
                          const next = new Set(joinedIds);
                          if (isJoined) next.delete(challenge.id);
                          else next.add(challenge.id);
                          setJoinedIds(next);
                        }}
                        className={`rounded-md px-3 py-1 text-xs transition-colors ${
                          isJoined
                            ? "bg-status-good text-primary-foreground"
                            : "bg-primary text-primary-foreground hover:bg-primary/90"
                        }`}
                      >
                        {isJoined ? "Joined" : "Join"}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Expanded Leaderboard */}
                {isExpanded && challenge.leaderboard.length > 0 && (
                  <div className="border-t border-border">
                    <div className="bg-muted/30 px-4 py-2">
                      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                        Leaderboard
                      </span>
                    </div>
                    <table className="w-full">
                      <tbody>
                        {challenge.leaderboard.map((entry) => (
                          <tr key={entry.rank} className="border-b border-border last:border-b-0">
                            <td className="w-8 px-4 py-1.5 font-mono text-xs">{entry.rank}</td>
                            <td className="py-1.5 text-sm">{entry.username}</td>
                            <td className="text-status-good py-1.5 text-right font-mono text-xs">
                              {entry.score}
                            </td>
                            <td className="px-4 py-1.5 text-right font-mono text-xs text-muted-foreground">
                              {entry.submissions} sub
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default CommunityChallenges;
