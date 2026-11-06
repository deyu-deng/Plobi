import { useContext } from "react";
import { ProfileContext } from "@/contexts/profile-context";

export function useAgentScope() {
  return useContext(ProfileContext);
}
