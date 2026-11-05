import type { RunEvent } from "./types";

export type StepState = "active" | "done" | "error";

export interface Step {
  key: string;
  node: "planner" | "critic" | "verifier" | "writer" | "system";
  state: StepState;
  message: string;
  data: Record<string, unknown>;
  round?: number;
}

export interface Lane {
  lane: number;
  question: string;
  state: StepState;
  message: string;
  data: Record<string, unknown>;
}

export interface Fan {
  key: string;
  node: "fan";
  round: number;
  lanes: Lane[];
}

export type Stage = Step | Fan;

/** Fold the flat event stream into the shape of the LangGraph run: steps and parallel fan-outs. */
export function buildTimeline(events: RunEvent[]): Stage[] {
  const stages: Stage[] = [];
  const fans = new Map<number, Fan>();
  let criticRound = 0;
  const open = new Map<string, Step>();

  for (const ev of events) {
    const data = (ev.data ?? {}) as Record<string, unknown>;
    if (ev.node.startsWith("researcher:")) {
      const [, r, l] = ev.node.split(":");
      const round = Number(r);
      const laneNo = Number(l);
      let fan = fans.get(round);
      if (!fan) {
        fan = { key: `fan-${round}`, node: "fan", round, lanes: [] };
        fans.set(round, fan);
        stages.push(fan);
      }
      let lane = fan.lanes.find((x) => x.lane === laneNo);
      if (!lane) {
        lane = { lane: laneNo, question: ev.message, state: "active", message: "Searching", data: {} };
        fan.lanes.push(lane);
        fan.lanes.sort((a, b) => a.lane - b.lane);
      }
      if (ev.kind === "progress") lane.message = ev.message;
      if (ev.kind === "done") Object.assign(lane, { state: "done", message: ev.message, data });
      continue;
    }

    const node = ev.node as Step["node"];
    if (node === "system") {
      if (ev.kind === "error") stages.push({ key: `sys-${ev.seq}`, node, state: "error", message: ev.message, data });
      for (const s of open.values()) if (s.state === "active" && ev.kind === "error") s.state = "error";
      continue;
    }
    if (ev.kind === "start") {
      if (node === "critic") criticRound += 1;
      const step: Step = { key: `${node}-${ev.seq}`, node, state: "active", message: ev.message, data, round: criticRound };
      open.set(node, step);
      stages.push(step);
    } else {
      const step = open.get(node);
      if (step) Object.assign(step, { state: ev.kind === "error" ? "error" : "done", message: ev.message, data });
    }
  }
  return stages;
}

export const NODE_LABEL: Record<string, string> = {
  planner: "Planner",
  critic: "Critic",
  verifier: "Verifier",
  writer: "Writer",
  system: "Run",
};
