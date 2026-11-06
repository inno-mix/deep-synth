import type { RunEvent } from "./types";

export type StepState = "active" | "done" | "error";

export interface Step {
  key: string;
  node: "planner" | "critic" | "verifier" | "writer" | "system" | "resume";
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
  retry: boolean; // this round was started again after a failure
  lanes: Lane[];
}

export type Stage = Step | Fan;

/**
 * Fold the flat event stream into the shape of the LangGraph run: steps and parallel fan-outs.
 *
 * A "resume" event splits the history into attempts. Work after it is drawn as new rows below the
 * marker, and anything left unfinished before it is marked failed. A "start" for a step that is
 * still active within the same attempt is an automatic retry and updates that row in place.
 */
export function buildTimeline(events: RunEvent[]): Stage[] {
  const stages: Stage[] = [];
  let attempt = 0;
  const fans = new Map<string, Fan>(); // `${attempt}:${round}`
  const roundsSeen = new Set<number>();
  const open = new Map<string, Step>(); // current attempt only
  let criticRound = 0;
  let lastCritic: Step | undefined;

  const failUnfinished = () => {
    for (const s of stages) {
      if (s.node === "fan") {
        for (const l of s.lanes) {
          if (l.state === "active") Object.assign(l, { state: "error", message: "Stopped before finishing" });
        }
      } else if (s.state === "active") {
        Object.assign(s, { state: "error", message: "Stopped before finishing" });
      }
    }
  };

  for (const ev of events) {
    const data = (ev.data ?? {}) as Record<string, unknown>;

    if (ev.node.startsWith("researcher:")) {
      const [, r, l] = ev.node.split(":");
      const round = Number(r);
      const laneNo = Number(l);
      const key = `${attempt}:${round}`;
      let fan = fans.get(key);
      if (!fan) {
        fan = { key: `fan-${key}`, node: "fan", round, retry: roundsSeen.has(round), lanes: [] };
        fans.set(key, fan);
        roundsSeen.add(round);
        stages.push(fan);
      }
      let lane = fan.lanes.find((x) => x.lane === laneNo);
      if (!lane) {
        lane = { lane: laneNo, question: ev.message, state: "active", message: "Searching", data: {} };
        fan.lanes.push(lane);
        fan.lanes.sort((a, b) => a.lane - b.lane);
      } else if (ev.kind === "start") {
        Object.assign(lane, { state: "active", message: "Retrying", data: {} }); // automatic retry
      }
      if (ev.kind === "progress") lane.message = ev.message;
      if (ev.kind === "done") Object.assign(lane, { state: "done", message: ev.message, data });
      continue;
    }

    if (ev.node === "system") {
      if (ev.kind === "resume") {
        failUnfinished();
        stages.push({ key: `resume-${ev.seq}`, node: "resume", state: "done", message: ev.message, data });
        attempt += 1;
        open.clear();
      } else if (ev.kind === "error") {
        failUnfinished();
        stages.push({ key: `sys-${ev.seq}`, node: "system", state: "error", message: ev.message, data });
      }
      continue;
    }

    const node = ev.node as Step["node"];
    const current = open.get(node);
    if (ev.kind === "start") {
      if (current && current.state === "active") {
        current.message = ev.message; // automatic retry of the same step
        continue;
      }
      if (node === "critic") {
        // A critic round that never finished is the same round when it runs again.
        if (!(lastCritic && lastCritic.state !== "done")) criticRound += 1;
      }
      const step: Step = { key: `${node}-${ev.seq}`, node, state: "active", message: ev.message, data, round: criticRound };
      if (node === "critic") lastCritic = step;
      open.set(node, step);
      stages.push(step);
    } else if (current) {
      Object.assign(current, { state: ev.kind === "error" ? "error" : "done", message: ev.message, data });
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
  resume: "Continued",
};

/** How a step reads in "Continue from …". Keys are LangGraph node names. */
export const RESUME_LABEL: Record<string, string> = {
  planner: "planning",
  researcher: "research",
  critic: "the review",
  verifier: "verification",
  writer: "writing",
};
