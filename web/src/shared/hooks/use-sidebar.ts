import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "yuubot.sidebar.collapsed";
const MOBILE_QUERY = "(max-width: 860px)";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // ignore storage failures
  }
}

export function useSidebar() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const onChange = (event: MediaQueryListEvent) => {
      setIsMobile(event.matches);
      if (!event.matches) {
        setMobileOpen(false);
      }
    };

    setIsMobile(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const toggleDesktop = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous;
      writeCollapsed(next);
      return next;
    });
  }, []);

  const toggleMobile = useCallback(() => {
    setMobileOpen((previous) => !previous);
  }, []);

  const closeMobile = useCallback(() => {
    setMobileOpen(false);
  }, []);

  const setMobile = useCallback((open: boolean) => {
    setMobileOpen(open);
  }, []);

  return {
    collapsed,
    mobileOpen,
    isMobile,
    toggleDesktop,
    toggleMobile,
    closeMobile,
    setMobile,
  };
}
