"use client";

import { use, useEffect, useMemo, useState } from "react";
import { getRun, markdownUrl, streamUrl } from "@/lib/api";
import { buildTimeline } from "@/lib/timeline";
import type { RunDetail, RunEvent } from "@/lib/types";
import { AgentTimeline } from "./AgentTimeline";
import { ReportView } from "./ReportView";
import { RunRecovery } from "./RunRecovery";

const isTerminal = (ev: RunEvent) => ev.node === "system" && (ev.kind === "done" || ev.kind === "error");

/** Insert or replace by seq: a continued run rewrites its old failure event into a resume marker. */
function merge(prev: RunEvent[], ev: RunEvent): RunEvent[] {
  const i = prev.findIndex((p) => p.seq === ev.seq);
  if (i === -1) return [...prev, ev].sort((a, b) => a.seq - b.seq);
  const next = prev.slice();
  next[i] = ev;
  return next;
}

export function RunView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0); // bumped by Continue to reconnect to the same run

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    getRun(id)
      .then((detail) => {
        if (cancelled) return;
        setRun(detail);
        setEvents(detail.events ?? []);
        if (detail.status === "done" || detail.status === "error") return;
        // Pending: connecting starts the run. Running: the stream replays from the start and follows.
        source = new EventSource(streamUrl(id));
        source.addEventListener("agent", (msg) => {
          const ev = JSON.parse((msg as MessageEvent).data) as RunEvent;
          setEvents((prev) => merge(prev, ev));
          if (isTerminal(ev)) {
            source?.close();
            getRun(id).then((d) => !cancelled && setRun(d));
          }
        });
        source.onerror = () => {
          // The server closes the stream when the run ends; only surface errors mid-run.
          if (source?.readyState === EventSource.CLOSED) setError("Lost the connection to the run. Reload to resume.");
        };
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load the run."));

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [id, attempt]);

  const stages = useMemo(() => buildTimeline(events), [events]);
  const last = events.at(-1);
  const failed = run?.status === "error" || (last !== undefined && isTerminal(last) && last.kind === "error");

  if (error && !run) return <p className="pt-16 text-rejected">{error}</p>;
  if (!run) return <p className="pt-16 text-ink-faint">Loading run…</p>;

  return (
    <div className="pt-8 sm:pt-12">
      <h1 className="font-serif text-[30px] sm:text-[38px] leading-tight tracking-[-0.01em] max-w-4xl">{run.topic}</h1>
      <p className="mt-2 text-sm text-ink-faint">Model: {run.model}</p>

      <div className="mt-8 grid gap-10 grid-cols-[minmax(0,1fr)] lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="lg:sticky lg:top-6 self-start min-w-0" aria-label="Agent activity">
          <AgentTimeline stages={stages} finished={run.status === "done"} />
        </aside>

        <section aria-live="polite">
          {run.report ? (
            <ReportView report={run.report} downloadHref={markdownUrl(id)} />
          ) : failed && run.status === "error" ? (
            <RunRecovery
              run={run}
              onContinued={() => {
                setError(null);
                setAttempt((n) => n + 1);
              }}
            />
          ) : (
            <Waiting stageCount={stages.length} />
          )}
          {error && run.report == null && !failed && <p className="mt-4 text-sm text-rejected">{error}</p>}
        </section>
      </div>
    </div>
  );
}

function Waiting({ stageCount }: { stageCount: number }) {
  return (
    <div className="text-ink-soft max-w-prose">
      <p className="font-serif text-[20px] text-ink">The brief appears here when the writer finishes.</p>
      <p className="mt-2 text-sm">
        {stageCount === 0
          ? "Starting the planner. Local models take a moment to load."
          : "Follow the agents on the left. Every claim is checked against its source text before it reaches the brief."}
      </p>
    </div>
  );
}
