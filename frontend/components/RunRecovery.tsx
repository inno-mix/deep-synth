"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { continueRun, restartRun } from "@/lib/api";
import { RESUME_LABEL } from "@/lib/timeline";
import type { RunDetail } from "@/lib/types";

/** Shown when a run fails: continue from the last completed step, or run the question again from scratch. */
export function RunRecovery({ run, onContinued }: { run: RunDetail; onContinued: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"continue" | "restart" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const from = (run.resume_from ?? []).map((n) => RESUME_LABEL[n] ?? n).join(" and ");

  async function act(kind: "continue" | "restart") {
    setBusy(kind);
    setActionError(null);
    try {
      if (kind === "continue") {
        await continueRun(run.id);
        onContinued();
      } else {
        const { id } = await restartRun(run.id);
        router.push(`/runs/${id}`);
      }
    } catch (e) {
      const what = kind === "continue" ? "continue" : "start over";
      setActionError(`Couldn't ${what}: ${e instanceof Error ? e.message : "the API didn't answer."}`);
      setBusy(null);
    }
  }

  return (
    <div className="border-l-2 border-rejected pl-4 max-w-prose">
      <p className="font-medium text-rejected">The run stopped before writing a brief.</p>
      {run.error && <p className="mt-1 text-sm text-ink-soft">{run.error}</p>}

      <div className="mt-5 flex flex-wrap gap-3">
        {run.resumable && (
          <button
            type="button"
            onClick={() => act("continue")}
            disabled={busy !== null}
            className="rounded-lg bg-signal text-white font-medium px-4 py-2 disabled:opacity-60"
          >
            {busy === "continue" ? "Continuing…" : `Continue from ${from}`}
          </button>
        )}
        <button
          type="button"
          onClick={() => act("restart")}
          disabled={busy !== null}
          className={
            run.resumable
              ? "rounded-lg border border-rule text-ink font-medium px-4 py-2 hover:border-ink-soft disabled:opacity-60"
              : "rounded-lg bg-signal text-white font-medium px-4 py-2 disabled:opacity-60"
          }
        >
          {busy === "restart" ? "Starting over…" : "Start over"}
        </button>
      </div>

      <p className="mt-3 text-sm text-ink-faint">
        {run.resumable
          ? "Continue keeps the finished steps and runs the failed one again. Start over runs the whole question again as a new brief; this run stays in your history."
          : "Nothing was saved before the failure, so the question has to run again from the start. This run stays in your history."}
      </p>
      {actionError && (
        <p role="alert" className="mt-3 text-sm text-rejected">
          {actionError}
        </p>
      )}
    </div>
  );
}
