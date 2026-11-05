import type { RunDetail, RunSummary } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function createRun(topic: string): Promise<{ id: string }> {
  return json(
    await fetch(`${API_URL}/api/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic }),
    }),
  );
}

export async function listRuns(): Promise<RunSummary[]> {
  return json(await fetch(`${API_URL}/api/runs`, { cache: "no-store" }));
}

export async function getRun(id: string): Promise<RunDetail> {
  return json(await fetch(`${API_URL}/api/runs/${id}`, { cache: "no-store" }));
}

export const streamUrl = (id: string) => `${API_URL}/api/runs/${id}/stream`;
export const markdownUrl = (id: string) => `${API_URL}/api/runs/${id}/report.md`;
