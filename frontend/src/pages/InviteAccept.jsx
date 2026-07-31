import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api, formatApiError } from "@/api";
import { useAuth } from "@/context/AuthContext";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Loader2, ShieldCheck, MailCheck, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

export default function InviteAccept() {
  const { token } = useParams();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [invite, setInvite] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", password: "", confirm: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get(`/public/invite/${token}`)
      .then(({ data }) => setInvite(data))
      .catch((e) => setErr(formatApiError(e.response?.data?.detail) || "Invalid invitation"))
      .finally(() => setLoading(false));
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    if (form.password.length < 8) { toast.error("Password must be at least 8 characters"); return; }
    if (form.password !== form.confirm) { toast.error("Passwords don't match"); return; }
    if (!form.name.trim()) { toast.error("Please enter your name"); return; }
    setSaving(true);
    try {
      await api.post(`/public/invite/${token}/accept`, {
        name: form.name.trim(),
        password: form.password,
      });
      toast.success(`Welcome to Notarium!`);
      await refresh();
      navigate("/");
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not accept invitation");
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#F4F7F9] flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-[#00317A]" />
      </div>
    );
  }

  if (err || !invite) {
    return (
      <div className="min-h-screen bg-[#F4F7F9] flex items-center justify-center px-6">
        <Card className="w-full max-w-md p-10 border-[#F4B4B4] rounded-sm shadow-none bg-white text-center" data-testid="invite-invalid">
          <div className="w-12 h-12 rounded-sm bg-[#FDECEC] mx-auto flex items-center justify-center mb-4">
            <AlertTriangle className="w-6 h-6 text-[#991B1B]" />
          </div>
          <h1 className="font-serif text-2xl text-[#00317A] mb-2">Invitation unavailable</h1>
          <p className="text-sm text-[#4B5563] mb-6">{err || "This invitation link is invalid."}</p>
          <Link to="/login" className="text-sm text-[#DF0D1B] underline underline-offset-2">Go to sign in →</Link>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F4F7F9] flex items-center justify-center px-6 py-10">
      <div className="w-full max-w-md" data-testid="invite-page">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-sm bg-[#00317A] p-2 flex items-center justify-center">
              <img src="/domini-logo.png" alt="Domini" className="w-full h-full object-contain" />
            </div>
          </div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2 flex items-center justify-center gap-2">
            <MailCheck className="w-3 h-3" /> Invitation
          </div>
          <h1 className="font-serif text-3xl text-[#00317A]">Join Notarium</h1>
          <p className="text-sm text-[#4B5563] mt-2">
            {invite.invited_by_name ? <><span className="font-medium">{invite.invited_by_name}</span> invited you to </> : "You're invited to "}
            {invite.firm_name ? <><span className="font-medium">{invite.firm_name}</span></> : "a firm"} on Notarium.
          </p>
        </div>

        <Card className="p-8 border-[#E2E8F0] rounded-sm shadow-none bg-white">
          <form onSubmit={submit} className="space-y-4">
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Email</Label>
              <Input value={invite.email} disabled className="mt-1 rounded-sm font-mono-plex bg-[#F4F7F9]" data-testid="invite-email" />
            </div>
            {invite.firm_name && (
              <div>
                <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Firm</Label>
                <Input value={invite.firm_name} disabled className="mt-1 rounded-sm bg-[#F4F7F9]" data-testid="invite-firm" />
              </div>
            )}
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Your Full Name</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="mt-1 rounded-sm"
                autoFocus
                data-testid="invite-name-input"
              />
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Create Password</Label>
              <Input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="mt-1 rounded-sm"
                minLength={8}
                data-testid="invite-password-input"
              />
              <div className="text-[11px] text-[#4B5563] mt-1">At least 8 characters.</div>
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Confirm Password</Label>
              <Input
                type="password"
                value={form.confirm}
                onChange={(e) => setForm({ ...form, confirm: e.target.value })}
                className="mt-1 rounded-sm"
                data-testid="invite-confirm-input"
              />
            </div>

            <Button
              type="submit"
              disabled={saving}
              className="w-full rounded-sm bg-[#DF0D1B] hover:bg-[#B70915] text-white mt-2"
              data-testid="invite-accept-btn"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : (<><ShieldCheck className="w-4 h-4 mr-2" />Accept & create account</>)}
            </Button>
          </form>
        </Card>
        <p className="text-center text-[11px] text-[#4B5563] mt-6 font-mono-plex">Powered by Notarium · Domini Business Services</p>
      </div>
    </div>
  );
}
