/** Minimal JSON API helper: one place that turns errors into Chinese text. */
import { humanizeError } from "./errors";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };

type ErrorBody = { error?: string; detail?: string };

async function request<T>(
  method: string,
  url: string,
  body?: unknown,
): Promise<ApiResult<T>> {
  try {
    const response = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const parsed = text ? (JSON.parse(text) as unknown) : {};
    if (!response.ok) {
      const failure = parsed as ErrorBody;
      return { ok: false, error: humanizeError(failure.error, failure.detail) };
    }
    return { ok: true, data: parsed as T };
  } catch {
    return { ok: false, error: "无法连接服务器" };
  }
}

export const apiGet = <T,>(url: string) => request<T>("GET", url);
export const apiPost = <T,>(url: string, body?: unknown) => request<T>("POST", url, body);
export const apiPatch = <T,>(url: string, body: unknown) => request<T>("PATCH", url, body);
export const apiPut = <T,>(url: string, body: unknown) => request<T>("PUT", url, body);
export const apiDelete = <T,>(url: string) => request<T>("DELETE", url);
