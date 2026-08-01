import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { api } from "@/api";
import { Loader2, BookOpen } from "lucide-react";

/**
 * Right-side drawer that renders the user manual (docs/USER_MANUAL.md).
 * Lazy-loads on first open; caches the content for the session so repeat
 * opens are instant.
 */
export default function HelpDrawer({ open, onOpenChange }) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || content || loading) return;
    setLoading(true);
    setError("");
    api.get("/docs/user-manual")
      .then((r) => setContent(r.data.content || ""))
      .catch((e) => setError(e.response?.data?.detail || "Could not load user manual"))
      .finally(() => setLoading(false));
  }, [open, content, loading]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-2xl p-0 flex flex-col bg-white border-l border-[#E2E8F0]"
        data-testid="help-drawer"
      >
        <SheetHeader className="px-8 pt-8 pb-5 border-b border-[#E2E8F0] bg-[#F4F7F9]">
          <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] flex items-center gap-2">
            <BookOpen className="w-3.5 h-3.5" /> Help
          </div>
          <SheetTitle className="font-serif text-2xl text-[#00317A] mt-1">
            User Manual
          </SheetTitle>
          <SheetDescription className="text-sm text-[#4B5563]">
            Everything you can do in Notarium — search this drawer any time with Ctrl / ⌘ + F.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-8 py-6" data-testid="help-drawer-body">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-[#4B5563] py-10">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading manual…
            </div>
          )}
          {error && !loading && (
            <div className="text-sm text-[#991B1B] bg-[#FDECEC] border border-[#F4B4B4] rounded-sm px-4 py-3">
              {error}
            </div>
          )}
          {!loading && !error && content && (
            <article className="notarium-help-md">
              <ReactMarkdown>{content}</ReactMarkdown>
            </article>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
