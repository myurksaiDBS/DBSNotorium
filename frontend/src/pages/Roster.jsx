import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Users, Crown, UserPlus } from "lucide-react";
import InviteNotaryDialog from "@/components/InviteNotaryDialog";

const STATUS_STYLE = {
  trial:     "bg-[#FEF9EC] text-[#7A5B00] border-[#F0D28A]",
  active:    "bg-[#E8F5EC] text-[#146334] border-[#B7E0C1]",
  past_due:  "bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]",
  canceled:  "bg-[#F4F7F9] text-[#4B5563] border-[#E2E8F0]",
};

export default function Roster() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);

  useEffect(() => {
    api.get("/admin/roster")
      .then((r) => setData(r.data))
      .catch((e) => setErr(e.response?.status === 403 ? "Admin only" : "Failed to load roster"));
  }, []);

  if (err) {
    return <div className="text-sm text-[#991B1B] p-6 bg-[#FDECEC] border border-[#F4B4B4] rounded-sm">{err}</div>;
  }
  if (!data) return <div className="text-sm text-[#4B5563]">Loading roster...</div>;

  return (
    <div className="space-y-6" data-testid="roster-page">
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2 flex items-center gap-2">
            <Crown className="w-3 h-3" /> Super Admin
          </div>
          <h1 className="font-serif text-4xl text-[#00317A]">Notary Roster</h1>
          <p className="text-sm text-[#4B5563] mt-1">
            {data.total} notaries using Notarium under Domini's umbrella
          </p>
        </div>
        <Button
          onClick={() => setInviteOpen(true)}
          className="rounded-sm bg-[#DF0D1B] hover:bg-[#B70915] text-white"
          data-testid="open-invite-btn"
        >
          <UserPlus className="w-4 h-4 mr-2" /> Invite Notary
        </Button>
      </div>

      <InviteNotaryDialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        defaultFirm={user?.firm_name || ""}
        defaultState={user?.state || ""}
      />

      <Card className="border-[#E2E8F0] rounded-sm shadow-none bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-[#F4F7F9] hover:bg-[#F4F7F9]">
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Notary</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Firm</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">State</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Subscription</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] text-right">Entries</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563] text-right">Signers</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-[#4B5563]">Last Activity</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.notaries.map((n, i) => (
              <TableRow key={n.id} data-testid={`roster-row-${n.id}`} data-zebra={i % 2 ? "odd" : "even"} className="row-anim" style={{ animationDelay: `${Math.min(i*20, 400)}ms` }}>
                <TableCell>
                  <div className="font-medium text-[#00317A] flex items-center gap-2">
                    {n.name}
                    {n.role === "admin" && <Badge variant="outline" className="rounded-sm bg-[#FDECEC] text-[#991B1B] border-[#F4B4B4]">Admin</Badge>}
                  </div>
                  <div className="text-xs text-[#4B5563] font-mono-plex">{n.email}</div>
                </TableCell>
                <TableCell className="text-sm">{n.firm_name || <span className="text-[#4B5563]">—</span>}</TableCell>
                <TableCell className="text-sm text-[#4B5563]">{n.state || "—"}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={`rounded-sm border ${STATUS_STYLE[n.subscription_status] || STATUS_STYLE.trial}`}>
                    {n.subscription_status} · {n.subscription_plan}
                  </Badge>
                </TableCell>
                <TableCell className="text-right font-mono-plex">{n.entry_count}</TableCell>
                <TableCell className="text-right font-mono-plex">{n.client_count}</TableCell>
                <TableCell className="text-xs font-mono-plex text-[#4B5563]">
                  {n.last_activity ? n.last_activity.slice(0, 10) : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
