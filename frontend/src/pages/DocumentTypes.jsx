import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Trash2, Plus } from "lucide-react";
import { toast } from "sonner";

export default function DocumentTypes() {
  const [types, setTypes] = useState([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const load = async () => {
    const { data } = await api.get("/document-types");
    setTypes(data);
  };
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await api.post("/document-types", { name, description });
      setName(""); setDescription("");
      toast.success("Document type added");
      load();
    } catch { toast.error("Failed to add"); }
  };

  const remove = async (id) => {
    await api.delete(`/document-types/${id}`);
    load();
  };

  return (
    <div className="space-y-6" data-testid="doctypes-page">
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#B49A5A] mb-2">Library</div>
          <h1 className="font-serif text-4xl text-[#0A192F]">Document Types</h1>
          <p className="text-sm text-[#4B5563] mt-1">Categorize documents you notarize.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-1 p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white h-fit">
          <h3 className="font-serif text-xl text-[#0A192F] mb-4">Add New Type</h3>
          <form onSubmit={add} className="space-y-4">
            <div>
              <Label>Name *</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} className="mt-1 rounded-sm" placeholder="e.g., Real Estate Deed" data-testid="doctype-name-input" />
            </div>
            <div>
              <Label>Description</Label>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} className="mt-1 rounded-sm" placeholder="Optional" />
            </div>
            <Button type="submit" className="rounded-sm bg-[#0A192F] hover:bg-[#12253F] text-white w-full" data-testid="add-doctype-btn">
              <Plus className="w-4 h-4 mr-2" /> Add Type
            </Button>
          </form>
        </Card>

        <Card className="lg:col-span-2 p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white">
          <h3 className="font-serif text-xl text-[#0A192F] mb-4">Current Library</h3>
          {types.length === 0 ? (
            <p className="text-sm text-[#4B5563]">No document types yet. Add one to get started.</p>
          ) : (
            <div className="divide-y divide-[#E2E8F0]">
              {types.map((t, i) => (
                <div key={t.id} className="py-3 flex items-center justify-between row-anim" style={{ animationDelay: `${i*30}ms` }}>
                  <div>
                    <div className="font-medium text-[#0A192F]">{t.name}</div>
                    {t.description && <div className="text-xs text-[#4B5563] mt-0.5">{t.description}</div>}
                  </div>
                  <Button size="icon" variant="ghost" onClick={() => remove(t.id)} data-testid={`delete-doctype-${t.id}`} className="hover:text-[#991B1B]">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
