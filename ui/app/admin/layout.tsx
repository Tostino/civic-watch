import type { Metadata } from "next";
import Link from "next/link";

import { Gate } from "@/components/admin/Gate";
import s from "./admin.module.css";

/* The curation shell: its own chrome, visibly not the reading
 * surface, reached only by knowing the URL. The public header returns null
 * under /admin, and nothing public links here. */
export const metadata: Metadata = {
  title: { default: "Curation", template: "%s · Curation" },
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={s.shell}>
      <header className={s.bar}>
        <Link href="/admin" className={s.brand}>
          <span className={s.mark} aria-hidden />
          Curation console
        </Link>
        <nav className={s.nav} aria-label="Console">
          <Link href="/admin">Queues</Link>
          <Link href="/admin/redactions">Addresses</Link>
          <Link href="/admin/ops">Operations</Link>
        </nav>
        <p className={s.who}>
          Changes made here apply at once. They outrank every inferred name and survive every
          rebuild.
        </p>
        <Link href="/" className={s.exit}>
          ← Reading site
        </Link>
      </header>
      <Gate>{children}</Gate>
    </div>
  );
}
