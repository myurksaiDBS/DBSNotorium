import { useState, useEffect } from "react";
import { api, formatApiError } from "@/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { UserPlus, Loader2, Copy, Check, Send, Trash2, MailX, RefreshCw } from "lucide-react";
import { toast } from "sonner";

export default function InviteNotaryDialog({ open, onOpenChange, defaultFirm = "", defaultState = "" }) {
  const [form, setForm] = useState({ email: "", firm_name: "", state: "", message: "" });
  const [sending, setSending] = useState(false);
  const [invites, setInvites] = useState([]);
  const [loadingList, setLoadingList] = useState(false);
  const [copiedToken, setCopiedToken] = useState(null);

  const loadInvites = async () => {
    setLoadingList(true);
    try {
      const { data } = await api.get("/invites");
      setInvites(data.invites || []);
    } catch { /* no-op */ } finally { setLoadingList(false); }
  };

  useEffect(() => {
    if (!open) return;
    setForm({ email: "", firm_name: defaultFirm, state: defaultState, message: "" });
    loadInvites();
  }, [open, defaultFirm, defaultState]);

  const send = async (e) => {
    e.preventDefault();
    if (!form.email.trim()) { toast.error("Enter a colleague's email"); return; }
    setSending(true);
    try {
      const { data } = await api.post("/invites", {
        email: form.email.trim(),
        firm_name: form.firm_name || null,
        state: form.state || null,
        message: form.message || null,
      });
      if (data.email_sent) {
        toast.success(`Invitation emailed to ${data.email}`);
      } else {
        toast.warning(`Invite created — email failed: ${data.email_error || "unknown"}. You can copy the link below.`);
      }
      setForm({ email: "", firm_name: defaultFirm, state: defaultState, message: "" });
      loadInvites();
    } catch (err) {
      toast.error(formatApiError(err.response?.data?.detail) || "Could not send invitation");
    } finally { setSending(false); }
  };

  const copyLink = async (token) => {
    const url = `${window.location.origin}/invite/${token}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedToken(token);
      toast.success("Invitation link copied");
      setTimeout(() => setCopiedToken(null), 2000);
    } catch {
      toast.error("Couldn't copy — link is: " + url);
    }
  };

  const revoke = async (id) => {
    if (!window.confirm("Revoke this invitation? The link will stop working.")) return;
    try {
      await api.delete(`/invites/${id}`);
      toast.success("Invitation revoked");
      loadInvites();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not revoke");
    }
  };

  const statusBadge = (inv) => {
    if (inv.used_at) return <Badge variant="outline" className="rounded-sm bg-[#E8F5EC] text-[#146334] border-[#B7E0C1]">Accepted</Badge>;
    if (inv.expires_at && new Date(inv.expires_at) < new Date()) {
      return <Badge variant="outline" className="rounded-sm bg-[#F4F7F9] text-[#4B5563] border-[#E2E8F0]">Expired</Badge>;
    }
    if (inv.email_status === "failed") {
      return <Badge variant="outline" className="rounded-sm bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]">Email failed</Badge>;
    }
    if (inv.email_status === "sent") return <Badge variant="outline" className="rounded-sm bg-[#EEF2FB] text-[#00317A] border-[#C7D4EC]">Sent</Badge>;
    return <Badge variant="outline" className="rounded-sm bg-[#FEF9EC] text-[#7A5B00] border-[#F0D28A]">Pending</Badge>;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl bg-white rounded-sm" data-testid="invite-dialog">
        <DialogHeader>
          <div className="flex items-center gap-3 mb-1">
            <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center">
              <UserPlus className="w-5 h-5 text-[#00317A]" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B]">Onboard</div>
              <DialogTitle className="font-serif text-2xl text-[#00317A]">Invite a Notary</DialogTitle>
            </div>
          </div>
          <DialogDescription className="text-sm text-[#4B5563]">
            Send a one-time signup link. Their account arrives pre-filled with your firm details.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={send} className="space-y-3 pt-2">
          <div>
            <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Colleague's Email</Label>
            <Input
              type="email"
              required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="jane@dominibusservices.com"
              className="mt-1 rounded-sm"
              data-testid="invite-form-email"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Firm Name (pre-fill)</Label>
              <Input
                value={form.firm_name}
                onChange={(e) => setForm({ ...form, firm_name: e.target.value })}
                className="mt-1 rounded-sm"
                data-testid="invite-form-firm"
              />
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">State (optional)</Label>
              <Input
                value={form.state}
                onChange={(e) => setForm({ ...form, state: e.target.value })}
                placeholder="e.g., California"
                className="mt-1 rounded-sm"
                data-testid="invite-form-state"
              />
            </div>
          </div>
          <div>
            <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Personal Note (optional)</Label>
            <Textarea
              rows={2}
              value={form.message}
              onChange={(e) => setForm({ ...form, message: e.target.value })}
              placeholder="Welcome to the team — set up your journal in under 60 seconds."
              className="mt-1 rounded-sm"
              data-testid="invite-form-message"
            />
          </div>
          <Button
            type="submit"
            disabled={sending}
            className="w-full rounded-sm bg-[#DF0D1B] hover:bg-[#B70915] text-white"
            data-testid="invite-form-send"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : (<><Send className="w-4 h-4 mr-2" />Send invitation</>)}
          </Button>
        </form>

        {/* Existing invites */}
        <div className="mt-6 pt-4 border-t border-[#E2E8F0]">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[10px] uppercase tracking-widest text-[#4B5563]">Sent Invitations</div>
            <button type="button" onClick={loadInvites} className="text-[10px] uppercase tracking-widest text-[#00317A] hover:text-[#DF0D1B] flex items-center gap-1" data-testid="refresh-invites">
              <RefreshCw className="w-3 h-3" />Refresh
            </button>
          </div>
          {loadingList ? (
            <div className="text-xs text-[#4B5563] flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />Loading...</div>
          ) : invites.length === 0 ? (
            <div className="text-xs text-[#4B5563] py-4 text-center bg-[#F4F7F9] rounded-sm border border-[#E2E8F0]" data-testid="no-invites">
              <MailX className="w-4 h-4 mx-auto mb-1 text-[#4B5563]" />
              No invitations sent yet.
            </div>
          ) : (
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1" data-testid="invites-list">
              {invites.map((inv) => (
                <div key={inv.id} className="flex items-center gap-3 p-3 border border-[#E2E8F0] rounded-sm bg-white" data-testid={`invite-row-${inv.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-[#111827] truncate">{inv.email}</div>
                    <div className="text-[11px] text-[#4B5563] font-mono-plex truncate">
                      {inv.firm_name || "—"}{inv.expires_at && !inv.used_at && ` · expires ${new Date(inv.expires_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  {statusBadge(inv)}
                  {!inv.used_at && (
                    <>
                      <Button type="button" size="sm" variant="ghost" onClick={() => copyLink(inv.token)} className="rounded-sm h-8 px-2 text-[#00317A] hover:bg-[#EEF2FB]" data-testid={`copy-invite-${inv.id}`}>
                        {copiedToken === inv.token ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => revoke(inv.id)} className="rounded-sm h-8 px-2 text-[#991B1B] hover:bg-[#FDECEC]" data-testid={`revoke-invite-${inv.id}`}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
