import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import {
  LayoutDashboard, BookOpen, Users, FileType2, Settings, LogOut, ScrollText,
} from "lucide-react";
import { Button } from "@/components/ui/button";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, testid: "nav-dashboard" },
  { to: "/journal", label: "Journal", icon: BookOpen, testid: "nav-journal" },
  { to: "/clients", label: "Signers", icon: Users, testid: "nav-clients" },
  { to: "/document-types", label: "Document Types", icon: FileType2, testid: "nav-doctypes" },
  { to: "/settings", label: "Commission", icon: Settings, testid: "nav-settings" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex bg-[#F4F7F9]">
      {/* Sidebar */}
      <aside className="w-64 bg-[#0A192F] text-white flex flex-col fixed inset-y-0 left-0">
        <div className="px-6 pt-8 pb-6 border-b border-white/10">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-sm bg-[#B49A5A] flex items-center justify-center">
              <ScrollText className="w-5 h-5 text-[#0A192F]" />
            </div>
            <div>
              <div className="font-serif text-lg leading-tight">Notarium</div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-white/60">Journal &amp; Log</div>
            </div>
          </div>
        </div>
        <nav className="flex-1 py-6 px-3 space-y-1">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              data-testid={n.testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-sm text-sm transition-colors duration-200 ${
                  isActive
                    ? "bg-white/10 text-white border-l-2 border-[#B49A5A] pl-[10px]"
                    : "text-white/70 hover:bg-white/5 hover:text-white"
                }`
              }
            >
              <n.icon className="w-4 h-4" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-4 border-t border-white/10">
          <div className="mb-3 text-xs">
            <div className="font-medium truncate" data-testid="sidebar-user-name">{user?.name}</div>
            <div className="text-white/50 truncate">{user?.email}</div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            data-testid="logout-btn"
            className="w-full justify-start text-white/80 hover:text-white hover:bg-white/10"
            onClick={async () => { await logout(); navigate("/login"); }}
          >
            <LogOut className="w-4 h-4 mr-2" /> Sign Out
          </Button>
        </div>
      </aside>

      {/* Content */}
      <main className="flex-1 ml-64">
        <div className="max-w-[1400px] mx-auto px-10 py-10">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
