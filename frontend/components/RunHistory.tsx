"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listRuns } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

const STATUS: Record<RunSummary["status"], { label: string; cls: string }> = {
  pending: { label: "Queued", cls: "text-ink-faint" },
  running: { label: "Running", cls: "text-signal" },
  done: { label: "Ready", cls: "text-verified" },
  error: { label: "Failed", cls: "text-rejected" },
};

export function RunHistory() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch(() => setOffline(true));
  }, []);

  if (offline)
    return (
      <p className="mt-16 text-sm text-ink-soft">
        The API isn’t answering. Start it with <code className="text-ink">make backend</code> and reload.
      </p>
    );
  if (!runs || runs.length === 0) return null;

  return (
    <section className="mt-16" aria-labelledby="history">
      <h2 id="history" className="text-sm font-medium text-ink-soft mb-2">
        Earlier briefs
      </h2>
      <ul className="border-t border-rule">
        {runs.map((r) => (
          <li key={r.id} className="border-b border-rule">
            <Link href={`/runs/${r.id}`} className="flex items-baseline gap-4 py-3 group">
              <span className="flex-1 text-ink group-hover:text-signal">{r.topic}</span>
              <span className={`text-sm ${STATUS[r.status].cls}`}>{STATUS[r.status].label}</span>
              <time className="text-sm text-ink-faint hidden sm:block w-24 text-right" dateTime={r.created_at}>
                {new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </time>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
