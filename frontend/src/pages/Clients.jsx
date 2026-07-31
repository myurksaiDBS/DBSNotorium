import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Plus, Search, Trash2, Pencil } from "lucide-react";
import { toast } from "sonner";

const empty = { name: "", address: "", phone: "", email: "", id_type: "", id_number: "", notes: "" };

export default function Clients() {
  const [clients, setClients] = useState([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    const { data } = await api.get("/clients", { params: q ? { q } : {} });
    setClients(data);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [q]);

  const save = async (e) => {
    e.preventDefault();
    try {
      if (editingId) {
        await api.put(`/clients/${editingId}`, form);
        toast.success("Signer updated");
      } else {
        await api.post("/clients", form);
        toast.success("Signer added");
      }
      setOpen(false); setForm(empty); setEditingId(null);
      load();
    } catch { toast.error("Failed to save"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Delete this signer?")) return;
    await api.delete(`/clients/${id}`);
    load();
    toast.success("Signer removed");
  };

  return (
    <div className="space-y-6" data-testid="clients-page">
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#B49A5A] mb-2">Contacts</div>
          <h1 className="font-serif text-4xl text-[#0A192F]">Signers</h1>
          <p className="text-sm text-[#4B5563] mt-1">{clients.length} people on record</p>
        </div>
        <Button onClick={() => { setEditingId(null); setForm(empty); setOpen(true); }}
          className="rounded-sm bg-[#0A192F] hover:bg-[#12253F] text-white"
          data-testid="new-client-btn">
          <Plus className="w-4 h-4 mr-2" /> Add Signer
        </Button>
      </div>

      <Card className="p-4 border-[#E2E8F0] rounded-sm shadow-none bg-white">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#4B5563]" />
          <Input placeholder="Search by name, phone, email..." value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pl-9 rounded-sm h-10" data-testid="clients-search" />
        </div>
      </Card>

      <Card className="border-[#E2E8F0] rounded-sm shadow-none bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-[#F4F7F9] hover:bg-[#F4F7F9]">
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Name</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Phone</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Email</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">ID</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Address</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {clients.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center py-10 text-[#4B5563]">No signers yet.</TableCell></TableRow>
            ) : clients.map((c, i) => (
              <TableRow key={c.id} data-zebra={i % 2 ? "odd" : "even"} className="row-anim" style={{ animationDelay: `${Math.min(i*20, 400)}ms` }}>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="font-mono-plex text-xs text-[#4B5563]">{c.phone || "—"}</TableCell>
                <TableCell className="text-sm text-[#4B5563]">{c.email || "—"}</TableCell>
                <TableCell className="text-xs font-mono-plex text-[#4B5563]">{c.id_type ? `${c.id_type} · ${c.id_number}` : "—"}</TableCell>
                <TableCell className="text-sm text-[#4B5563]">{c.address || "—"}</TableCell>
                <TableCell className="text-right">
                  <Button size="icon" variant="ghost" onClick={() => { setEditingId(c.id); setForm({ name: c.name, address: c.address, phone: c.phone, email: c.email, id_type: c.id_type, id_number: c.id_number, notes: c.notes }); setOpen(true); }} data-testid={`edit-client-${c.id}`}><Pencil className="w-4 h-4" /></Button>
                  <Button size="icon" variant="ghost" onClick={() => remove(c.id)} data-testid={`delete-client-${c.id}`} className="hover:text-[#991B1B]"><Trash2 className="w-4 h-4" /></Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="rounded-sm max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">{editingId ? "Edit Signer" : "New Signer"}</DialogTitle>
            <DialogDescription>Save signer details for reuse in future journal entries.</DialogDescription>
          </DialogHeader>
          <form onSubmit={save} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <Label>Full Name *</Label>
              <Input required value={form.name} onChange={(e) => setForm({...form, name: e.target.value})} className="mt-1 rounded-sm" data-testid="client-name-input" />
            </div>
            <div><Label>Phone</Label><Input value={form.phone} onChange={(e) => setForm({...form, phone: e.target.value})} className="mt-1 rounded-sm" /></div>
            <div><Label>Email</Label><Input type="email" value={form.email} onChange={(e) => setForm({...form, email: e.target.value})} className="mt-1 rounded-sm" /></div>
            <div className="md:col-span-2"><Label>Address</Label><Input value={form.address} onChange={(e) => setForm({...form, address: e.target.value})} className="mt-1 rounded-sm" /></div>
            <div><Label>ID Type</Label><Input value={form.id_type} onChange={(e) => setForm({...form, id_type: e.target.value})} className="mt-1 rounded-sm" /></div>
            <div><Label>ID Number</Label><Input value={form.id_number} onChange={(e) => setForm({...form, id_number: e.target.value})} className="mt-1 rounded-sm font-mono-plex" /></div>
            <div className="md:col-span-2 flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" className="rounded-sm bg-[#0A192F] hover:bg-[#12253F] text-white" data-testid="save-client-btn">Save</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
