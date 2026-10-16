import { FakeRunRequest, RunAccepted, runAcceptedSchema } from "@/contracts/runs";

import { API_URL } from "./config";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** POST /api/v1/runs/fake. The response is validated against the contract, not trusted. */
export async function startFakeRun(request: FakeRunRequest): Promise<RunAccepted> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/runs/fake`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch {
    throw new ApiError(0, `The API at ${API_URL} is not reachable. Is it running on the laptop?`);
  }
  if (response.status !== 202) {
    throw new ApiError(response.status, `Could not start the run (HTTP ${response.status}).`);
  }
  return runAcceptedSchema.parse(await response.json());
}
