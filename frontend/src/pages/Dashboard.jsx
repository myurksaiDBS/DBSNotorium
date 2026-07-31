import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, LineChart, Line, PieChart, Pie, Cell, Legend } from "recharts";
import { BookOpen, Users, DollarSign, TrendingUp } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const NAVY = "#00317A";
const GOLD = "#D4AF37";
const RED = "#DF0D1B";
const PIE_COLORS = ["#00317A", "#DF0D1B", "#D4AF37", "#3F6B5C", "#5C4B7A", "#8B5A3C"];

const StatCard = ({ label, value, icon: Icon, sub, testid }) => (
  <Card className="p-6 bg-white border-[#E2E8F0] rounded-sm shadow-none" data-testid={testid}>
    <div className="flex items-start justify-between">
      <div>
        <div className="text-[11px] uppercase tracking-widest text-[#4B5563] mb-2">{label}</div>
        <div className="font-serif text-3xl text-[#00317A]">{value}</div>
        {sub && <div className="mt-1 text-xs text-[#4B5563]">{sub}</div>}
      </div>
      <div className="w-10 h-10 bg-[#F4F7F9] rounded-sm flex items-center justify-center">
        <Icon className="w-4 h-4 text-[#00317A]" />
      </div>
    </div>
  </Card>
);

export default function Dashboard() {
  const { user } = useAuth();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.get("/stats/dashboard").then(r => setStats(r.data)).catch(() => {});
  }, []);

  if (!stats) {
    return <div className="text-[#4B5563] text-sm">Loading dashboard...</div>;
  }

  return (
    <div className="space-y-8" data-testid="dashboard-page">
      {/* Header */}
      <div className="flex items-end justify-between border-b border-[#E2E8F0] pb-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.25em] text-[#DF0D1B] mb-2">Notary Journal</div>
          <h1 className="font-serif text-4xl text-[#00317A]">Good day, {user?.name?.split(" ")[0]}</h1>
          <p className="text-sm text-[#4B5563] mt-1">
            {new Date().toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
          </p>
        </div>
        <div className="text-right">
          <div className="text-[11px] uppercase tracking-widest text-[#4B5563]">Commission</div>
          <div className="font-mono-plex text-sm text-[#00317A]">
            {user?.commission_number || "— not set —"}
          </div>
          {user?.state && <div className="text-xs text-[#4B5563]">{user.state}</div>}
        </div>
      </div>

      {/* Stat grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
        <StatCard label="Entries this month" value={stats.month_entries} sub={`${stats.total_entries} lifetime`} icon={BookOpen} testid="stat-month-entries" />
        <StatCard label="Revenue this month" value={`$${stats.month_revenue.toFixed(2)}`} sub={`$${stats.total_revenue.toFixed(2)} lifetime`} icon={DollarSign} testid="stat-month-revenue" />
        <StatCard label="Total Signers" value={stats.total_clients} icon={Users} testid="stat-total-clients" />
        <StatCard label="Avg Fee" value={stats.total_entries ? `$${(stats.total_revenue/stats.total_entries).toFixed(2)}` : "$0.00"} icon={TrendingUp} testid="stat-avg-fee" />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="lg:col-span-2 p-6 bg-white border-[#E2E8F0] rounded-sm shadow-none">
          <div className="flex items-baseline justify-between mb-5">
            <div>
              <h3 className="font-serif text-xl text-[#00317A]">Monthly Activity</h3>
              <p className="text-xs text-[#4B5563] mt-0.5">Last 6 months — entries &amp; revenue</p>
            </div>
          </div>
          <div style={{ width: "100%", height: 280 }}>
            <ResponsiveContainer>
              <BarChart data={stats.monthly_series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#4B5563", fontFamily: "IBM Plex Sans" }} axisLine={{ stroke: "#E2E8F0" }} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#4B5563", fontFamily: "IBM Plex Sans" }} axisLine={{ stroke: "#E2E8F0" }} tickLine={false} />
                <Tooltip contentStyle={{ borderRadius: 2, border: "1px solid #E2E8F0", fontFamily: "IBM Plex Sans", fontSize: 12 }}
                  formatter={(v, name) => name === "revenue" ? [`$${v}`, "Revenue"] : [v, "Entries"]} />
                <Bar dataKey="count" fill={NAVY} radius={[2,2,0,0]} name="count" />
                <Bar dataKey="revenue" fill={RED} radius={[2,2,0,0]} name="revenue" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="p-6 bg-white border-[#E2E8F0] rounded-sm shadow-none">
          <h3 className="font-serif text-xl text-[#00317A] mb-1">Notarization Types</h3>
          <p className="text-xs text-[#4B5563] mb-4">Distribution across records</p>
          {stats.type_breakdown.length === 0 ? (
            <div className="text-sm text-[#4B5563] py-10 text-center">No entries yet.</div>
          ) : (
            <div style={{ width: "100%", height: 240 }}>
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={stats.type_breakdown} dataKey="count" nameKey="type" outerRadius={80} innerRadius={45}>
                    {stats.type_breakdown.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ borderRadius: 2, border: "1px solid #E2E8F0", fontFamily: "IBM Plex Sans", fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11, fontFamily: "IBM Plex Sans" }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
