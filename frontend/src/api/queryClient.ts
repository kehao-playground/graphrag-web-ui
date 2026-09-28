import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { message } from "antd";

// meta.silent opts one query or mutation out of the shared error toast:
// the deliberately quiet reads (health badges, preflight, tag catalog)
// whose failure costs a decoration, not the pane, and mutations whose own
// onError shows something better (a conflict modal, an inline result).
declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: { silent?: boolean };
    mutationMeta: { silent?: boolean };
  }
}

// The app's one client config (R1-49, R1-87), shared with the component
// tests so they run under the same defaults:
// - retry: false — every error the backend sends is a coded 4xx/5xx the
//   user should see now, not after three backoff rounds;
// - one error toast per failed query/mutation, raised by the caches
//   instead of a useEffect per call site. Messages arrive localized
//   (ApiRequestError); network errors surface their own text.
// Freshness stays per key (see queries.ts): only the expensive listings
// carry a staleTime; everything else refetches on mount as before.
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (!query.meta?.silent) message.error(error.message);
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        if (!mutation.meta?.silent) message.error(error.message);
      },
    }),
  });
}
