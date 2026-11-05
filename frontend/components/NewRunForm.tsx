"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createRun } from "@/lib/api";

const EXAMPLES = [
  "Compare PostgreSQL pgvector vs Qdrant performance",
  "Kafka vs Redpanda for low-latency event streaming",
  "Is SQLite viable as a production database for multi-tenant SaaS?",
];

export function NewRunForm() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (topic.trim().length < 8) {
      setError("Write at least a few words so the planner has something to split.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { id } = await createRun(topic.trim());
      router.push(`/runs/${id}`);
    } catch (err) {
      setError(err instanceof Error ? `Couldn't start the run: ${err.message}` : "Couldn't reach the API.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-10">
      <label htmlFor="topic" className="sr-only">
        Research question
      </label>
      <div className="flex flex-col sm:flex-row gap-2 bg-paper border border-rule rounded-xl p-2 focus-within:border-signal transition-colors">
        <textarea
          id="topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          rows={2}
          maxLength={300}
          placeholder="Compare PostgreSQL pgvector vs Qdrant performance"
          className="flex-1 resize-none bg-transparent px-3 py-2 text-[17px] text-ink placeholder:text-ink-faint outline-none"
        />
        <button
          type="submit"
          disabled={busy}
          className="self-stretch sm:self-end rounded-lg bg-signal text-white font-medium px-5 py-2.5 disabled:opacity-60"
        >
          {busy ? "Starting…" : "Research"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-rejected">
          {error}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <span className="text-ink-faint">Try</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setTopic(ex)}
            className="text-ink-soft underline decoration-rule underline-offset-4 hover:decoration-signal text-left"
          >
            {ex}
          </button>
        ))}
      </div>
    </form>
  );
}
