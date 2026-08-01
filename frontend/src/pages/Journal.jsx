import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Plus, Search, FileDown, Trash2, Eye, Pencil, Mail, FileText, Copy, ClipboardList, RotateCcw, Bookmark, BookmarkPlus, X, Download } from "lucide-react";
import SignaturePad from "@/components/SignaturePad";
import { toast } from "sonner";
import { API } from "@/api";

const NOTARIZATION_TYPES = [
  "Acknowledgment", "Jurat", "Oath / Affirmation", "Copy Certification",
  "Signature Witnessing", "Protest",
];
const ID_TYPES = [
  "Driver's License", "State ID", "US Passport", "Military ID",
  "Permanent Resident Card", "Foreign Passport", "Personally Known",
];

const emptyForm = {
  entry_date: new Date().toISOString().slice(0, 16),
  notarization_type: "Acknowledgment",
  document_type: "",
  document_description: "",
  signer_name: "",
  signer_address: "",
  signer_phone: "",
  signer_email: "",
  id_type: "Driver's License",
  id_number: "",
  id_expiration: "",
  id_issued_by: "",
  witness_name: "",
  fee: 0,
  notes: "",
  signature_data: "",
};

export default function Journal() {
  const [entries, setEntries] = useState([]);
  const [docTypes, setDocTypes] = useState([]);
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [open, setOpen] = useState(false);
  const [viewing, setViewing] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const sigRef = useRef(null);
  const location = useLocation();
  const nav = useNavigate();
  const [emailingEntry, setEmailingEntry] = useState(null);
  const [emailTo, setEmailTo] = useState("");
  const [emailNote, setEmailNote] = useState("");
  const [sending, setSending] = useState(false);
  const [emailHistory, setEmailHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [presets, setPresets] = useState([]);
  const [activePreset, setActivePreset] = useState(null);
  const [saveName, setSaveName] = useState("");
  const [savingPreset, setSavingPreset] = useState(false);
  const hasActiveFilters = !!(q || (typeFilter && typeFilter !== "all") || startDate || endDate);

  const loadPresets = useCallback(async () => {
    try {
      const { data } = await api.get("/journal-presets");
      setPresets(data || []);
    } catch (e) { console.error("Load presets failed:", e); }
  }, []);
  const applyPreset = (p) => {
    setQ(p.q || "");
    setTypeFilter(p.type || "all");
    setStartDate(p.start_date || "");
    setEndDate(p.end_date || "");
    setActivePreset(p.id);
    toast.success(`Applied "${p.name}"`);
  };
  const clearFilters = () => {
    setQ(""); setTypeFilter("all"); setStartDate(""); setEndDate("");
    setActivePreset(null);
  };
  const savePreset = async (e) => {
    e.preventDefault();
    const name = saveName.trim();
    if (!name) { toast.error("Give this filter a name"); return; }
    setSavingPreset(true);
    try {
      await api.post("/journal-presets", {
        name, q, type: typeFilter === "all" ? "" : typeFilter,
        start_date: startDate, end_date: endDate,
      });
      toast.success(`Saved "${name}"`);
      setSaveName("");
      loadPresets();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Could not save preset");
    } finally { setSavingPreset(false); }
  };
  const deletePreset = async (id, name) => {
    if (!window.confirm(`Delete "${name}"?`)) return;
    try {
      await api.delete(`/journal-presets/${id}`);
      if (activePreset === id) setActivePreset(null);
      loadPresets();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Could not delete");
    }
  };

  const loadEmailHistory = useCallback(async (entryId) => {
    setHistoryLoading(true);
    try {
      const { data } = await api.get(`/entries/${entryId}/email-history`);
      setEmailHistory(data);
    } catch (e) { console.error("Load email history failed:", e); setEmailHistory([]); }
    finally { setHistoryLoading(false); }
  }, []);

  // Refresh history whenever the view dialog opens for an entry
  useEffect(() => {
    if (viewing?.id) loadEmailHistory(viewing.id);
    else setEmailHistory([]);
  }, [viewing?.id, loadEmailHistory]);

  const openEmailDialog = (entry) => {
    setEmailingEntry(entry);
    setEmailTo(entry.signer_email || "");
    setEmailNote("");
  };

  const downloadCertificate = async (entry) => {
    const url = `${API}/entries/${entry.id}/certificate.pdf`;
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) { toast.error("Failed to download certificate"); return; }
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `notary-certificate-${entry.id.slice(-8)}.pdf`;
    document.body.appendChild(link); link.click(); link.remove();
    toast.success("Certificate downloaded");
  };

  const sendCertificateEmail = async (e) => {
    e.preventDefault();
    if (!emailingEntry) return;
    setSending(true);
    try {
      const { data } = await api.post(
        `/entries/${emailingEntry.id}/email-certificate`,
        { to: emailTo, message: emailNote },
      );
      toast.success(`Certificate emailed to ${data.sent_to}`);
      setEmailingEntry(null); setEmailTo(""); setEmailNote("");
      loadEntries();
      if (viewing?.id) loadEmailHistory(viewing.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to send email");
    } finally { setSending(false); }
  };

  // If arriving from an Intake, prefill and open the New Entry dialog
  useEffect(() => {
    const prefill = location.state?.prefill;
    if (!prefill) return;
    setEditingId(null);
    // Use functional setForm so we keep the current document_type without
    // depending on `form` (which would re-fire this effect on every keystroke).
    setForm(f => ({
      ...emptyForm,
      signer_name: prefill.signer_name || "",
      signer_address: prefill.signer_address || "",
      signer_phone: prefill.signer_phone || "",
      signer_email: prefill.signer_email || "",
      id_type: prefill.id_type || "Driver's License",
      id_number: prefill.id_number || "",
      id_expiration: prefill.id_expiration || "",
      id_issued_by: prefill.id_issued_by || "",
      notes: prefill.notes || "",
      document_type: f.document_type,
    }));
    setOpen(true);
    // Clear location state so refresh doesn't reopen
    nav("/journal", { replace: true, state: {} });
  }, [location.state, nav]);

  const loadEntries = useCallback(async () => {
    const params = {};
    if (q) params.q = q;
    if (typeFilter !== "all") params.notarization_type = typeFilter;
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    const { data } = await api.get("/entries", { params });
    setEntries(data);
  }, [q, typeFilter, startDate, endDate]);

  const loadDocTypes = useCallback(async () => {
    const { data } = await api.get("/document-types");
    setDocTypes(data);
    // Only seed the default document type when the form hasn't chosen one yet;
    // functional setForm keeps this hook independent of `form`.
    if (data.length) {
      setForm(f => (f.document_type ? f : { ...f, document_type: data[0].name }));
    }
  }, []);

  useEffect(() => { loadDocTypes(); }, [loadDocTypes]);
  useEffect(() => { loadEntries(); }, [loadEntries]);
  useEffect(() => { loadPresets(); }, [loadPresets]);

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...emptyForm, document_type: docTypes[0]?.name || "" });
    setOpen(true);
    setTimeout(() => sigRef.current?.clear(), 50);
  };

  const openEdit = (e) => {
    setEditingId(e.id);
    setForm({
      entry_date: (e.entry_date || "").slice(0, 16),
      notarization_type: e.notarization_type || "Acknowledgment",
      document_type: e.document_type || "",
      document_description: e.document_description || "",
      signer_name: e.signer_name || "",
      signer_address: e.signer_address || "",
      signer_phone: e.signer_phone || "",
      signer_email: e.signer_email || "",
      id_type: e.id_type || "Driver's License",
      id_number: e.id_number || "",
      id_expiration: e.id_expiration || "",
      id_issued_by: e.id_issued_by || "",
      witness_name: e.witness_name || "",
      fee: e.fee || 0,
      notes: e.notes || "",
      signature_data: e.signature_data || "",
    });
    setOpen(true);
  };

  const save = async (ev) => {
    ev.preventDefault();
    const sig = sigRef.current?.toDataURL() || form.signature_data || "";
    const payload = { ...form, signature_data: sig, fee: parseFloat(form.fee) || 0,
      entry_date: form.entry_date.length === 16 ? form.entry_date + ":00" : form.entry_date };
    try {
      if (editingId) {
        await api.put(`/entries/${editingId}`, payload);
        toast.success("Entry updated");
      } else {
        await api.post("/entries", payload);
        toast.success("Journal entry recorded");
      }
      setOpen(false);
      loadEntries();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Failed to save entry");
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Delete this entry permanently?")) return;
    await api.delete(`/entries/${id}`);
    toast.success("Entry deleted");
    loadEntries();
  };

  const exportPDF = async (preset) => {
    const params = new URLSearchParams();
    const sd = preset ? preset.start_date : startDate;
    const ed = preset ? preset.end_date : endDate;
    if (sd) params.append("start_date", sd);
    if (ed) params.append("end_date", ed);
    const url = `${API}/entries/export/pdf${params.toString() ? `?${params.toString()}` : ""}`;
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) { toast.error("Failed to export PDF"); return; }
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    const label = preset ? preset.name.replace(/[^a-z0-9]+/gi, "-").toLowerCase() : new Date().toISOString().slice(0,10);
    link.download = `notary-journal-${label}.pdf`;
    document.body.appendChild(link); link.click(); link.remove();
    toast.success(preset ? `Exported "${preset.name}"` : "PDF exported");
  };

  const totalFees = useMemo(
    () => entries.reduce((s, e) => s + (parseFloat(e.fee) || 0), 0),
    [entries]
  );

  return (
    <div className="space-y-6" data-testid="journal-page">
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Records</div>
          <h1 className="font-serif text-4xl text-[#00317A]">Journal Entries</h1>
          <p className="text-sm text-[#4B5563] mt-1">
            {entries.length} entries · ${totalFees.toFixed(2)} in fees
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={exportPDF} data-testid="export-pdf-btn"
            className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white">
            <FileDown className="w-4 h-4 mr-2" /> Export PDF
          </Button>
          <Button onClick={openCreate} data-testid="new-entry-btn"
            className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white">
            <Plus className="w-4 h-4 mr-2" /> New Entry
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="p-4 border-[#E2E8F0] rounded-sm shadow-none bg-white">
        {/* Saved-filter chips */}
        {(presets.length > 0 || hasActiveFilters) && (
          <div className="flex items-center flex-wrap gap-2 pb-3 mb-3 border-b border-[#E2E8F0]" data-testid="preset-bar">
            <span className="text-[10px] uppercase tracking-[0.25em] text-[#4B5563] mr-1">
              <Bookmark className="w-3 h-3 inline mr-1" />Saved
            </span>
            {presets.map((p) => (
              <div key={p.id}
                   className={`group flex items-center rounded-sm border text-xs transition-colors ${
                     activePreset === p.id
                       ? "bg-[#00317A] border-[#00317A] text-white"
                       : "bg-white border-[#E2E8F0] text-[#111827] hover:border-[#00317A]/40"
                   }`}
                   data-testid={`preset-${p.id}`}>
                <button
                  type="button"
                  onClick={() => applyPreset(p)}
                  className="pl-2.5 pr-1.5 py-1"
                  title={`${p.q || "(any)"} · ${p.type || "any type"} · ${p.start_date || "…"}—${p.end_date || "…"}`}
                >
                  {p.name}
                </button>
                <button
                  type="button"
                  onClick={() => exportPDF(p)}
                  className={`px-1.5 py-1 border-l ${
                    activePreset === p.id
                      ? "border-white/20 hover:bg-[#00265E]"
                      : "border-[#E2E8F0] hover:bg-[#EEF2FB] text-[#00317A]"
                  }`}
                  title={`Export "${p.name}" as PDF`}
                  data-testid={`preset-export-${p.id}`}
                  aria-label="Export preset as PDF"
                >
                  <Download className="w-3 h-3" />
                </button>
                <button
                  type="button"
                  onClick={() => deletePreset(p.id, p.name)}
                  className={`px-1.5 py-1 rounded-r-sm border-l ${
                    activePreset === p.id ? "border-white/20 hover:bg-[#00265E]" : "border-[#E2E8F0] hover:bg-[#FDECEC] text-[#4B5563] hover:text-[#991B1B]"
                  }`}
                  data-testid={`preset-delete-${p.id}`}
                  aria-label="Delete preset"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="text-xs text-[#DF0D1B] hover:underline ml-1"
                data-testid="clear-filters-btn"
              >
                Clear
              </button>
            )}
            {hasActiveFilters && (
              <form onSubmit={savePreset} className="ml-auto flex items-center gap-1" data-testid="save-preset-form">
                <Input
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder="Name this filter"
                  maxLength={60}
                  className="rounded-sm h-8 text-xs w-44"
                  data-testid="save-preset-input"
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={savingPreset || !saveName.trim()}
                  className="rounded-sm h-8 bg-[#00317A] hover:bg-[#00265E] text-white text-xs"
                  data-testid="save-preset-btn"
                >
                  <BookmarkPlus className="w-3.5 h-3.5 mr-1" />
                  {savingPreset ? "…" : "Save"}
                </Button>
              </form>
            )}
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div className="md:col-span-2 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#4B5563]" />
            <Input placeholder="Search signer, document, ID..." value={q} onChange={(e) => { setQ(e.target.value); setActivePreset(null); }}
              className="pl-9 rounded-sm h-10" data-testid="journal-search" />
          </div>
          <Select value={typeFilter} onValueChange={(v) => { setTypeFilter(v); setActivePreset(null); }}>
            <SelectTrigger className="rounded-sm h-10" data-testid="type-filter"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {NOTARIZATION_TYPES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input type="date" value={startDate} onChange={(e) => { setStartDate(e.target.value); setActivePreset(null); }} className="rounded-sm h-10" data-testid="start-date-filter" />
          <Input type="date" value={endDate} onChange={(e) => { setEndDate(e.target.value); setActivePreset(null); }} className="rounded-sm h-10" data-testid="end-date-filter" />
        </div>
      </Card>

      {/* Table */}
      <Card className="border-[#E2E8F0] rounded-sm shadow-none bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-[#F4F7F9] hover:bg-[#F4F7F9]">
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] w-14">#</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Date</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Type</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Signer</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Document</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">ID</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] text-right">Fee</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] w-32 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-[#4B5563]">No journal entries yet. Click "New Entry" to record your first notarization.</TableCell></TableRow>
            ) : entries.map((e, idx) => (
              <TableRow key={e.id} className="row-anim" style={{ animationDelay: `${Math.min(idx*20, 400)}ms` }} data-zebra={idx % 2 ? "odd" : "even"}>
                <TableCell className="font-mono-plex text-xs text-[#4B5563]">{entries.length - idx}</TableCell>
                <TableCell className="font-mono-plex text-xs">{(e.entry_date || "").slice(0,10)}</TableCell>
                <TableCell><Badge variant="secondary" className="rounded-sm bg-[#F4F7F9] text-[#00317A] border border-[#E2E8F0] font-normal">{e.notarization_type}</Badge></TableCell>
                <TableCell className="font-medium">{e.signer_name}</TableCell>
                <TableCell className="text-sm text-[#4B5563]">{e.document_type}{e.document_description ? ` — ${e.document_description}` : ""}</TableCell>
                <TableCell className="text-xs font-mono-plex text-[#4B5563]">{e.id_type} · {e.id_number}</TableCell>
                <TableCell className="text-right font-mono-plex">${(parseFloat(e.fee)||0).toFixed(2)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button size="icon" variant="ghost" onClick={() => setViewing(e)} data-testid={`view-entry-${e.id}`}><Eye className="w-4 h-4" /></Button>
                    <Button size="icon" variant="ghost" onClick={() => downloadCertificate(e)} data-testid={`download-cert-${e.id}`} title="Download certificate PDF">
                      <FileText className="w-4 h-4" />
                    </Button>
                    <Button size="icon" variant="ghost" onClick={() => openEmailDialog(e)} data-testid={`email-cert-${e.id}`} title="Email certificate to signer">
                      <Mail className="w-4 h-4" />
                    </Button>
                    <Button size="icon" variant="ghost" onClick={() => openEdit(e)} data-testid={`edit-entry-${e.id}`}><Pencil className="w-4 h-4" /></Button>
                    <Button size="icon" variant="ghost" onClick={() => remove(e.id)} data-testid={`delete-entry-${e.id}`} className="hover:text-[#991B1B]"><Trash2 className="w-4 h-4" /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      {/* Create/Edit Dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto rounded-sm">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl text-[#00317A]">
              {editingId ? "Edit Journal Entry" : "New Journal Entry"}
            </DialogTitle>
            <DialogDescription>Record the notarial act. All fields marked with * are required.</DialogDescription>
          </DialogHeader>
          <form onSubmit={save} className="space-y-6">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Notarial Act</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Label>Date &amp; Time *</Label>
                  <Input type="datetime-local" required value={form.entry_date}
                    onChange={(e) => setForm({...form, entry_date: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-date" />
                </div>
                <div>
                  <Label>Notarization Type *</Label>
                  <Select value={form.notarization_type} onValueChange={(v) => setForm({...form, notarization_type: v})}>
                    <SelectTrigger className="mt-1 rounded-sm" data-testid="form-notarization-type"><SelectValue /></SelectTrigger>
                    <SelectContent>{NOTARIZATION_TYPES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>Document Type *</Label>
                  <Select value={form.document_type} onValueChange={(v) => setForm({...form, document_type: v})}>
                    <SelectTrigger className="mt-1 rounded-sm" data-testid="form-doc-type"><SelectValue placeholder="Select..." /></SelectTrigger>
                    <SelectContent>{docTypes.map(t => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>Fee ($)</Label>
                  <Input type="number" step="0.01" value={form.fee}
                    onChange={(e) => setForm({...form, fee: e.target.value})}
                    className="mt-1 rounded-sm font-mono-plex" data-testid="form-fee" />
                </div>
                <div className="md:col-span-2">
                  <Label>Document Description</Label>
                  <Input value={form.document_description}
                    onChange={(e) => setForm({...form, document_description: e.target.value})}
                    placeholder="e.g., Deed of trust dated 3/15/2026"
                    className="mt-1 rounded-sm" data-testid="form-doc-desc" />
                </div>
              </div>
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Signer Information</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Label>Full Name *</Label>
                  <Input required value={form.signer_name}
                    onChange={(e) => setForm({...form, signer_name: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-signer-name" />
                </div>
                <div>
                  <Label>Phone</Label>
                  <Input value={form.signer_phone}
                    onChange={(e) => setForm({...form, signer_phone: e.target.value})}
                    className="mt-1 rounded-sm font-mono-plex" data-testid="form-signer-phone" />
                </div>
                <div className="md:col-span-2">
                  <Label>Address</Label>
                  <Input value={form.signer_address}
                    onChange={(e) => setForm({...form, signer_address: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-signer-address" />
                </div>
                <div>
                  <Label>Email</Label>
                  <Input type="email" value={form.signer_email}
                    onChange={(e) => setForm({...form, signer_email: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-signer-email" />
                </div>
                <div>
                  <Label>Credible Witness (optional)</Label>
                  <Input value={form.witness_name}
                    onChange={(e) => setForm({...form, witness_name: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-witness" />
                </div>
              </div>
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Identification</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Label>ID Type *</Label>
                  <Select value={form.id_type} onValueChange={(v) => setForm({...form, id_type: v})}>
                    <SelectTrigger className="mt-1 rounded-sm" data-testid="form-id-type"><SelectValue /></SelectTrigger>
                    <SelectContent>{ID_TYPES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>ID Number *</Label>
                  <Input required value={form.id_number}
                    onChange={(e) => setForm({...form, id_number: e.target.value})}
                    className="mt-1 rounded-sm font-mono-plex" data-testid="form-id-number" />
                </div>
                <div>
                  <Label>Issued By (State/Country)</Label>
                  <Input value={form.id_issued_by}
                    onChange={(e) => setForm({...form, id_issued_by: e.target.value})}
                    className="mt-1 rounded-sm" data-testid="form-id-issued-by" />
                </div>
                <div>
                  <Label>ID Expiration</Label>
                  <Input type="date" value={form.id_expiration}
                    onChange={(e) => setForm({...form, id_expiration: e.target.value})}
                    className="mt-1 rounded-sm font-mono-plex" data-testid="form-id-exp" />
                </div>
              </div>
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] mb-3">Signer's Signature</div>
              <SignaturePad ref={sigRef} initialData={form.signature_data} />
            </div>

            <div>
              <Label>Notes</Label>
              <Textarea value={form.notes}
                onChange={(e) => setForm({...form, notes: e.target.value})}
                placeholder="Additional details, thumbprint reference, etc."
                className="mt-1 rounded-sm" rows={3} data-testid="form-notes" />
            </div>

            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" data-testid="save-entry-btn"
                className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white">
                {editingId ? "Save Changes" : "Record Entry"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={!!viewing} onOpenChange={(o) => !o && setViewing(null)}>
        <DialogContent className="max-w-2xl rounded-sm">
          {viewing && (
            <>
              <DialogHeader>
                <DialogTitle className="font-serif text-2xl text-[#00317A]">Journal Entry Detail</DialogTitle>
                <DialogDescription className="font-mono-plex">{viewing.entry_date}</DialogDescription>
              </DialogHeader>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Notarization" value={viewing.notarization_type} />
                <Field label="Document" value={`${viewing.document_type}${viewing.document_description ? " — " + viewing.document_description : ""}`} />
                <Field label="Signer" value={viewing.signer_name} />
                <Field label="Fee" value={`$${(parseFloat(viewing.fee)||0).toFixed(2)}`} mono />
                <Field label="Address" value={viewing.signer_address || "—"} span />
                <Field label="Phone" value={viewing.signer_phone || "—"} mono />
                <Field label="Email" value={viewing.signer_email || "—"} />
                <Field label="ID Type" value={viewing.id_type} />
                <Field label="ID Number" value={viewing.id_number} mono />
                <Field label="ID Expires" value={viewing.id_expiration || "—"} mono />
                <Field label="Issued By" value={viewing.id_issued_by || "—"} />
                {viewing.witness_name && <Field label="Witness" value={viewing.witness_name} />}
                {viewing.notes && <Field label="Notes" value={viewing.notes} span />}
              </div>
              {viewing.signature_data && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mb-2">Signature</div>
                  <div className="border border-[#E2E8F0] rounded-sm p-2 bg-[#FDFDFD]">
                    <img src={viewing.signature_data} alt="signature" className="h-24 object-contain mx-auto" />
                  </div>
                </div>
              )}
              <div className="pt-4 border-t border-[#E2E8F0] flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => downloadCertificate(viewing)}
                  className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white"
                  data-testid="view-download-cert-btn">
                  <FileText className="w-4 h-4 mr-2" /> Download certificate
                </Button>
                <Button onClick={() => { openEmailDialog(viewing); setViewing(null); }}
                  className="rounded-sm bg-[#DF0D1B] hover:bg-[#B00B16] text-white"
                  data-testid="view-email-cert-btn">
                  <Mail className="w-4 h-4 mr-2" /> Email to signer
                </Button>
                {viewing.certificate_emailed_at && (
                  <div className="text-xs text-[#4B5563] self-center ml-auto">
                    Last sent to <span className="font-medium text-[#111827]">{viewing.certificate_emailed_to}</span>
                  </div>
                )}
              </div>

              {/* Email delivery history */}
              <div className="pt-2" data-testid="email-history-section">
                <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-[#4B5563] mb-3">
                  <ClipboardList className="w-3.5 h-3.5" /> Email Delivery History
                </div>
                {historyLoading ? (
                  <div className="text-xs text-[#4B5563] italic">Loading...</div>
                ) : emailHistory.length === 0 ? (
                  <div className="text-xs text-[#4B5563] italic p-3 bg-[#F4F7F9] rounded-sm border border-[#E2E8F0]">
                    No emails sent yet for this entry.
                  </div>
                ) : (
                  <ul className="divide-y divide-[#E2E8F0] border border-[#E2E8F0] rounded-sm" data-testid="email-history-list">
                    {emailHistory.map((h) => (
                      <li key={h.id} data-testid={`email-history-item-${h.id}`}
                          className="p-3 flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span className="text-sm font-medium text-[#111827] truncate">{h.to}</span>
                            {h.status === "sent" ? (
                              <span className="text-[10px] uppercase tracking-widest text-[#146334] bg-[#E8F5EC] px-1.5 py-0.5 rounded-sm">Sent</span>
                            ) : (
                              <span className="text-[10px] uppercase tracking-widest text-[#991B1B] bg-[#FDECEC] px-1.5 py-0.5 rounded-sm">Failed</span>
                            )}
                          </div>
                          <div className="text-[11px] text-[#4B5563] font-mono-plex mt-0.5">
                            {new Date(h.sent_at).toLocaleString()}
                          </div>
                          {h.error && (
                            <div className="text-[11px] text-[#991B1B] mt-1 leading-relaxed">
                              {h.error}
                            </div>
                          )}
                          {h.email_id && (
                            <div className="text-[10px] text-[#4B5563] font-mono-plex mt-1 truncate">
                              provider id: {h.email_id}
                            </div>
                          )}
                        </div>
                        {h.status === "failed" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              // Reopen the email dialog pre-filled with this row's recipient
                              // so the notary can retry without re-typing.
                              setEmailingEntry(viewing);
                              setEmailTo(h.to);
                              setEmailNote("");
                            }}
                            className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white shrink-0"
                            data-testid={`resend-cert-${h.id}`}
                          >
                            <RotateCcw className="w-3.5 h-3.5 mr-1" /> Resend
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Email certificate dialog */}
      <Dialog open={!!emailingEntry} onOpenChange={(o) => !o && setEmailingEntry(null)}>
        <DialogContent className="rounded-sm max-w-lg">
          {emailingEntry && (
            <>
              <DialogHeader>
                <DialogTitle className="font-serif text-2xl text-[#00317A]">Email Certificate</DialogTitle>
                <DialogDescription>
                  Send a Domini-branded receipt to {emailingEntry.signer_name}. Recipients get a public
                  download link, not the raw PDF attachment.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={sendCertificateEmail} className="space-y-4">
                <div>
                  <Label>Signer email *</Label>
                  <Input required type="email" value={emailTo} onChange={(e) => setEmailTo(e.target.value)}
                    className="mt-1 rounded-sm h-11" data-testid="email-cert-to" />
                </div>
                <div>
                  <Label>Personal note (optional)</Label>
                  <Textarea value={emailNote} onChange={(e) => setEmailNote(e.target.value)}
                    className="mt-1 rounded-sm" rows={3}
                    placeholder="e.g., Thanks again — call me if you need another copy."
                    data-testid="email-cert-note" />
                </div>
                <div className="text-xs text-[#4B5563] bg-[#F4F7F9] p-3 rounded-sm border border-[#E2E8F0]">
                  Certificate No. <span className="font-mono-plex text-[11px]">NOT-{emailingEntry.id.slice(-8).toUpperCase()}</span>
                  {" · "}Signer replies will come to your inbox.
                </div>
                <DialogFooter>
                  <Button type="button" variant="ghost" onClick={() => setEmailingEntry(null)}>Cancel</Button>
                  <Button type="submit" disabled={sending}
                    className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white"
                    data-testid="send-cert-email-btn">
                    <Mail className="w-4 h-4 mr-2" /> {sending ? "Sending..." : "Send Certificate"}
                  </Button>
                </DialogFooter>
              </form>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({ label, value, mono, span }) {
  return (
    <div className={span ? "col-span-2" : ""}>
      <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mb-0.5">{label}</div>
      <div className={`text-[#111827] ${mono ? "font-mono-plex" : ""}`}>{value}</div>
    </div>
  );
}
