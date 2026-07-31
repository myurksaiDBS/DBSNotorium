import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, formatApiError } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle2, ShieldCheck, Clock } from "lucide-react";

const ID_TYPES = [
  "Driver's License", "State ID", "US Passport", "Military ID",
  "Permanent Resident Card", "Foreign Passport",
];

export default function SignerIntake() {
  const { token } = useParams();
  const [ctx, setCtx] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({
    signer_name: "",
    signer_address: "",
    signer_phone: "",
    signer_email: "",
    id_type: "Driver's License",
    id_number: "",
    id_expiration: "",
    id_issued_by: "",
    notes: "",
  });

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get(`/public/intake/${token}`);
        setCtx(data);
        if (data.status === "completed" || data.status === "used") setSubmitted(true);
      } catch (e) {
        setError(formatApiError(e.response?.data?.detail) || "Link not found");
      } finally { setLoading(false); }
    })();
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post(`/public/intake/${token}`, form);
      setSubmitted(true);
    } catch (err) {
      setError(formatApiError(err.response?.data?.detail) || "Failed to submit");
    } finally { setSaving(false); }
  };

  if (loading) {
    return <FullScreen><div className="text-[#4B5563] text-sm font-mono-plex uppercase tracking-widest">Loading...</div></FullScreen>;
  }

  if (error && !ctx) {
    return <BrandedFrame ctx={{}}>
      <div className="text-center py-10">
        <div className="mx-auto w-12 h-12 rounded-full bg-[#FDECEC] flex items-center justify-center mb-4">
          <Clock className="w-6 h-6 text-[#991B1B]" />
        </div>
        <h2 className="font-serif text-2xl text-[#00317A] mb-2">Link unavailable</h2>
        <p className="text-sm text-[#4B5563]">{error}</p>
      </div>
    </BrandedFrame>;
  }

  if (ctx?.status === "expired") {
    return <BrandedFrame ctx={ctx}>
      <div className="text-center py-10">
        <div className="mx-auto w-12 h-12 rounded-full bg-[#FDECEC] flex items-center justify-center mb-4">
          <Clock className="w-6 h-6 text-[#991B1B]" />
        </div>
        <h2 className="font-serif text-2xl text-[#00317A] mb-2">This link has expired</h2>
        <p className="text-sm text-[#4B5563]">Please contact {ctx.notary_name || "your notary"} for a new link.</p>
      </div>
    </BrandedFrame>;
  }

  if (submitted) {
    return <BrandedFrame ctx={ctx}>
      <div className="text-center py-10">
        <div className="mx-auto w-14 h-14 rounded-full bg-[#E8F5EC] flex items-center justify-center mb-4">
          <CheckCircle2 className="w-7 h-7 text-[#146334]" />
        </div>
        <h2 className="font-serif text-3xl text-[#00317A] mb-2">You're all set</h2>
        <p className="text-sm text-[#4B5563] max-w-md mx-auto">
          Thank you. Your details have been sent to {ctx?.notary_name || "your notary"}.
          They'll bring this information to your appointment — you don't need to keep this page open.
        </p>
      </div>
    </BrandedFrame>;
  }

  return (
    <BrandedFrame ctx={ctx}>
      <form onSubmit={submit} className="space-y-6" data-testid="intake-form">
        {ctx?.appointment_note && (
          <div className="p-4 border border-[#E2E8F0] bg-[#F4F7F9] rounded-sm">
            <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mb-1">Note from your notary</div>
            <div className="text-sm text-[#111827]">{ctx.appointment_note}</div>
          </div>
        )}

        <section>
          <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Your Information</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <Label>Full legal name *</Label>
              <Input required value={form.signer_name} onChange={(e) => setForm({...form, signer_name: e.target.value})} className="mt-1 rounded-sm h-11" data-testid="intake-signer-name" />
            </div>
            <div className="md:col-span-2">
              <Label>Address</Label>
              <Input value={form.signer_address} onChange={(e) => setForm({...form, signer_address: e.target.value})} className="mt-1 rounded-sm h-11" data-testid="intake-address" />
            </div>
            <div>
              <Label>Phone</Label>
              <Input value={form.signer_phone} onChange={(e) => setForm({...form, signer_phone: e.target.value})} className="mt-1 rounded-sm h-11 font-mono-plex" data-testid="intake-phone" />
            </div>
            <div>
              <Label>Email</Label>
              <Input type="email" value={form.signer_email} onChange={(e) => setForm({...form, signer_email: e.target.value})} className="mt-1 rounded-sm h-11" data-testid="intake-email" />
            </div>
          </div>
        </section>

        <section>
          <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Identification</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label>ID type *</Label>
              <Select value={form.id_type} onValueChange={(v) => setForm({...form, id_type: v})}>
                <SelectTrigger className="mt-1 rounded-sm h-11" data-testid="intake-id-type"><SelectValue /></SelectTrigger>
                <SelectContent>{ID_TYPES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <Label>ID number *</Label>
              <Input required value={form.id_number} onChange={(e) => setForm({...form, id_number: e.target.value})} className="mt-1 rounded-sm h-11 font-mono-plex" data-testid="intake-id-number" />
            </div>
            <div>
              <Label>Issued by (state/country)</Label>
              <Input value={form.id_issued_by} onChange={(e) => setForm({...form, id_issued_by: e.target.value})} className="mt-1 rounded-sm h-11" data-testid="intake-id-issued" />
            </div>
            <div>
              <Label>Expiration date</Label>
              <Input type="date" value={form.id_expiration} onChange={(e) => setForm({...form, id_expiration: e.target.value})} className="mt-1 rounded-sm h-11 font-mono-plex" data-testid="intake-id-exp" />
            </div>
          </div>
        </section>

        <section>
          <Label>Anything your notary should know</Label>
          <Textarea value={form.notes} onChange={(e) => setForm({...form, notes: e.target.value})} className="mt-1 rounded-sm" rows={3} placeholder="Optional" data-testid="intake-notes" />
        </section>

        {error && <div className="text-sm text-[#991B1B] bg-red-50 border border-red-200 rounded-sm px-3 py-2">{error}</div>}

        <div className="flex items-start gap-3 p-4 bg-[#F4F7F9] rounded-sm border border-[#E2E8F0]">
          <ShieldCheck className="w-5 h-5 text-[#00317A] shrink-0 mt-0.5" />
          <div className="text-xs text-[#4B5563] leading-relaxed">
            Your information will be sent only to {ctx?.notary_name || "your notary"} and used to complete the notarization.
            You will still present your ID in person.
          </div>
        </div>

        <Button type="submit" disabled={saving}
          className="w-full h-12 rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white text-base"
          data-testid="intake-submit-btn">
          {saving ? "Submitting..." : "Send to my Notary"}
        </Button>
      </form>
    </BrandedFrame>
  );
}

