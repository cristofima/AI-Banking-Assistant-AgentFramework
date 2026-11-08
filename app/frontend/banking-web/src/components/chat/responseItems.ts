import { disputePreviewSchema, supportCaseSchema, type DisputePreview } from "@/api/supportCaseContracts";
import type { SupportCase } from "@/models/SupportCase";

export interface ToolOutputItem {
  type: string;
  id?: string;
  call_id?: string;
  name?: string;
  output?: unknown;
  consent_link?: string;
  error?: unknown;
}

export function toolProgressKey(name: string | undefined): string | null {
  if (!name || /handoff|transfer|triage|^request_info$/i.test(name)) return null;
  if (/dispute|supportcase/i.test(name)) return "Processing support case";
  if (/transaction/i.test(name)) return "Looking up transactions";
  if (/account|balance|product|card/i.test(name)) return "Looking up account information";
  return "Processing request";
}

// FastMCP results may wrap the model JSON in a text content block.
export function readToolCase(output: unknown): SupportCase | null {
  const parse = (value: unknown): unknown => {
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch { return null; }
  };
  const value = parse(output);
  if (toolOutputFailed(value)) return null;
  const direct = supportCaseSchema.safeParse(value);
  if (direct.success) return direct.data;
  if (!value || typeof value !== "object" || !("content" in value) || !Array.isArray(value.content)) return null;
  if ("isError" in value && value.isError === true) return null;
  for (const block of value.content) {
    if (block?.type !== "text") continue;
    const result = supportCaseSchema.safeParse(parse(block.text));
    if (result.success) return result.data;
  }
  return null;
}

export function readToolPreview(output: unknown): DisputePreview | null {
  const parse = (value: unknown): unknown => {
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch { return null; }
  };
  const value = parse(output);
  if (toolOutputFailed(value)) return null;
  const direct = disputePreviewSchema.safeParse(value);
  if (direct.success) return direct.data;
  if (!value || typeof value !== "object" || !("content" in value) || !Array.isArray(value.content)) return null;
  for (const block of value.content) {
    if (block?.type !== "text") continue;
    const result = disputePreviewSchema.safeParse(parse(block.text));
    if (result.success) return result.data;
  }
  return null;
}

export function toolOutputFailed(output: unknown): boolean {
  let value = output;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return false; }
  }
  return Boolean(value && typeof value === "object" &&
    (("isError" in value && value.isError === true) || ("error" in value && value.error)));
}
