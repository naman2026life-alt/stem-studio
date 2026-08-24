"use client";

import { LogIn, LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

import { createClient } from "@/lib/supabase/client";

export function AuthButton({ signedIn, prominent = false }: { signedIn: boolean; prominent?: boolean }) {
  const router = useRouter();
  async function signIn() {
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) window.alert(error.message);
  }

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.refresh();
  }

  if (!signedIn) {
    return (
      <button className={prominent ? "button-primary" : "button-ghost"} onClick={signIn} type="button">
        <LogIn size={17} /> Continue with Google
      </button>
    );
  }

  return <button className="button-ghost" onClick={signOut} type="button"><LogOut size={16} /> Sign out</button>;
}