function FullScreen({ children }) {
  return <div className="min-h-screen bg-[#F4F7F9] flex items-center justify-center">{children}</div>;
}

function BrandedFrame({ ctx, children }) {
  const hasFirm = !!(ctx?.firm_name || ctx?.firm_logo_data);
  return (
    <div className="min-h-screen bg-[#F4F7F9]">
      <div className="bg-[#00317A] text-white">
        <div className="max-w-2xl mx-auto px-6 py-6 flex items-center gap-4">
          <div className="w-14 h-14 rounded-sm bg-white p-1.5 shadow-md shrink-0">
            <img src="/domini-logo.png" alt="Domini" className="w-full h-full object-contain" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-serif text-xl leading-tight">Domini Business Services</div>
            <div className="text-[10px] uppercase tracking-[0.25em] text-white/70 mt-0.5">Notary · Signer Intake</div>
          </div>
          {ctx?.firm_logo_data && (
            <div className="hidden sm:flex items-center gap-3 pl-4 border-l border-white/20">
              <div className="h-12 w-24 bg-white/95 rounded-sm p-1.5 flex items-center justify-center">
                <img src={ctx.firm_logo_data} alt={ctx?.firm_name || "Firm logo"}
                     className="max-h-full max-w-full object-contain" data-testid="intake-firm-logo" />
              </div>
            </div>
          )}
        </div>
        <div className="h-1 bg-[#DF0D1B]" />
      </div>

      <div className="max-w-2xl mx-auto px-6 py-10">
        {hasFirm && (
          <div className="mb-4 flex items-center gap-3" data-testid="intake-firm-banner">
            {ctx?.firm_logo_data && (
              <div className="w-10 h-10 rounded-sm bg-white border border-[#E2E8F0] p-1.5 shrink-0 sm:hidden">
                <img src={ctx.firm_logo_data} alt="" className="w-full h-full object-contain" />
              </div>
            )}
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#4B5563]">You've been invited by</div>
              <div className="font-serif text-lg text-[#00317A]" data-testid="intake-firm-name">
                {ctx?.firm_name || ctx?.notary_name || "Your notary"}
              </div>
              {ctx?.firm_website && (
                <a href={ctx.firm_website.startsWith("http") ? ctx.firm_website : `https://${ctx.firm_website}`}
                   target="_blank" rel="noopener noreferrer"
                   className="text-xs text-[#DF0D1B] hover:underline font-mono-plex"
                   data-testid="intake-firm-website">
                  {ctx.firm_website}
                </a>
              )}
            </div>
          </div>
        )}
        <div className="bg-white border border-[#E2E8F0] rounded-sm p-8 md:p-10 shadow-sm">
          <div className="mb-6">
            <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Before we meet</div>
            <h1 className="font-serif text-3xl text-[#00317A]">Signer Intake Form</h1>
            {ctx?.notary_name && (
              <p className="text-sm text-[#4B5563] mt-2">
                Requested by <span className="font-medium text-[#111827]">{ctx.notary_name}</span>
                {ctx.commission_number && <span className="font-mono-plex text-xs"> · Commission #{ctx.commission_number}</span>}
                {ctx.state && <span> · {ctx.state}</span>}
              </p>
            )}
          </div>
          {children}
        </div>
        <div className="text-center mt-6 text-[10px] uppercase tracking-[0.25em] text-[#4B5563] font-mono-plex">
          A Division of YFI Enterprises, LLC
        </div>
      </div>
    </div>
  );
}
