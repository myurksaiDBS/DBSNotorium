import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api, formatApiError } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { BellRing, Send, Mail, ListChecks, Lock, Stamp, KeyRound, Upload, Trash2, ImagePlus, Building2, MailX, Plus, X } from "lucide-react";
import { toast } from "sonner";

export default function Settings() {
  const { user, updateProfile } = useAuth();
  const [form, setForm] = useState({
    name: user?.name || "",
    commission_number: user?.commission_number || "",
    commission_expires: user?.commission_expires || "",
    state: user?.state || "",
    alerts_enabled: user?.alerts_enabled !== false,
    firm_name: user?.firm_name || "",
    firm_address: user?.firm_address || "",
    firm_phone: user?.firm_phone || "",
    firm_website: user?.firm_website || "",
    firm_license_number: user?.firm_license_number || "",
  });
  const [firmSaving, setFirmSaving] = useState(false);
  const [bounces, setBounces] = useState(null);
  const [bouncePreview, setBouncePreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [alertsStatus, setAlertsStatus] = useState(null);
  const [testing, setTesting] = useState(false);
  const [checklist, setChecklist] = useState(null);
  const [seal, setSeal] = useState({ has_seal: false, seal_data: null });
  const [firmLogo, setFirmLogo] = useState({ has_logo: false, firm_logo_data: null });
  const [uploading, setUploading] = useState(false);
  const [logoUploading, setLogoUploading] = useState(false);
  const [pwd, setPwd] = useState({ current_password: "", new_password: "", confirm: "" });
  const [pwdSaving, setPwdSaving] = useState(false);
  const sealInputRef = useRef(null);
  const firmLogoInputRef = useRef(null);

  const loadStatus = async () => {
    try {
      const { data } = await api.get("/alerts/status");
      setAlertsStatus(data);
    } catch { /* no-op */ }
  };
  const loadChecklist = async () => {
    try {
      const { data } = await api.get("/renewal-checklist");
      setChecklist(data);
    } catch { /* no-op */ }
  };
  const loadSeal = async () => {
    try {
      const { data } = await api.get("/auth/seal");
      setSeal(data);
    } catch { /* no-op */ }
  };
  const onSealPick = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpe?g)$/.test(file.type)) {
      toast.error("Please choose a PNG or JPEG image.");
      return;
    }
    if (file.size > 500_000) {
      toast.error("Seal image must be under 500 KB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      setUploading(true);
      try {
        await api.put("/auth/seal", { seal_data: reader.result });
        toast.success("Seal uploaded");
        loadSeal();
      } catch (err) {
        toast.error(formatApiError(err.response?.data?.detail) || "Upload failed");
      } finally { setUploading(false); }
    };
    reader.readAsDataURL(file);
  };
  const removeSeal = async () => {
    if (!window.confirm("Remove your saved notary seal?")) return;
    await api.delete("/auth/seal");
    toast.success("Seal removed");
    loadSeal();
  };
  const loadFirmLogo = async () => {
    try {
      const { data } = await api.get("/auth/firm-logo");
      setFirmLogo(data);
    } catch { /* no-op */ }
  };
  const onFirmLogoPick = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpe?g)$/.test(file.type)) {
      toast.error("Please choose a PNG or JPEG image.");
      return;
    }
    if (file.size > 500_000) {
      toast.error("Firm logo must be under 500 KB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      setLogoUploading(true);
      try {
        await api.put("/auth/firm-logo", { firm_logo_data: reader.result });
        toast.success("Firm logo uploaded");
        loadFirmLogo();
      } catch (err) {
        toast.error(formatApiError(err.response?.data?.detail) || "Upload failed");
      } finally { setLogoUploading(false); }
    };
    reader.readAsDataURL(file);
  };
  const removeFirmLogo = async () => {
    if (!window.confirm("Remove your firm logo? Certificates will show only the Domini shield.")) return;
    await api.delete("/auth/firm-logo");
    toast.success("Firm logo removed");
    loadFirmLogo();
  };
  const changePassword = async (e) => {
    e.preventDefault();
    if (pwd.new_password.length < 8) { toast.error("New password must be at least 8 characters"); return; }
    if (pwd.new_password !== pwd.confirm) { toast.error("New password and confirmation don't match"); return; }
    setPwdSaving(true);
    try {
      await api.post("/auth/change-password", {
        current_password: pwd.current_password,
        new_password: pwd.new_password,
      });
      toast.success("Password updated");
      setPwd({ current_password: "", new_password: "", confirm: "" });
    } catch (err) {
      toast.error(formatApiError(err.response?.data?.detail) || "Password change failed");
    } finally { setPwdSaving(false); }
  };

  useEffect(() => { loadStatus(); loadChecklist(); loadSeal(); loadFirmLogo(); loadBounces(); }, []);

  const loadBounces = async () => {
    try {
      const { data } = await api.get("/alerts/bounces");
      setBounces(data);
    } catch { /* no-op */ }
  };

  const saveFirmProfile = async (e) => {
    e.preventDefault();
    setFirmSaving(true);
    const res = await updateProfile({
      firm_name: form.firm_name,
      firm_address: form.firm_address,
      firm_phone: form.firm_phone,
      firm_website: form.firm_website,
      firm_license_number: form.firm_license_number,
    });
    setFirmSaving(false);
    if (res.ok) toast.success("Firm profile saved");
    else toast.error(res.error);
  };

  const sendBouncePreview = async () => {
    setBouncePreview(true);
    try {
      const { data } = await api.post("/cron/delivery-bounces/preview");
      toast.success(`Preview digest sent to ${data.sent_to} (${data.count} bounces)`);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not send preview");
    } finally { setBouncePreview(false); }
  };

  const toggleChecklistItem = async (id, done) => {
    // Optimistic update
    setChecklist((c) => c && {
      ...c,
      items: c.items.map((it) => it.id === id ? { ...it, completed: done } : it),
      completed_count: c.completed_count + (done ? 1 : -1),
    });
    try {
      await api.post("/renewal-checklist/toggle", { item_id: id, done });
    } catch (e) {
      toast.error("Could not update — try again");
      loadChecklist();
    }
  };

  const [newStep, setNewStep] = useState({ title: "", detail: "" });
  const [addingStep, setAddingStep] = useState(false);

  const addCustomStep = async (e) => {
    e.preventDefault();
    if (!newStep.title.trim()) { toast.error("Give your step a title"); return; }
    setAddingStep(true);
    try {
      await api.post("/renewal-checklist/custom", newStep);
      toast.success("Custom step added");
      setNewStep({ title: "", detail: "" });
      loadChecklist();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not add step");
    } finally { setAddingStep(false); }
  };

  const deleteCustomStep = async (id) => {
    if (!window.confirm("Remove this custom step? Its progress will be cleared.")) return;
    try {
      await api.delete(`/renewal-checklist/custom/${encodeURIComponent(id)}`);
      loadChecklist();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not delete step");
    }
  };

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    const res = await updateProfile(form);
    setSaving(false);
    if (res.ok) { toast.success("Commission details saved"); loadStatus(); }
    else toast.error(res.error);
  };

  const toggleAlerts = async (checked) => {
    const previous = form.alerts_enabled;
    setForm(f => ({ ...f, alerts_enabled: checked }));
    const res = await updateProfile({ alerts_enabled: checked });
    if (res.ok) {
      toast.success(checked ? "Commission alerts enabled" : "Commission alerts paused");
      loadStatus();
    } else {
      // Revert optimistic update if persistence failed
      setForm(f => ({ ...f, alerts_enabled: previous }));
      toast.error(res.error);
    }
  };

  const sendTest = async () => {
    setTesting(true);
    try {
      const { data } = await api.post("/alerts/test");
      toast.success(`Preview email sent to ${data.sent_to}`);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail) || "Could not send test email");
    } finally { setTesting(false); }
  };

  const daysLeft = alertsStatus?.days_left;
  const daysBadge = () => {
    if (daysLeft == null) return null;
    if (daysLeft < 0) return <Badge data-testid="alerts-days-left" className="rounded-sm bg-[#FDECEC] text-[#991B1B] border border-[#F4B4B4]" variant="outline">Expired {Math.abs(daysLeft)} days ago</Badge>;
    if (daysLeft <= 7) return <Badge data-testid="alerts-days-left" className="rounded-sm bg-[#FDECEC] text-[#991B1B] border border-[#F4B4B4]" variant="outline">{daysLeft} days left</Badge>;
    if (daysLeft <= 30) return <Badge data-testid="alerts-days-left" className="rounded-sm bg-[#FEF9EC] text-[#7A5B00] border border-[#F0D28A]" variant="outline">{daysLeft} days left</Badge>;
    if (daysLeft <= 60) return <Badge data-testid="alerts-days-left" className="rounded-sm bg-[#EEF2FB] text-[#00317A] border border-[#C7D4EC]" variant="outline">{daysLeft} days left</Badge>;
    return <Badge data-testid="alerts-days-left" className="rounded-sm bg-[#E8F5EC] text-[#146334] border border-[#B7E0C1]" variant="outline">{daysLeft} days left</Badge>;
  };

  return (
    <div className="space-y-6" data-testid="settings-page">
      <div className="border-b border-[#E2E8F0] pb-6">
        <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Profile</div>
        <h1 className="font-serif text-4xl text-[#00317A]">Commission Details</h1>
        <p className="text-sm text-[#4B5563] mt-1">Displayed on your exported journals and used for alerts.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2 p-8 border-[#E2E8F0] rounded-sm shadow-none bg-white">
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
            <Button type="submit" disabled={saving} className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white" data-testid="save-settings-btn">
              {saving ? "Saving..." : "Save Details"}
            </Button>
          </form>
        </Card>

        {/* Alerts panel */}
        <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white h-fit" data-testid="alerts-panel">
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-start gap-3">
              <div className="w-9 h-9 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
                <BellRing className="w-4 h-4 text-[#00317A]" />
              </div>
              <div>
                <div className="font-serif text-lg text-[#00317A]">Commission Alerts</div>
                <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mt-0.5">Automatic reminders</div>
              </div>
            </div>
            <Switch
              checked={form.alerts_enabled}
              onCheckedChange={toggleAlerts}
              data-testid="alerts-toggle"
            />
          </div>

          <p className="text-xs text-[#4B5563] leading-relaxed">
            {`We'll email `}
            <span className="font-medium text-[#111827]">{user?.email}</span>
            {` at `}
            <span className="font-mono-plex text-[11px]">60 · 30 · 14 · 7 · 1</span>
            {` days before your commission expires — plus on expiration day and if it lapses.`}
          </p>

          <div className="mt-4 border-t border-[#E2E8F0] pt-4 space-y-2 text-xs">
            {alertsStatus?.configured ? (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-[#4B5563]">Commission expires</span>
                  <span className="font-mono-plex text-[#111827]" data-testid="alerts-expires-on">
                    {new Date(alertsStatus.commission_expires).toLocaleDateString(undefined,
                      { year: "numeric", month: "short", day: "numeric" })}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[#4B5563]">Time left</span>
                  {daysBadge()}
                </div>
                {alertsStatus.upcoming_alerts?.length > 0 && (
                  <div className="pt-3">
                    <div className="text-[10px] uppercase tracking-widest text-[#4B5563] mb-2">Upcoming alerts</div>
                    <ul className="space-y-1" data-testid="alerts-upcoming-list">
                      {alertsStatus.upcoming_alerts.map((a) => (
                        <li key={a.days_out} className="flex items-center justify-between" data-testid={`alerts-upcoming-${a.days_out}`}>
                          <span className="text-[#111827]">
                            {a.days_out === 0 ? "Day of expiration" : `${a.days_out}-day notice`}
                          </span>
                          <span className="font-mono-plex text-[10px] text-[#4B5563]">{a.on}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {alertsStatus.last_alert_sent_at && (
                  <div className="pt-2 text-[11px] text-[#4B5563] flex items-center gap-1">
                    <Mail className="w-3 h-3" />
                    Last alert sent {new Date(alertsStatus.last_alert_sent_at).toLocaleString()}
                  </div>
                )}
              </>
            ) : (
              <div className="text-[11px] text-[#4B5563] italic">
                Add your commission expiration date to activate alerts.
              </div>
            )}
          </div>

          <Button
            type="button"
            variant="outline"
            onClick={sendTest}
            disabled={testing || !form.alerts_enabled}
            className="mt-4 w-full rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white"
            data-testid="send-test-alert-btn"
          >
            <Send className="w-3.5 h-3.5 mr-2" />
            {testing ? "Sending..." : "Send preview to my inbox"}
          </Button>
        </Card>
      </div>

      <Card className="p-6 max-w-2xl border-[#DF0D1B]/25 rounded-sm shadow-none bg-[#FEF2F2]">
        <div className="text-[10px] uppercase tracking-widest text-[#991B1B] mb-2">Signed in as</div>
        <div className="text-[#00317A] font-medium">{user?.email}</div>
      </Card>

      {/* Notary Seal + Password */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="seal-card">
          <div className="flex items-start gap-3 mb-4 pb-4 border-b border-[#E2E8F0]">
            <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
              <Stamp className="w-5 h-5 text-[#00317A]" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Personal Brand</div>
              <div className="font-serif text-xl text-[#00317A]">Notary Seal</div>
              <p className="text-xs text-[#4B5563] mt-1">
                Optional. Stamps beside your attestation on every certificate PDF. PNG or JPEG, under 500 KB.
              </p>
            </div>
          </div>
          {seal.has_seal ? (
            <div className="flex items-center gap-4">
              <div className="w-24 h-24 border border-[#E2E8F0] rounded-sm p-2 bg-white flex items-center justify-center">
                <img src={seal.seal_data} alt="Notary seal" className="max-w-full max-h-full object-contain" data-testid="seal-preview" />
              </div>
              <div className="flex-1">
                <div className="text-xs text-[#146334] mb-2">Seal uploaded — it will appear on all future certificates.</div>
                <div className="flex gap-2">
                  <Button type="button" size="sm" variant="outline"
                    onClick={() => sealInputRef.current?.click()}
                    className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white"
                    data-testid="replace-seal-btn">
                    <Upload className="w-3.5 h-3.5 mr-1" /> Replace
                  </Button>
                  <Button type="button" size="sm" variant="ghost"
                    onClick={removeSeal}
                    className="rounded-sm text-[#991B1B] hover:bg-[#FDECEC]"
                    data-testid="remove-seal-btn">
                    <Trash2 className="w-3.5 h-3.5 mr-1" /> Remove
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <Button type="button"
              onClick={() => sealInputRef.current?.click()}
              disabled={uploading}
              className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white w-full"
              data-testid="upload-seal-btn">
              <Upload className="w-4 h-4 mr-2" />
              {uploading ? "Uploading..." : "Upload notary seal"}
            </Button>
          )}
          <input ref={sealInputRef} type="file" accept="image/png,image/jpeg"
            onChange={onSealPick} className="hidden" data-testid="seal-file-input" />
        </Card>

        <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="firm-logo-card">
          <div className="flex items-start gap-3 mb-4 pb-4 border-b border-[#E2E8F0]">
            <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
              <ImagePlus className="w-5 h-5 text-[#00317A]" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Firm Branding</div>
              <div className="font-serif text-xl text-[#00317A]">Firm Logo</div>
              <p className="text-xs text-[#4B5563] mt-1">
                Optional. Prints beside the Domini shield on certificates and QR posters. PNG or JPEG, under 500 KB.
              </p>
            </div>
          </div>
          {firmLogo.has_logo ? (
            <div className="flex items-center gap-4">
              <div className="w-32 h-24 border border-[#E2E8F0] rounded-sm p-2 bg-white flex items-center justify-center">
                <img src={firmLogo.firm_logo_data} alt="Firm logo" className="max-w-full max-h-full object-contain" data-testid="firm-logo-preview" />
              </div>
              <div className="flex-1">
                <div className="text-xs text-[#146334] mb-2">Logo uploaded — appears on new PDF exports.</div>
                <div className="flex gap-2">
                  <Button type="button" size="sm" variant="outline"
                    onClick={() => firmLogoInputRef.current?.click()}
                    className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white"
                    data-testid="replace-firm-logo-btn">
                    <Upload className="w-3.5 h-3.5 mr-1" /> Replace
                  </Button>
                  <Button type="button" size="sm" variant="ghost"
                    onClick={removeFirmLogo}
                    className="rounded-sm text-[#991B1B] hover:bg-[#FDECEC]"
                    data-testid="remove-firm-logo-btn">
                    <Trash2 className="w-3.5 h-3.5 mr-1" /> Remove
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <Button type="button"
              onClick={() => firmLogoInputRef.current?.click()}
              disabled={logoUploading}
              className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white w-full"
              data-testid="upload-firm-logo-btn">
              <Upload className="w-4 h-4 mr-2" />
              {logoUploading ? "Uploading..." : "Upload firm logo"}
            </Button>
          )}
          <input ref={firmLogoInputRef} type="file" accept="image/png,image/jpeg"
            onChange={onFirmLogoPick} className="hidden" data-testid="firm-logo-file-input" />
        </Card>

        <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="password-card">
          <div className="flex items-start gap-3 mb-4 pb-4 border-b border-[#E2E8F0]">
            <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
              <KeyRound className="w-5 h-5 text-[#00317A]" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Security</div>
              <div className="font-serif text-xl text-[#00317A]">Change Password</div>
              <p className="text-xs text-[#4B5563] mt-1">Minimum 8 characters. You stay signed in on this device.</p>
            </div>
          </div>
          <form onSubmit={changePassword} className="space-y-3">
            <div>
              <Label className="text-xs">Current password</Label>
              <Input type="password" required value={pwd.current_password}
                onChange={(e) => setPwd({...pwd, current_password: e.target.value})}
                className="mt-1 rounded-sm" data-testid="pwd-current" />
            </div>
            <div>
              <Label className="text-xs">New password</Label>
              <Input type="password" required minLength={8} value={pwd.new_password}
                onChange={(e) => setPwd({...pwd, new_password: e.target.value})}
                className="mt-1 rounded-sm" data-testid="pwd-new" />
            </div>
            <div>
              <Label className="text-xs">Confirm new password</Label>
              <Input type="password" required minLength={8} value={pwd.confirm}
                onChange={(e) => setPwd({...pwd, confirm: e.target.value})}
                className="mt-1 rounded-sm" data-testid="pwd-confirm" />
            </div>
            <Button type="submit" disabled={pwdSaving}
              className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white w-full"
              data-testid="change-password-btn">
              {pwdSaving ? "Updating..." : "Update password"}
            </Button>
          </form>
        </Card>
      </div>

      {/* Firm Profile — extended firm details for the certificate footer */}
      <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="firm-profile-card">
        <div className="flex items-start gap-3 mb-4 pb-4 border-b border-[#E2E8F0]">
          <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
            <Building2 className="w-5 h-5 text-[#00317A]" />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Branding</div>
            <div className="font-serif text-xl text-[#00317A]">Firm Profile</div>
            <p className="text-xs text-[#4B5563] mt-1">
              Optional. Address, phone, website, and license number print in the certificate footer.
            </p>
          </div>
        </div>
        <form onSubmit={saveFirmProfile} className="space-y-4">
          <div>
            <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Firm Name</Label>
            <Input
              value={form.firm_name}
              onChange={(e) => setForm({ ...form, firm_name: e.target.value })}
              placeholder="Domini Business Services"
              className="mt-1 rounded-sm"
              data-testid="firm-name-input"
            />
          </div>
          <div>
            <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Firm Address</Label>
            <Textarea
              rows={2}
              value={form.firm_address}
              onChange={(e) => setForm({ ...form, firm_address: e.target.value })}
              placeholder="123 Main St, Sacramento CA 95814"
              className="mt-1 rounded-sm"
              data-testid="firm-address-input"
            />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Phone</Label>
              <Input
                value={form.firm_phone}
                onChange={(e) => setForm({ ...form, firm_phone: e.target.value })}
                placeholder="(916) 555-0100"
                className="mt-1 rounded-sm font-mono-plex"
                data-testid="firm-phone-input"
              />
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">Website</Label>
              <Input
                value={form.firm_website}
                onChange={(e) => setForm({ ...form, firm_website: e.target.value })}
                placeholder="dominibusservices.com"
                className="mt-1 rounded-sm font-mono-plex"
                data-testid="firm-website-input"
              />
            </div>
          </div>
          <div>
            <Label className="text-[10px] uppercase tracking-widest text-[#4B5563]">License / Registration Number</Label>
            <Input
              value={form.firm_license_number}
              onChange={(e) => setForm({ ...form, firm_license_number: e.target.value })}
              placeholder="e.g., NP-CA-2026-0001"
              className="mt-1 rounded-sm font-mono-plex"
              data-testid="firm-license-input"
            />
          </div>
          <Button
            type="submit"
            disabled={firmSaving}
            className="rounded-sm bg-[#00317A] hover:bg-[#00265E] text-white"
            data-testid="save-firm-profile-btn"
          >
            {firmSaving ? "Saving..." : "Save Firm Profile"}
          </Button>
        </form>
      </Card>

      {/* Delivery Bounce Digest */}
      <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="bounce-digest-card">
        <div className="flex items-start justify-between gap-4 mb-4 pb-4 border-b border-[#E2E8F0] flex-wrap">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-sm bg-[#FDECEC] flex items-center justify-center shrink-0">
              <MailX className="w-5 h-5 text-[#991B1B]" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Delivery</div>
              <div className="font-serif text-xl text-[#00317A]">Bounce Digest</div>
              <p className="text-xs text-[#4B5563] mt-1 max-w-xl">
                A morning email lists any certificate emails that failed overnight, each with a one-click <b>Open &amp; resend</b> link.
              </p>
            </div>
          </div>
          <Button
            type="button"
            onClick={sendBouncePreview}
            disabled={bouncePreview || (bounces?.total ?? 0) === 0}
            variant="outline"
            className="rounded-sm border-[#00317A] text-[#00317A] hover:bg-[#00317A] hover:text-white"
            data-testid="bounce-preview-btn"
          >
            {bouncePreview ? "Sending..." : "Send me a preview"}
          </Button>
        </div>
        {bounces == null ? (
          <div className="text-xs text-[#4B5563]">Loading recent delivery...</div>
        ) : bounces.total === 0 ? (
          <div className="p-4 bg-[#F1FAF3] rounded-sm border border-[#B7E0C1] text-sm text-[#146334]" data-testid="bounce-empty">
            No bounced certificate emails in the last 7 days. Delivery looks healthy.
          </div>
        ) : (
          <div className="space-y-2" data-testid="bounce-list">
            <div className="text-xs text-[#4B5563] font-mono-plex">
              {bounces.total} bounce{bounces.total === 1 ? "" : "s"} · last 7 days
            </div>
            {bounces.bounces.slice(0, 5).map((b) => (
              <div key={b.id} className="p-3 border border-[#F4B4B4] bg-[#FDECEC] rounded-sm text-xs" data-testid={`bounce-row-${b.id}`}>
                <div className="flex justify-between gap-3 flex-wrap">
                  <span className="font-mono-plex text-[#111827]">{b.recipient || "—"}</span>
                  <span className="text-[#4B5563]">{b.sent_at?.slice(0, 10) || ""}</span>
                </div>
                <div className="text-[#991B1B] mt-1">{b.error_msg || "delivery failed"}</div>
              </div>
            ))}
            {bounces.total > 5 && (
              <div className="text-xs text-[#4B5563] text-center pt-1">
                and {bounces.total - 5} more — resend individually from each journal entry.
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Renewal Checklist */}
      {checklist && (
        <Card className="p-6 border-[#E2E8F0] rounded-sm shadow-none bg-white" data-testid="renewal-checklist-card">
          <div className="flex items-start justify-between mb-4 pb-4 border-b border-[#E2E8F0]">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-sm bg-[#EEF2FB] flex items-center justify-center shrink-0">
                {checklist.unlocked
                  ? <ListChecks className="w-5 h-5 text-[#00317A]" />
                  : <Lock className="w-5 h-5 text-[#4B5563]" />}
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-1">Countdown</div>
                <div className="font-serif text-2xl text-[#00317A]">Renewal Checklist</div>
                <p className="text-xs text-[#4B5563] mt-1 max-w-xl">
                  Tailored to <span className="font-medium text-[#111827]">{checklist.state}</span>. Unlocks
                  once you're within 30 days of your commission expiration.
                </p>
              </div>
            </div>
            {checklist.state_customized && (
              <div className="text-right mr-4" data-testid="checklist-state-badge">
                <div className="text-[10px] uppercase tracking-widest text-[#DF0D1B] bg-[#FDECEC] border border-[#F4B4B4] px-2 py-0.5 rounded-sm inline-block">
                  State-Specific
                </div>
              </div>
            )}
            {checklist.unlocked && (
              <div className="text-right">
                <div className="font-mono-plex text-2xl text-[#00317A]" data-testid="checklist-progress">
                  {checklist.completed_count}/{checklist.total_count}
                </div>
                <div className="text-[10px] uppercase tracking-widest text-[#4B5563]">Done</div>
              </div>
            )}
          </div>

          {!checklist.unlocked ? (
            <div className="p-5 bg-[#F4F7F9] rounded-sm border border-[#E2E8F0] text-sm text-[#4B5563]" data-testid="checklist-locked-notice">
              <div className="flex items-center gap-2 text-[#00317A] font-medium mb-1">
                <Lock className="w-4 h-4" /> Locked
              </div>
              {checklist.days_left != null ? (
                <>Your renewal checklist opens automatically {checklist.days_left - 30} days from now — when your commission is 30 days from expiring.</>
              ) : (
                <>Add your commission expiration date above to schedule your renewal checklist.</>
              )}
            </div>
          ) : (
            <ul className="space-y-3" data-testid="checklist-items">
              {checklist.items.map((it, idx) => (
                <li key={it.id}
                    data-testid={`checklist-item-${it.id}`}
                    className={`flex items-start gap-3 p-3 rounded-sm border transition-colors ${
                      it.completed
                        ? "bg-[#F1FAF3] border-[#B7E0C1]"
                        : "bg-white border-[#E2E8F0] hover:border-[#00317A]/30"
                    }`}>
                  <Checkbox
                    checked={it.completed}
                    onCheckedChange={(v) => toggleChecklistItem(it.id, !!v)}
                    className="mt-0.5 data-[state=checked]:bg-[#00317A] data-[state=checked]:border-[#00317A]"
                    data-testid={`checklist-checkbox-${it.id}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-mono-plex text-[10px] text-[#4B5563]">{String(idx+1).padStart(2,'0')}</span>
                      <span className={`text-sm ${it.completed ? "text-[#146334] line-through" : "text-[#111827]"} font-medium`}>
                        {it.title}
                      </span>
                      {it.state_specific && (
                        <span className="text-[9px] uppercase tracking-widest text-[#DF0D1B] bg-[#FDECEC] px-1.5 py-0.5 rounded-sm border border-[#F4B4B4]">
                          {checklist.state}
                        </span>
                      )}
                      {it.custom && (
                        <span className="text-[9px] uppercase tracking-widest text-[#00317A] bg-[#EEF2FB] px-1.5 py-0.5 rounded-sm border border-[#C7D4EC]">
                          Your step
                        </span>
                      )}
                    </div>
                    {it.detail && <div className="text-xs text-[#4B5563] mt-1 ml-6">{it.detail}</div>}
                  </div>
                  {it.custom && (
                    <button
                      type="button"
                      onClick={() => deleteCustomStep(it.id)}
                      className="text-[#991B1B] hover:bg-[#FDECEC] rounded-sm p-1 shrink-0"
                      data-testid={`checklist-delete-${it.id}`}
                      title="Remove this custom step"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* Add-your-own-step composer — always available so notaries can
              start building the list even before the 30-day unlock. */}
          {checklist.unlocked && (
            <form onSubmit={addCustomStep} className="mt-6 pt-5 border-t border-[#E2E8F0] space-y-2" data-testid="add-custom-step-form">
              <div className="text-[10px] uppercase tracking-widest text-[#4B5563]">Add your own step</div>
              <div className="flex flex-col md:flex-row gap-2">
                <Input
                  value={newStep.title}
                  onChange={(e) => setNewStep({ ...newStep, title: e.target.value })}
                  placeholder="e.g. Order new stamp from Notary Rotary"
                  maxLength={120}
                  className="rounded-sm h-10 flex-1"
                  data-testid="custom-step-title-input"
                />
                <Input
                  value={newStep.detail}
                  onChange={(e) => setNewStep({ ...newStep, detail: e.target.value })}
                  placeholder="Optional note"
                  maxLength={280}
                  className="rounded-sm h-10 flex-1"
                  data-testid="custom-step-detail-input"
                />
                <Button
                  type="submit"
                  disabled={addingStep || !newStep.title.trim()}
                  className="rounded-sm h-10 bg-[#00317A] hover:bg-[#00265E] text-white"
                  data-testid="add-custom-step-btn"
                >
                  <Plus className="w-4 h-4 mr-1" />
                  {addingStep ? "Adding..." : "Add step"}
                </Button>
              </div>
              <div className="text-[11px] text-[#4B5563]">Up to 20 custom steps · they'll count toward your progress total.</div>
            </form>
          )}
        </Card>
      )}
    </div>
  );
}
