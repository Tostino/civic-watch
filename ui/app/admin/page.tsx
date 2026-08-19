import { Dashboard } from "@/components/admin/Dashboard";

/* The queues. Everything here is client-rendered behind the Gate in the
 * layout: the console is authenticated, dynamic and never indexed, so there
 * is nothing for the server to pre-render. */
export default function AdminPage() {
  return <Dashboard />;
}
