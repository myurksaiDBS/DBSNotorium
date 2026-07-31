import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useNavigate, Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { ScrollText } from "lucide-react";
import { toast } from "sonner";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState("login"); // login | register
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { register } = useAuth();

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
      <div className="hidden lg:flex lg:col-span-2 relative overflow-hidden bg-[#0A192F] text-white">
        <div
          className="absolute inset-0 opacity-30"
          style={{
            backgroundImage: `url('https://images.unsplash.com/photo-1631557776808-91908aba7ca0?crop=entropy&cs=srgb&fm=jpg&q=85&w=1200')`,
            backgroundSize: "cover", backgroundPosition: "center",
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0A192F] via-[#0A192F]/70 to-transparent" />
        <div className="relative z-10 p-12 flex flex-col justify-between w-full">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-sm bg-[#B49A5A] flex items-center justify-center">
              <ScrollText className="w-5 h-5 text-[#0A192F]" />
            </div>
            <div>
              <div className="font-serif text-xl leading-tight">Notarium</div>
              <div className="text-[10px] uppercase tracking-[0.25em] text-white/60">Journal &amp; Log</div>
            </div>
          </div>
          <div>
            <h2 className="font-serif text-4xl leading-tight mb-4">
              The record of trust,<br/>bound to your seal.
            </h2>
            <p className="text-white/70 text-sm max-w-md">
              A modern journal for the modern notary — signer records, document logs,
              digital signatures, and audit-ready PDF exports in one place.
            </p>
            <div className="mt-8 border-l-2 border-[#B49A5A] pl-4 text-xs text-white/60 font-mono-plex">
              COMMISSION · SIGNER · DOCUMENT · SIGNATURE
            </div>
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="lg:col-span-3 flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-md">
          <h1 className="font-serif text-4xl mb-2">
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
              className="w-full h-11 bg-[#0A192F] hover:bg-[#12253F] rounded-sm text-white transition-colors">
              {loading ? "Please wait..." : mode === "login" ? "Sign In" : "Create Account"}
            </Button>
          </form>

          <div className="mt-6 text-sm text-[#4B5563]">
            {mode === "login" ? (
              <>New here?{" "}
                <button type="button" data-testid="toggle-register" className="text-[#0A192F] font-medium underline underline-offset-2 hover:text-[#B49A5A]" onClick={() => setMode("register")}>Create an account</button>
              </>
            ) : (
              <>Already registered?{" "}
                <button type="button" data-testid="toggle-login" className="text-[#0A192F] font-medium underline underline-offset-2 hover:text-[#B49A5A]" onClick={() => setMode("login")}>Sign in</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
