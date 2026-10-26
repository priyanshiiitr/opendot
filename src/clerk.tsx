import { ClerkProvider, useAuth } from "@clerk/react";
import React, { useEffect, type ReactNode } from "react";
import { setClerkTokenGetter } from "./api";

const PUBLISHABLE_KEY = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined)?.trim() || "";

function ClerkTokenBridge({ children }: { children: ReactNode }) {
  const { getToken, isSignedIn } = useAuth();

  useEffect(() => {
    if (!isSignedIn) {
      setClerkTokenGetter(null);
      return;
    }
    setClerkTokenGetter(async () => {
      try {
        return (await getToken()) || null;
      } catch {
        return null;
      }
    });
    return () => setClerkTokenGetter(null);
  }, [getToken, isSignedIn]);

  return <>{children}</>;
}

/** Wraps the app with Clerk when VITE_CLERK_PUBLISHABLE_KEY is set; otherwise passthrough. */
export function MaybeClerkProvider({ children }: { children: ReactNode }) {
  if (!PUBLISHABLE_KEY) {
    return <>{children}</>;
  }
  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY}>
      <ClerkTokenBridge>{children}</ClerkTokenBridge>
    </ClerkProvider>
  );
}

export function clerkConfigured(): boolean {
  return Boolean(PUBLISHABLE_KEY);
}
