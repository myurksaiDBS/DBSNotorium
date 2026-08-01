import { useEffect, useState } from "react";
import { api } from "@/api";
import { useNavigate } from "react-router-dom";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Plus, Copy, Trash2, MessageSquare, ArrowRight, Link2, QrCode, BellRing } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/api";

const STATUS_STYLE = {
  pending:   { label: "Awaiting signer", cls: "bg-[#FEF9EC] text-[#7A5B00] border-[#F0D28A]" },
  completed: { label: "Ready",            cls: "bg-[#E8F5EC] text-[#146334] border-[#B7E0C1]" },
  used:      { label: "Used",             cls: "bg-[#F4F7F9] text-[#4B5563] border-[#E2E8F0]" },
  expired:   { label: "Expired",          cls: "bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]" },
};

export default function Intakes() {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ label: "", appointment_note: "", expires_hours: 168, signer_email: "", appointment_at: "" });
  const [detail, setDetail] = useState(null);
  const [posterHeadline, setPosterHeadline] = useState("");
  const [posterNote, setPosterNote] = useState("");
  const nav = useNavigate();

  const load = async () => {
    const { data } = await api.get("/intakes");
    setItems(data);
  };
  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    try {
      // Convert datetime-local (naive local time) to a UTC ISO string so the
      // backend cron windows compare against a canonical timezone.
      const payload = { ...form };
      if (payload.appointment_at) {
        const local = new Date(payload.appointment_at);
        if (!isNaN(local.getTime())) payload.appointment_at = local.toISOString();
      }
      const { data } = await api.post("/intakes", payload);
      toast.success("Pre-fill link created");
      setOpen(false);
      setForm({ label: "", appointment_note: "", expires_hours: 168, signer_email: "", appointment_at: "" });
      load();
      // Auto-open detail so user can copy
      setDetail(data);
    } catch (err) {
      const msg = err?.response?.data?.detail;
      toast.error(typeof msg === "string" ? msg : "Failed to create link");
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Delete this intake link?")) return;
    await api.delete(`/intakes/${id}`);
    load();
    toast.success("Intake deleted");
  };

  const sendReminder = async (id) => {
    try {
      await api.post(`/intakes/${id}/send-reminder`);
      toast.success("Reminder sent to signer");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not send reminder");
    }
  };

  const publicUrl = (token) =>
    `${window.location.origin}/intake/${token}`;

  const copyLink = (token) => {
    navigator.clipboard.writeText(publicUrl(token));
    toast.success("Link copied");
  };

  const smsBody = (item) => {
    const url = publicUrl(item.token);
    const notary = item.notary_name || "your notary";
    return `Hi — this is ${notary} with Domini Business Services. Please fill out your signer details before we meet: ${url}`;
  };

  const copySMS = (item) => {
    navigator.clipboard.writeText(smsBody(item));
    toast.success("Message copied");
  };

  const downloadPoster = async (item, opts = {}) => {
    const params = new URLSearchParams();
    if (opts.headline) params.append("headline", opts.headline);
    if (opts.note) params.append("note", opts.note);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const url = `${API}/intakes/${item.id}/qr-poster.pdf${qs}`;
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) { toast.error("Failed to download poster"); return; }
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `intake-poster-${item.id.slice(-8)}.pdf`;
    document.body.appendChild(link); link.click(); link.remove();
    toast.success("Poster downloaded");
  };

  const openEntryFromIntake = async (item) => {
    // Mark as used, then hand off to Journal with prefill data via location state
    try { await api.post(`/intakes/${item.id}/mark-used`); } catch (e) { console.error("Mark intake used failed:", e); }
    nav("/journal", { state: { prefill: item.data, intakeId: item.id } });
  };

  return (
    <div className="space-y-6" data-testid="intakes-page">
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Pre-Appointment</div>
          <h1 className="font-serif text-4xl text-[#00317A]">Signer Intake Links</h1>
          <p className="text-sm text-[#4B5563] mt-1">
            Send signers a Domini-branded page to enter their name, address and ID before you meet.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}
          className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white"
          data-testid="new-intake-btn">
          <Plus className="w-4 h-4 mr-2" /> New Intake Link
        </Button>
      </div>

      <Card className="border-[#E2E8F0] rounded-sm shadow-none bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-[#F4F7F9] hover:bg-[#F4F7F9]">
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Label</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Signer</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Status</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Created</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Expires</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center py-12 text-[#4B5563]">
                No intake links yet. Create one and share the link with your signer.
              </TableCell></TableRow>
            ) : items.map((it, idx) => {
              const style = STATUS_STYLE[it.status] || STATUS_STYLE.pending;
              return (
                <TableRow key={it.id} className="row-anim" style={{ animationDelay: `${Math.min(idx*20, 400)}ms` }} data-zebra={idx % 2 ? "odd" : "even"}>
                  <TableCell>
                    <div className="font-medium text-[#00317A]">{it.label || "Signer intake"}</div>
                    {it.appointment_note && <div className="text-xs text-[#4B5563] mt-0.5">{it.appointment_note}</div>}
                  </TableCell>
                  <TableCell className="text-sm">{it.data?.signer_name || <span className="text-[#4B5563]">—</span>}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={`rounded-sm border font-normal ${style.cls}`}>{style.label}</Badge>
                  </TableCell>
                  <TableCell className="font-mono-plex text-xs text-[#4B5563]">{(it.created_at || "").slice(0,10)}</TableCell>
                  <TableCell className="font-mono-plex text-xs text-[#4B5563]">{(it.expires_at || "").slice(0,10)}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      {it.status === "pending" && it.signer_email && it.appointment_at && !it.reminder_sent_at && (
                        <Button size="sm" variant="ghost" onClick={() => sendReminder(it.id)} data-testid={`send-reminder-${it.id}`} title="Email a reminder to the signer now">
                          <BellRing className="w-4 h-4 mr-1" /> Remind
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => setDetail(it)} data-testid={`share-intake-${it.id}`}>
                        <Link2 className="w-4 h-4 mr-1" /> Share
                      </Button>
                      {it.status === "completed" && (
                        <Button size="sm" onClick={() => openEntryFromIntake(it)}
                          className="bg-[#DF0D1B] hover:bg-[#B00B16] text-white rounded-sm"
                          data-testid={`use-intake-${it.id}`}>
                          Use <ArrowRight className="w-3.5 h-3.5 ml-1" />
                        </Button>
                      )}
                      <Button size="icon" variant="ghost" onClick={() => remove(it.id)} data-testid={`delete-intake-${it.id}`} className="hover:text-[#991B1B]">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>

      {/* Create dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="rounded-sm max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl text-[#00317A]">New Intake Link</DialogTitle>
            <DialogDescription>
              A public, Domini-branded page will be created. Share the link — no login required for the signer.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={create} className="space-y-4">
            <div>
              <Label>Label (private, for you)</Label>
              <Input value={form.label} onChange={(e) => setForm({...form, label: e.target.value})} className="mt-1 rounded-sm" placeholder="e.g., Smith closing – Thu 3pm" data-testid="intake-label" />
            </div>
            <div>
              <Label>Appointment note (shown to signer)</Label>
              <Textarea value={form.appointment_note} onChange={(e) => setForm({...form, appointment_note: e.target.value})} className="mt-1 rounded-sm" rows={2} placeholder="Optional — meeting time, place, or documents to bring." data-testid="intake-note" />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <Label>Signer email <span className="text-[#4B5563] font-normal">(for reminders)</span></Label>
                <Input type="email" value={form.signer_email} onChange={(e) => setForm({...form, signer_email: e.target.value})} className="mt-1 rounded-sm" placeholder="signer@example.com" data-testid="intake-signer-email" />
              </div>
              <div>
                <Label>Appointment date &amp; time</Label>
                <Input type="datetime-local" value={form.appointment_at} onChange={(e) => setForm({...form, appointment_at: e.target.value})} className="mt-1 rounded-sm" data-testid="intake-appointment-at" />
                <div className="text-[10px] text-[#4B5563] mt-1">Uses your local time zone</div>
              </div>
            </div>
            <div className="text-xs text-[#4B5563] -mt-2 leading-relaxed">
              When both are set, we&apos;ll automatically email the signer ~24 hours before their appointment if the intake is still pending.
            </div>
            <div>
              <Label>Link expires in</Label>
              <select value={form.expires_hours} onChange={(e) => setForm({...form, expires_hours: parseInt(e.target.value)})}
                className="mt-1 w-full h-10 rounded-sm border border-[#E2E8F0] px-3 bg-white text-sm"
                data-testid="intake-expires">
                <option value={24}>24 hours</option>
                <option value={72}>3 days</option>
                <option value={168}>7 days</option>
                <option value={336}>14 days</option>
                <option value={720}>30 days</option>
              </select>
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white" data-testid="save-intake-btn">
                Create Link
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Share detail dialog */}
      <Dialog open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <DialogContent className="rounded-sm max-w-lg">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle className="font-serif text-2xl text-[#00317A]">Share with Signer</DialogTitle>
                <DialogDescription>{detail.label || "Signer intake"}</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div>
                  <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Public link</Label>
                  <div className="mt-1 flex gap-2">
                    <Input readOnly value={publicUrl(detail.token)} className="rounded-sm font-mono-plex text-xs" data-testid="intake-share-url" />
                    <Button type="button" onClick={() => copyLink(detail.token)} className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white" data-testid="copy-link-btn">
                      <Copy className="w-4 h-4 mr-1" /> Copy
                    </Button>
                  </div>
                </div>
                <div>
                  <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Text message draft</Label>
                  <Textarea readOnly value={smsBody(detail)} rows={4} className="mt-1 rounded-sm text-sm" data-testid="intake-sms-body" />
                  <div className="mt-2 flex gap-2">
                    <Button type="button" variant="outline" onClick={() => copySMS(detail)}
                      className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white" data-testid="copy-sms-btn">
                      <Copy className="w-4 h-4 mr-1" /> Copy Text
                    </Button>
                    <a href={`sms:?body=${encodeURIComponent(smsBody(detail))}`}
                       className="inline-flex items-center px-3 py-2 rounded-sm bg-[#DF0D1B] text-white text-sm hover:bg-[#B00B16] transition-colors"
                       data-testid="open-sms-btn">
                      <MessageSquare className="w-4 h-4 mr-1" /> Open in Messages
                    </a>
                  </div>
                </div>
                <div className="pt-3 border-t border-[#E2E8F0]">
                  <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Walk-in / print</Label>
                  <div className="mt-2 grid grid-cols-1 md:grid-cols-[80px,1fr] gap-3 items-start">
                    <img
                      src={`${API}/intakes/${detail.id}/qr.png`}
                      alt="QR"
                      className="w-20 h-20 border border-[#E2E8F0] rounded-sm p-1 bg-white"
                      data-testid="intake-qr-preview"
                    />
                    <div className="space-y-2">
                      <p className="text-xs text-[#4B5563] leading-relaxed">
                        Print a Domini-branded QR poster so walk-in signers can scan and pre-fill on their phone. Optional: customize for a specific appointment.
                      </p>
                      <Input
                        placeholder="Custom headline (defaults to 'Scan to complete your details')"
                        value={posterHeadline}
                        onChange={(e) => setPosterHeadline(e.target.value)}
                        className="rounded-sm h-9 text-xs"
                        maxLength={80}
                        data-testid="poster-headline-input"
                      />
                      <Textarea
                        placeholder={`Custom appointment note (defaults to intake note${detail.appointment_note ? `: "${detail.appointment_note}"` : ''})`}
                        value={posterNote}
                        onChange={(e) => setPosterNote(e.target.value)}
                        rows={2}
                        className="rounded-sm text-xs"
                        maxLength={240}
                        data-testid="poster-note-input"
                      />
                      <Button type="button"
                        onClick={() => downloadPoster(detail, { headline: posterHeadline, note: posterNote })}
                        className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white"
                        data-testid="download-poster-btn">
                        <QrCode className="w-4 h-4 mr-1.5" /> Download QR Poster (PDF)
                      </Button>
                    </div>
                  </div>
                </div>
                {detail.status === "completed" && detail.data && (
                  <div className="p-3 border border-[#B7E0C1] bg-[#F1FAF3] rounded-sm text-sm">
                    <div className="font-medium text-[#146334] mb-1">Signer has completed the form.</div>
                    <div className="text-[#4B5563] text-xs">
                      {detail.data.signer_name} · {detail.data.id_type} · {detail.data.id_number}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
