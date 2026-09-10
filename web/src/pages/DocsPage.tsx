import { useLayoutEffect } from "react";
import { Download, ExternalLink, FileText } from "lucide-react";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";
import { getBranding } from "@/lib/branding";
import { PluginSlot } from "@/plugins";

// Kept for the iframe fallback (branding.docs.mode === "iframe").
export const HERMES_DOCS_URL = "https://hermes-agent.nousresearch.com/docs/";

const DS_BUTTON_OUTLINED_LINK_CN = cn(
  "group relative inline-grid grid-cols-[auto_1fr_auto] items-center",
  "px-[.9em_.75em] py-[1.25em] gap-2",
  "leading-0 font-bold tracking-[0.2em] uppercase",
  "text-midground bg-transparent shadow-midground",
  "shadow-[inset_-1px_-1px_0_0_#00000080,inset_1px_1px_0_0_#ffffff80]",
);

export default function DocsPage() {
  const { t } = useI18n();
  const { setEnd } = usePageHeader();
  const docs = getBranding().docs;
  const pdfMode = docs.mode === "pdf";
  const docsHref = pdfMode ? docs.url : HERMES_DOCS_URL;

  useLayoutEffect(() => {
    setEnd(
      <a
        href={docsHref}
        target="_blank"
        rel="noopener noreferrer"
        className={DS_BUTTON_OUTLINED_LINK_CN}
      >
        {pdfMode ? (
          <Download className="size-3.5" />
        ) : (
          <ExternalLink className="size-3.5" />
        )}
        {pdfMode ? "Download PDF" : t.app.openDocumentation}
      </a>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t, docsHref, pdfMode]);

  // PDF mode: a native-styled download card served by /api/numbers/docs.pdf.
  // This is a Numbers-owned page (branding-config driven), so it never sends
  // the user to the external Hermes docs site.
  if (pdfMode) {
    return (
      <div
        className={cn(
          "flex min-h-0 w-full min-w-0 flex-1 flex-col",
          "pt-1 sm:pt-2",
        )}
      >
        <PluginSlot name="docs:top" />
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-start gap-2">
                <FileText className="h-4 w-4 mt-0.5 text-muted-foreground" />
                <div className="flex flex-col">
                  <span className="text-sm font-medium">
                    {t.app.nav.documentation}
                  </span>
                  <span className="text-xs text-muted-foreground max-w-prose">
                    Download the Numbers documentation (PDF).
                  </span>
                </div>
              </div>
              <a
                href={docs.url}
                target="_blank"
                rel="noopener noreferrer"
                className={DS_BUTTON_OUTLINED_LINK_CN}
              >
                <Download className="size-3.5" />
                Download PDF
              </a>
            </div>
          </CardContent>
        </Card>
        <PluginSlot name="docs:bottom" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex min-h-0 w-full min-w-0 flex-1 flex-col",
        "pt-1 sm:pt-2",
      )}
    >
      <PluginSlot name="docs:top" />
      <iframe
        title={t.app.nav.documentation}
        src={HERMES_DOCS_URL}
        className={cn(
          "min-h-0 w-full min-w-0 flex-1",
          "rounded-sm border border-current/20",
          // Docusaurus paints over a transparent <html> / <body> and relies on
          // the browser's canvas color to fill the viewport. Force a light
          // color scheme + white background so the docs render cleanly
          // regardless of the active dashboard theme.
          "[color-scheme:light] bg-white",
        )}
        sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
        referrerPolicy="no-referrer-when-downgrade"
      />
      <PluginSlot name="docs:bottom" />
    </div>
  );
}
