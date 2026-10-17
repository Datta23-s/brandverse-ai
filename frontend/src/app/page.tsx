import { AuthGate } from "@/components/auth/AuthGate";
import { Workspace } from "@/components/workspace/Workspace";

export default function HomePage() {
  return <AuthGate>
      <Workspace />
    </AuthGate>;
}
