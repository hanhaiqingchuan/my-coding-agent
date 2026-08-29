import { ApiError } from "../../api/client";
import type {
  CampaignDetailDto,
  CampaignSummaryDto,
  EvaluationReader,
  RunDetailDto,
} from "./types";

/**
 * The read-only evaluation client. It mirrors `ApiClient`'s error handling but
 * attaches no CSRF token: the evaluation endpoints are reads, and Task 13's
 * ruling keeps the token for state-changing requests only.
 */
export class EvaluationClient implements EvaluationReader {
  async listCampaigns(): Promise<CampaignSummaryDto[]> {
    return this.request<CampaignSummaryDto[]>("/api/evaluations");
  }

  async campaignDetail(campaign: string): Promise<CampaignDetailDto> {
    return this.request<CampaignDetailDto>(
      `/api/evaluations/${encodeURIComponent(campaign)}`,
    );
  }

  async runDetail(
    campaign: string,
    taskId: string,
    repeat: number,
  ): Promise<RunDetailDto> {
    return this.request<RunDetailDto>(
      `/api/evaluations/${encodeURIComponent(campaign)}/runs/${encodeURIComponent(taskId)}/${repeat}`,
    );
  }

  private async request<T>(path: string): Promise<T> {
    const response = await fetch(path, {
      method: "GET",
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        `API request failed with status ${response.status}`,
      );
    }
    return (await response.json()) as T;
  }
}
