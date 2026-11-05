"use client";

import { use, useEffect, useMemo, useState } from "react";
import { getRun, markdownUrl, streamUrl } from "@/lib/api";
import { buildTimeline } from "@/lib/timeline";
import type { RunDetail, RunEvent } from "@/lib/types";
import { AgentTimeline } from "./AgentTimeline";
import { ReportView } from "./ReportView";

export function RunView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    getRun(id)
      .then((detail) => {
        if (cancelled) return;
        setRun(detail);
        if (detail.status === "done" || detail.status === "error") {
          setEvents(detail.events ?? []);
          return;
        }
        // Pending: this connection executes the graph. Running: it replays and follows.
        source = new EventSource(streamUrl(id));
        source.addEventListener("agent", (msg) => {
          const ev = JSON.parse((msg as MessageEvent).data) as RunEvent;
          setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev]));
          if (ev.node === "system") {
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
  }, [id]);

  const stages = useMemo(() => buildTimeline(events), [events]);
  const failed = run?.status === "error" || events.some((e) => e.node === "system" && e.kind === "error");

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
          ) : failed ? (
            <div className="border-l-2 border-rejected pl-4">
              <p className="font-medium text-rejected">The run stopped before writing a brief.</p>
              <p className="mt-1 text-sm text-ink-soft">{run.error ?? events.at(-1)?.message}</p>
              <p className="mt-3 text-sm text-ink-soft">
                Check that the model server is running, then start the question again from the home page.
              </p>
            </div>
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
