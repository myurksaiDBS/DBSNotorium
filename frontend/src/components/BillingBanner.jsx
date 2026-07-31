import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api";
import { AlertTriangle, Clock } from "lucide-react";

export default function BillingBanner() {
  const [s, setS] = useState(null);

  useEffect(() => {
    let alive = true;
    api.get("/billing/status")
      .then(({ data }) => { if (alive) setS(data); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!s || s.is_admin_bypass) return null;
  const st = s.effective_status;
  if (st === "active") return null;

  if (st === "trialing" && s.trial_days_left > 3) {
    return null;   // quiet during comfortable trial window
  }

  if (st === "trialing") {
    return (
      <div className="bg-[#FEF9EC] border-b border-[#F0D28A] px-6 py-2.5 text-[13px] text-[#7A5B00] flex items-center justify-center gap-3" data-testid="trial-banner">
        <Clock className="w-4 h-4" />
        <span>
          Your free trial ends in <span className="font-semibold">{s.trial_days_left} {s.trial_days_left === 1 ? "day" : "days"}</span>.
        </span>
        <Link to="/billing" className="underline underline-offset-2 font-medium hover:text-[#5A4300]" data-testid="trial-banner-cta">
          Choose a plan →
        </Link>
      </div>
    );
  }

  // expired / canceled / past_due
  return (
    <div className="bg-[#FDECEC] border-b border-[#F4B4B4] px-6 py-2.5 text-[13px] text-[#991B1B] flex items-center justify-center gap-3" data-testid="expired-banner">
      <AlertTriangle className="w-4 h-4" />
      <span>Your Notarium access is paused — subscribe to keep logging entries.</span>
      <Link to="/billing" className="underline underline-offset-2 font-medium hover:text-[#7C1416]" data-testid="expired-banner-cta">
        Subscribe →
      </Link>
    </div>
  );
}
