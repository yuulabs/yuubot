import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { routeTree } from "./routeTree.gen";
import "katex/dist/katex.min.css";
import "./index.css";

const STALE_BUILD_RELOAD_KEY = "yuubot:stale-build-reload";

registerStaleBuildRecovery();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

// Register the router for type safety
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

function registerStaleBuildRecovery() {
  window.addEventListener("vite:preloadError", (event) => {
    const entryUrl = currentEntryUrl();
    if (sessionStorage.getItem(STALE_BUILD_RELOAD_KEY) === entryUrl) {
      return;
    }

    event.preventDefault();
    sessionStorage.setItem(STALE_BUILD_RELOAD_KEY, entryUrl);
    window.location.reload();
  });
}

function currentEntryUrl(): string {
  return (
    document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src ??
    window.location.href
  );
}
