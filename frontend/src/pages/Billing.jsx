import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CreditCard, ShieldCheck, Sparkles, ArrowUpRight, Loader2, CalendarDays } from "lucide-react";
import { toast } from "sonner";

const statusMeta = {
  active:   { label: "Active",   cls: "bg-[#E8F5EC] text-[#146334] border-[#B7E0C1]" },
  trialing: { label: "Trial",    cls: "bg-[#FEF9EC] text-[#7A5B00] border-[#F0D28A]" },
  expired:  { label: "Expired",  cls: "bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]" },
  canceled: { label: "Canceled", cls: "bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]" },
  past_due: { label: "Past Due", cls: "bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]" },
};

export default function Billing() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);   // lookup_key currently checking out
  const [portalBusy, setPortalBusy] = useState(false);
  const [params, setParams] = useSearchParams();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/status");
      setState(data);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not load billing");
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll session status after returning from Stripe
  useEffect(() => {
    const sessionId = params.get("session_id");
    if (params.get("canceled")) {
      toast.info("Checkout canceled — you can try again anytime.");
      params.delete("canceled");
      setParams(params, { replace: true });
    }
    if (!sessionId) { load(); return; }
    let cancelled = false;
    const start = Date.now();
    const poll = async () => {
      try {
        const { data } = await api.get(`/billing/session/${sessionId}`);
        if (data.payment_status === "paid") {
          toast.success("Subscription active — welcome to Notarium Pro!");
          params.delete("session_id");
          setParams(params, { replace: true });
          await load();
          return;
        }
      } catch { /* ignore transient */ }
      if (Date.now() - start > 20000) { load(); return; }
      if (!cancelled) setTimeout(poll, 1500);
    };
    poll();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const subscribe = async (lookupKey) => {
    setBusy(lookupKey);
    try {
      const { data } = await api.post("/billing/checkout", {
        lookup_key: lookupKey,
        origin_url: window.location.origin,
      });
      window.location.href = data.checkout_url;
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Checkout failed");
      setBusy(null);
    }
  };

  const openPortal = async () => {
    setPortalBusy(true);
    try {
      const { data } = await api.post("/billing/portal", { origin_url: window.location.origin });
      window.location.href = data.portal_url;
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Portal error");
      setPortalBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-[#4B5563]">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  if (!state) return null;

  const status = state.effective_status;
  const meta = statusMeta[status] || statusMeta.expired;
  const hasSub = status === "active" && state.raw_status === "active";

  return (
    <div className="space-y-6" data-testid="billing-page">
      <div className="border-b border-[#E2E8F0] pb-6">
        <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Billing</div>
        <h1 className="font-serif text-4xl text-[#00317A]">Subscription</h1>
        <p className="text-sm text-[#4B5563] mt-1">
          Each notary manages their own Notarium plan. Cancel anytime.
        </p>
      </div>

      {/* Current status */}
      <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="billing-status-card">
        <div className="flex items-start justify-between gap-6 flex-wrap">
          <div className="flex items-start gap-4">
            <div className="w-11 h-11 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
              <CreditCard className="w-5 h-5 text-[#00317A]" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <div className="font-serif text-lg text-[#00317A]">
                  {state.plan_label ? `Notarium Pro — ${state.plan_label}` : "Notarium Pro"}
                </div>
                <Badge variant="outline" className={`rounded-sm ${meta.cls}`} data-testid="billing-status-badge">
                  {meta.label}
                </Badge>
                {state.is_admin_bypass && (
                  <Badge variant="outline" className="rounded-sm bg-[#EEF2FB] text-[#00317A] border-[#C7D4EC]">Admin bypass</Badge>
                )}
              </div>
              <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mt-1">Current plan</div>

              {status === "trialing" && (
                <p className="text-sm text-[#4B5563] mt-3 flex items-center gap-2">
                  <CalendarDays className="w-4 h-4 text-[#DF0D1B]" />
                  <span>
                    <span className="font-medium text-[#111827]">{state.trial_days_left} days</span> left in your free trial.
                  </span>
                </p>
              )}
              {status === "active" && state.current_period_end && (
                <p className="text-sm text-[#4B5563] mt-3">
                  Next invoice on <span className="font-mono-plex text-[13px] text-[#111827]">{new Date(state.current_period_end).toLocaleDateString()}</span>
                  {state.cancel_at_period_end && <span className="ml-2 text-[#991B1B]">· cancels at period end</span>}
                </p>
              )}
              {(status === "expired" || status === "canceled" || status === "past_due") && (
                <p className="text-sm text-[#991B1B] mt-3">
                  Your access to journal entries, signers, and intake links is paused. Subscribe below to restore.
                </p>
              )}
            </div>
          </div>

          {hasSub && (
            <Button
              onClick={openPortal}
              disabled={portalBusy}
              className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white"
              data-testid="manage-billing-btn"
            >
              {portalBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : (
                <><ArrowUpRight className="w-4 h-4 mr-2" />Manage Billing</>
              )}
            </Button>
          )}
        </div>
      </Card>

      {/* Plan grid */}
      {!hasSub && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#4B5563] mb-4">Choose a plan</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {state.plans.map((p) => (
              <Card
                key={p.lookup_key}
                className={`p-6 rounded-sm shadow-none bg-white relative ${
                  p.lookup_key === "notarium_pro_yearly"
                    ? "border-[#00317A] border-2"
                    : "border-[#E2E8F0]"
                }`}
                data-testid={`plan-${p.lookup_key}`}
              >
                {p.lookup_key === "notarium_pro_yearly" && (
                  <div className="absolute -top-3 left-6 bg-[#00317A] text-white text-[10px] uppercase tracking-widest px-2 py-1 rounded-sm flex items-center gap-1">
                    <Sparkles className="w-3 h-3" />Best Value
                  </div>
                )}
                <div className="font-serif text-2xl text-[#00317A]">{p.label}</div>
                <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mt-1">Per notary · Billed {p.interval}ly</div>
                <div className="mt-5 flex items-baseline gap-1">
                  <span className="font-serif text-5xl text-[#111827]">${p.amount.toFixed(0)}</span>
                  <span className="text-sm text-[#4B5563]">/{p.interval}</span>
                </div>
                {p.savings_label && (
                  <div className="text-xs text-[#146334] mt-1 font-medium">{p.savings_label}</div>
                )}
                <ul className="mt-5 text-sm text-[#4B5563] space-y-2">
                  <li className="flex items-start gap-2"><ShieldCheck className="w-4 h-4 text-[#00317A] mt-0.5 shrink-0" />Unlimited journal entries</li>
                  <li className="flex items-start gap-2"><ShieldCheck className="w-4 h-4 text-[#00317A] mt-0.5 shrink-0" />PDF exports with Domini watermark</li>
                  <li className="flex items-start gap-2"><ShieldCheck className="w-4 h-4 text-[#00317A] mt-0.5 shrink-0" />Signer intake links & QR posters</li>
                  <li className="flex items-start gap-2"><ShieldCheck className="w-4 h-4 text-[#00317A] mt-0.5 shrink-0" />Commission expiration alerts</li>
                  <li className="flex items-start gap-2"><ShieldCheck className="w-4 h-4 text-[#00317A] mt-0.5 shrink-0" />14-day free trial · cancel anytime</li>
                </ul>
                <Button
                  onClick={() => subscribe(p.lookup_key)}
                  disabled={busy === p.lookup_key}
                  className={`mt-6 w-full rounded-sm ${
                    p.lookup_key === "notarium_pro_yearly"
                      ? "bg-[#DF0D1B] hover:bg-[#B70915] text-white"
                      : "bg-[#00317A] hover:bg-[#00265E] text-white"
                  }`}
                  data-testid={`subscribe-${p.lookup_key}`}
                >
                  {busy === p.lookup_key
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : `Start ${p.label} — 14 days free`}
                </Button>
              </Card>
            ))}
          </div>
        </div>
      )}

      <p className="text-xs text-[#4B5563] font-mono-plex">
        Payments processed by Stripe. Test card <span className="text-[#111827]">4242 4242 4242 4242</span> · any future expiry · any CVC.
      </p>
    </div>
  );
}
