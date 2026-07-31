import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export default function Settings() {
  const { user, updateProfile } = useAuth();
  const [form, setForm] = useState({
    name: user?.name || "",
    commission_number: user?.commission_number || "",
    commission_expires: user?.commission_expires || "",
    state: user?.state || "",
  });
  const [saving, setSaving] = useState(false);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    const res = await updateProfile(form);
    setSaving(false);
    if (res.ok) toast.success("Commission details saved");
    else toast.error(res.error);
  };

  return (
    <div className="space-y-6" data-testid="settings-page">
      <div className="border-b border-[#E2E8F0] pb-6">
        <div className="text-[11px] uppercase tracking-[0.25em] text-[#B49A5A] mb-2">Profile</div>
        <h1 className="font-serif text-4xl text-[#0A192F]">Commission Details</h1>
        <p className="text-sm text-[#4B5563] mt-1">Displayed on your exported journals.</p>
      </div>

      <Card className="p-8 max-w-2xl border-[#E2E8F0] rounded-sm shadow-none bg-white">
        <form onSubmit={save} className="space-y-5">
          <div>
            <Label>Notary Name</Label>
            <Input value={form.name} onChange={(e) => setForm({...form, name: e.target.value})} className="mt-1 rounded-sm" data-testid="settings-name" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>Commission Number</Label>
              <Input value={form.commission_number || ""} onChange={(e) => setForm({...form, commission_number: e.target.value})} className="mt-1 rounded-sm font-mono-plex" data-testid="settings-comm-number" />
            </div>
            <div>
              <Label>State</Label>
              <Input value={form.state || ""} onChange={(e) => setForm({...form, state: e.target.value})} className="mt-1 rounded-sm" placeholder="e.g., California" data-testid="settings-state" />
            </div>
          </div>
          <div>
            <Label>Commission Expiration</Label>
            <Input type="date" value={form.commission_expires || ""} onChange={(e) => setForm({...form, commission_expires: e.target.value})} className="mt-1 rounded-sm font-mono-plex max-w-xs" data-testid="settings-comm-exp" />
          </div>
          <Button type="submit" disabled={saving} className="rounded-sm bg-[#0A192F] hover:bg-[#12253F] text-white" data-testid="save-settings-btn">
            {saving ? "Saving..." : "Save Details"}
          </Button>
        </form>
      </Card>

      <Card className="p-6 max-w-2xl border-[#B49A5A]/30 rounded-sm shadow-none bg-[#FBF7EF]">
        <div className="text-[10px] uppercase tracking-widest text-[#8B6F35] mb-2">Signed in as</div>
        <div className="text-[#0A192F] font-medium">{user?.email}</div>
      </Card>
    </div>
  );
}
