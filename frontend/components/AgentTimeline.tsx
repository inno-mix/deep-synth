import type { Fan, Stage, Step } from "@/lib/timeline";
import { NODE_LABEL, RESUME_LABEL } from "@/lib/timeline";

export function AgentTimeline({ stages, finished }: { stages: Stage[]; finished: boolean }) {
  return (
    <ol className="track space-y-5">
      {stages.map((s) => (s.node === "fan" ? <FanOut key={s.key} fan={s} /> : <StepRow key={s.key} step={s} />))}
      {finished && (
        <li className="relative text-sm text-verified font-medium">
          <span className="node-dot" data-state="done" style={{ background: "var(--verified)", borderColor: "var(--verified)" }} />
          Brief written
        </li>
      )}
    </ol>
  );
}

function StepRow({ step }: { step: Step }) {
  const d = step.data as Record<string, unknown>;
  if (step.node === "resume") return <ResumeMarker d={d} />;
  const title =
    step.node === "critic" ? `Critic, round ${step.round}` : step.node === "system" ? "Run stopped" : NODE_LABEL[step.node];
  return (
    <li className="relative">
      <span className="node-dot" data-state={step.state} aria-hidden />
      <p className="font-medium text-ink">{title}</p>
      <p className={`text-sm ${step.state === "error" ? "text-rejected" : "text-ink-soft"}`}>{step.message}</p>
      {step.node === "critic" && step.state === "done" && <CriticDetail d={d} />}
      {step.node === "verifier" && step.state === "done" && <VerifierDetail d={d} />}
    </li>
  );
}

/** Where an earlier attempt failed and the run was continued. Rows below it are the new attempt. */
function ResumeMarker({ d }: { d: Record<string, unknown> }) {
  const from = ((d.resume_from as string[]) ?? []).map((n) => RESUME_LABEL[n] ?? n);
  return (
    <li className="relative resume-marker">
      <span className="node-dot" data-state="resume" aria-hidden />
      <p className="font-medium text-ink">Continued{from.length > 0 && ` from ${from.join(" and ")}`}</p>
      {typeof d.error === "string" && <p className="text-sm text-ink-faint">Earlier attempt stopped: {d.error}</p>}
    </li>
  );
}

function CriticDetail({ d }: { d: Record<string, unknown> }) {
  const gaps = (d.gaps as string[]) ?? [];
  const contradictions = (d.contradictions as string[]) ?? [];
  if (!gaps.length && !contradictions.length) return null;
  return (
    <details className="mt-1 text-sm text-ink-soft">
      <summary className="cursor-pointer text-ink-faint hover:text-ink">
        {gaps.length} gaps, {contradictions.length} contradictions
      </summary>
      <ul className="mt-1 space-y-1 list-disc pl-4">
        {gaps.map((g) => (
          <li key={g}>{g}</li>
        ))}
        {contradictions.map((c) => (
          <li key={c} className="text-rejected">
            {c}
          </li>
        ))}
      </ul>
    </details>
  );
}

function VerifierDetail({ d }: { d: Record<string, unknown> }) {
  const conf = (d.confidence as Record<string, number>) ?? {};
  const kept = Number(d.kept ?? 0);
  const dropped = Number(d.dropped ?? 0);
  const total = Math.max(kept + dropped, 1);
  return (
    <div className="mt-2" aria-label={`${kept} kept, ${dropped} rejected`}>
      <div className="flex h-1.5 w-full max-w-[220px] overflow-hidden rounded-full bg-rule">
        <span style={{ width: `${(kept / total) * 100}%` }} className="bg-verified" />
        <span style={{ width: `${(dropped / total) * 100}%` }} className="bg-rejected" />
      </div>
      <p className="mt-1 text-xs text-ink-faint">
        {conf.high ?? 0} exact quotes, {conf.medium ?? 0} close, {conf.low ?? 0} loose
      </p>
    </div>
  );
}

function FanOut({ fan }: { fan: Fan }) {
  const active = fan.lanes.some((l) => l.state === "active");
  return (
    <li className="relative">
      <span className="node-dot" data-state={active ? "active" : "done"} aria-hidden />
      <p className="font-medium text-ink">
        {fan.round === 0 ? "Researchers" : "Follow-up research"}
        {fan.retry && ", retried"}
        <span className="text-ink-faint font-normal"> ({fan.lanes.length} in parallel)</span>
      </p>
      <ul className="lanes mt-2 space-y-3">
        {fan.lanes.map((l) => {
          const sources = (l.data.sources as { domain: string; url: string }[]) ?? [];
          return (
            <li key={l.lane} className="lane" data-state={l.state}>
              <span className="lane-dot" aria-hidden />
              <p className="text-sm text-ink leading-snug">{l.question}</p>
              <p className="text-xs text-ink-faint mt-0.5">{l.message}</p>
              {sources.length > 0 && (
                <p className="text-xs mt-0.5 truncate">
                  {sources.map((s, i) => (
                    <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="text-ink-soft hover:text-signal">
                      {i > 0 && ", "}
                      {s.domain}
                    </a>
                  ))}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </li>
  );
}
