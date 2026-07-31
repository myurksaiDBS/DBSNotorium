import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api, formatApiError } from "@/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { BellRing, Send, Mail, ListChecks, Lock } from "lucide-react";
import { toast } from "sonner";

export default function Settings() {
  const { user, updateProfile } = useAuth();
  const [form, setForm] = useState({
    name: user?.name || "",
    commission_number: user?.commission_number || "",
    commission_expires: user?.commission_expires || "",
    state: user?.state || "",
    alerts_enabled: user?.alerts_enabled !== false,
  });
  const [saving, setSaving] = useState(false);
  const [alertsStatus, setAlertsStatus] = useState(null);
  const [testing, setTesting] = useState(false);
  const [checklist, setChecklist] = useState(null);

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
  useEffect(() => { loadStatus(); loadChecklist(); }, []);

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
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono-plex text-[10px] text-[#4B5563]">{String(idx+1).padStart(2,'0')}</span>
                      <span className={`text-sm ${it.completed ? "text-[#146334] line-through" : "text-[#111827]"} font-medium`}>
                        {it.title}
                      </span>
                    </div>
                    {it.detail && <div className="text-xs text-[#4B5563] mt-1 ml-6">{it.detail}</div>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
