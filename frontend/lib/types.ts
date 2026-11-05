// Types are generated from the FastAPI OpenAPI schema: `pnpm gen:types` (backend must be importable).
import type { components } from "./api-schema";

type S = components["schemas"];
export type RunEvent = S["RunEvent"];
export type RunSummary = S["RunSummary"];
export type RunDetail = S["RunDetail"];
export type RunStatus = RunSummary["status"];
export type Report = S["Report"];
export type Citation = S["Citation"];
