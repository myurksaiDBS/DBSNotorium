import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export default function Login() {
  const { login, register } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    const res = mode === "login"
      ? await login(email, password)
      : await register(name, email, password);
    setLoading(false);
    if (!res.ok) { setError(res.error); toast.error(res.error); return; }
    toast.success(mode === "login" ? "Welcome back" : "Account created");
    nav("/");
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-5 bg-white">
      {/* Left panel */}
      <div className="hidden lg:flex lg:col-span-2 relative overflow-hidden bg-[#00317A] text-white">
        <div
          className="absolute inset-0 opacity-25"
          style={{
            backgroundImage: `url('https://images.unsplash.com/photo-1631557776808-91908aba7ca0?crop=entropy&cs=srgb&fm=jpg&q=85&w=1200')`,
            backgroundSize: "cover", backgroundPosition: "center",
          }}
        />
        <div className="absolute inset-0 bg-[#00317A]/70" />
        <div className="relative z-10 p-12 flex flex-col justify-between w-full">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-sm bg-white p-1.5 shadow-lg">
              <img src="/domini-logo.png" alt="Domini Business Services" className="w-full h-full object-contain" />
            </div>
            <div>
              <div className="font-serif text-2xl leading-tight">Notarium</div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-white/70">Domini Business Services</div>
            </div>
          </div>
          <div>
            <div className="h-1 w-16 bg-[#DF0D1B] mb-6" />
            <h2 className="font-serif text-4xl leading-tight mb-4">
              The record of trust,<br/>bound to your seal.
            </h2>
            <p className="text-white/75 text-sm max-w-md leading-relaxed">
              The professional notary journal by Domini Business Services.
              Signer records, document logs, digital signatures, and audit-ready
              PDF exports — all in one authoritative place.
            </p>
            <div className="mt-8 border-l-2 border-[#D4AF37] pl-4 text-[10px] text-white/60 font-mono-plex uppercase tracking-widest">
              Commission · Signer · Document · Signature
            </div>
          </div>
          <div className="text-[10px] uppercase tracking-[0.25em] text-white/40 font-mono-plex">
            A Division of YFI Enterprises, LLC
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="lg:col-span-3 flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden mb-8 flex items-center gap-3">
            <div className="w-12 h-12 rounded-sm bg-white border border-[#E2E8F0] p-1">
              <img src="/domini-logo.png" alt="Domini" className="w-full h-full object-contain" />
            </div>
            <div>
              <div className="font-serif text-lg text-[#00317A]">Notarium</div>
              <div className="text-[9px] uppercase tracking-[0.25em] text-[#4B5563]">Domini Business Services</div>
            </div>
          </div>

          <div className="h-1 w-12 bg-[#DF0D1B] mb-5" />
          <h1 className="font-serif text-4xl mb-2 text-[#00317A]">
            {mode === "login" ? "Sign in to your journal" : "Create your notary account"}
          </h1>
          <p className="text-sm text-[#4B5563] mb-8">
            {mode === "login"
              ? "Enter your credentials to access your log."
              : "Register once — your entries remain private to you."}
          </p>

          <form onSubmit={submit} className="space-y-5">
            {mode === "register" && (
              <div>
                <Label htmlFor="name" className="text-xs uppercase tracking-wider text-[#4B5563]">Full name</Label>
                <Input id="name" data-testid="register-name-input"
                  value={name} onChange={(e) => setName(e.target.value)}
                  required className="mt-1.5 h-11 rounded-sm" />
              </div>
            )}
            <div>
              <Label htmlFor="email" className="text-xs uppercase tracking-wider text-[#4B5563]">Email</Label>
              <Input id="email" data-testid="login-email-input" type="email"
                value={email} onChange={(e) => setEmail(e.target.value)}
                required className="mt-1.5 h-11 rounded-sm" />
            </div>
            <div>
              <Label htmlFor="password" className="text-xs uppercase tracking-wider text-[#4B5563]">Password</Label>
              <Input id="password" data-testid="login-password-input" type="password"
                value={password} onChange={(e) => setPassword(e.target.value)}
                required className="mt-1.5 h-11 rounded-sm" />
            </div>

            {error && <div className="text-sm text-[#991B1B] bg-red-50 border border-red-200 rounded-sm px-3 py-2" data-testid="auth-error">{error}</div>}

            <Button type="submit" disabled={loading}
              data-testid="auth-submit-btn"
              className="w-full h-11 bg-[#00317A] hover:bg-[#00265E] rounded-sm text-white transition-colors">
              {loading ? "Please wait..." : mode === "login" ? "Sign In" : "Create Account"}
            </Button>
          </form>

          <div className="mt-6 text-sm text-[#4B5563]">
            {mode === "login" ? (
              <>New here?{" "}
                <button type="button" data-testid="toggle-register" className="text-[#DF0D1B] font-medium underline underline-offset-2 hover:text-[#00317A]" onClick={() => setMode("register")}>Create an account</button>
              </>
            ) : (
              <>Already registered?{" "}
                <button type="button" data-testid="toggle-login" className="text-[#DF0D1B] font-medium underline underline-offset-2 hover:text-[#00317A]" onClick={() => setMode("login")}>Sign in</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
