import { Typography } from "@nous-research/ui/ui/components/typography/index";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { getBranding } from "@/lib/branding";

export function SidebarFooter({ status }: SidebarFooterProps) {
  // Org label + link come from injected branding config (see @/lib/branding),
  // so they change via server config without a rebuild and never clash on merge.
  const { org, orgUrl } = getBranding();

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between gap-2",
        "px-5 py-2.5",
        "border-t border-current/10",
      )}
    >
      <Typography
        className="font-mono-ui text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase"
      >
        {status?.version != null ? `v${status.version}` : "—"}
      </Typography>

      <a
        href={orgUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(
          "font-sans text-display text-xs tracking-[0.12em] text-midground",
          "transition-opacity hover:opacity-90",
          "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
        )}
      >
        {org}
      </a>
    </div>
  );
}

interface SidebarFooterProps {
  status: StatusResponse | null;
}
