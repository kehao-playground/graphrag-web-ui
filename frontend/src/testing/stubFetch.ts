import { beforeEach, vi } from "vitest";
import { useAuth } from "../stores/auth";

type FetchDouble = (path: string, init?: RequestInit) => Promise<Response> | Response;

// Component tests run the REAL api client (api/apiJson/sendOk) and put the
// test double at the fetch boundary: api() calls fetch(path, init) with the
// JSON and auth headers merged into init, so a (path, init) → Response
// double serves every helper. Registered per test (after the file's own
// beforeEach, which may set up the auth store); a local-mode store with no
// token gets one so api() never reaches for a refresh.
export function stubFetch(double: FetchDouble): void {
  beforeEach(() => {
    vi.stubGlobal("fetch", double);
    const { authMode, accessToken } = useAuth.getState();
    if (authMode !== "proxy" && !accessToken) useAuth.setState({ accessToken: "test-token" });
  });
}
