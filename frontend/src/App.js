import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Journal from "@/pages/Journal";
import Clients from "@/pages/Clients";
import DocumentTypes from "@/pages/DocumentTypes";
import Settings from "@/pages/Settings";
import Intakes from "@/pages/Intakes";
import SignerIntake from "@/pages/SignerIntake";
import Roster from "@/pages/Roster";
import Billing from "@/pages/Billing";

function ProtectedRoute({ children }) {
  const { user, checked } = useAuth();
  if (!checked) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F4F7F9]">
        <div className="text-sm text-[#4B5563] font-mono-plex uppercase tracking-widest">Loading...</div>
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function PublicOnly({ children }) {
  const { user, checked } = useAuth();
  if (!checked) return null;
  if (user) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <div className="App">
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/intake/:token" element={<SignerIntake />} />
            <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
            <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
              <Route index element={<Dashboard />} />
              <Route path="journal" element={<Journal />} />
              <Route path="clients" element={<Clients />} />
              <Route path="intakes" element={<Intakes />} />
              <Route path="document-types" element={<DocumentTypes />} />
              <Route path="settings" element={<Settings />} />
              <Route path="billing" element={<Billing />} />
              <Route path="admin/roster" element={<Roster />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        <Toaster position="top-right" richColors />
      </AuthProvider>
    </div>
  );
}
