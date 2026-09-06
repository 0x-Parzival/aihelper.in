import crypto from "node:crypto";
import express from "express";
import { fromNodeHeaders, toNodeHandler } from "better-auth/node";
import { auth } from "./auth.mjs";

const app = express();
const port = Number(process.env.BETTER_AUTH_PORT || 8002);
const bridgeSecret = process.env.AUTH_BRIDGE_SECRET;
if (!bridgeSecret || bridgeSecret.length < 32) throw new Error("AUTH_BRIDGE_SECRET must be a unique random value of at least 32 characters");

function callbackPath(value) {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") ? value : "/spiritualai";
}
function page(title, content) {
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/assets/favicon.ico" sizes="any"><link rel="icon" href="/assets/favicon-32x32.png" type="image/png" sizes="32x32"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.png"><title>${title} · AI Helper</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#eff0f2;color:#111216;font:16px/1.45 Arial,sans-serif}.card{width:min(420px,calc(100% - 32px));padding:30px;border-radius:22px;background:#fff;box-shadow:0 20px 60px #182d4e20}h1{margin:0 0 8px;font-size:28px}p{color:#626672}form{display:grid;gap:13px;margin-top:22px}label{display:grid;gap:5px;font-size:13px;font-weight:bold}input,button{font:inherit;padding:12px;border:1px solid #d8d9de;border-radius:10px}button{border:0;background:#111216;color:#fff;font-weight:bold;cursor:pointer}button.secondary{background:#e8ebf0;color:#24262c}.password-row{display:flex;gap:8px}.password-row input{min-width:0;flex:1}.password-row button{white-space:nowrap;padding:10px}.notice{margin-top:14px;padding:12px;border-radius:10px;background:#fff4d7;color:#735100;font-size:14px}.notice button{margin-top:8px;background:#76542f}.message{min-height:1.2em;color:#b3372f;font-size:14px}</style><main class="card">${content}</main></html>`;
}

app.get("/auth/login", (req, res) => {
  const next = callbackPath(req.query.next);
  res.type("html").send(page("Sign in", `<h1>Sign in</h1><p>Use the email address and password provided by your business administrator.</p><form id="form"><label>Email<input id="email" name="email" type="email" autocomplete="email" required></label><label>Password<span class="password-row"><input id="password" name="password" type="password" autocomplete="current-password" required><button class="secondary" id="show-password" type="button" aria-label="Show password">Show</button></span></label><button>Sign in</button><div class="message" id="message"></div></form><section id="verify-notice" class="notice" hidden>Your email still needs verification.<br><button id="resend-verification" type="button">Send a new verification email</button></section><p><a href="/auth/forgot-password">Forgot password?</a></p><script>const f=document.querySelector('#form'),m=document.querySelector('#message'),email=document.querySelector('#email'),password=document.querySelector('#password'),show=document.querySelector('#show-password'),notice=document.querySelector('#verify-notice'),resend=document.querySelector('#resend-verification');show.addEventListener('click',()=>{const visible=password.type==='text';password.type=visible?'password':'text';show.textContent=visible?'Show':'Hide';show.setAttribute('aria-label',visible?'Show password':'Hide password')});f.addEventListener('submit',async e=>{e.preventDefault();m.textContent='';notice.hidden=true;const d=Object.fromEntries(new FormData(f));const r=await fetch('/auth/sign-in/email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...d,rememberMe:true})});const x=await r.json().catch(()=>({}));if(!r.ok){m.textContent=x.message||'Unable to sign in.';if(r.status===403||/not verified/i.test(m.textContent))notice.hidden=false;return}location.assign(${JSON.stringify(next)})});resend.addEventListener('click',async()=>{if(!email.reportValidity())return;resend.disabled=true;const r=await fetch('/auth/send-verification-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email.value,callbackURL:${JSON.stringify(next)}})});m.style.color=r.ok?'#176b47':'#b3372f';m.textContent=r.ok?'A verification email is on its way.':'Unable to send verification email right now.';resend.disabled=false})</script>`));
});

app.get("/auth/forgot-password", (_req, res) => res.type("html").send(page("Reset password", `<h1>Reset password</h1><p>Enter your email and we’ll send a reset link if an account exists.</p><form id="form"><label>Email<input name="email" type="email" autocomplete="email" required></label><button>Send reset link</button><div class="message" id="message"></div></form><p><a href="/auth/login">Back to sign in</a></p><script>const f=document.querySelector('#form'),m=document.querySelector('#message');f.addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/auth/request-password-reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:new FormData(f).get('email'),redirectTo:location.origin+'/auth/reset-password'})});m.style.color='#176b47';m.textContent=r.ok?'If that account exists, a reset link is on its way.':'Unable to request a reset right now.'})</script>`)));

app.get("/auth/reset-password", (req, res) => {
  const token = typeof req.query.token === "string" ? req.query.token : "";
  res.type("html").send(page("Choose a new password", `<h1>Choose a new password</h1><p>Your new password must be at least 12 characters.</p><form id="form"><label>New password<span class="password-row"><input id="password" name="password" type="password" minlength="12" autocomplete="new-password" required><button class="secondary" id="show-password" type="button" aria-label="Show password">Show</button></span></label><button>Update password</button><div class="message" id="message"></div></form><script>const f=document.querySelector('#form'),m=document.querySelector('#message'),password=document.querySelector('#password'),show=document.querySelector('#show-password'),token=${JSON.stringify(token)};show.addEventListener('click',()=>{const visible=password.type==='text';password.type=visible?'password':'text';show.textContent=visible?'Show':'Hide';show.setAttribute('aria-label',visible?'Show password':'Hide password')});f.addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/auth/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({newPassword:new FormData(f).get('password'),token})});const x=await r.json().catch(()=>({}));if(!r.ok){m.textContent=x.message||'This reset link is invalid or expired.';return}location.assign('/auth/login')})</script>`));
});

// Better Auth must receive auth routes before JSON parsing.
app.all("/auth/sign-up/email", (_req, res) => res.sendStatus(404));
app.all("/auth/*splat", toNodeHandler(auth));
app.use(express.json());

function internalOnly(req, res, next) {
  const supplied = req.get("X-Auth-Bridge-Secret") || "";
  if (supplied.length !== bridgeSecret.length || !crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(bridgeSecret))) return res.sendStatus(403);
  next();
}

app.get("/internal/session", internalOnly, async (req, res) => {
  const session = await auth.api.getSession({ headers: fromNodeHeaders(req.headers) });
  if (!session?.user?.email) return res.sendStatus(401);
  res.json({ email: session.user.email, name: session.user.name || "", userId: session.user.id });
});

app.post("/internal/provision", internalOnly, async (req, res) => {
  const { email, password, name } = req.body || {};
  if (typeof email !== "string" || typeof password !== "string" || typeof name !== "string") return res.status(400).json({ error: "Invalid account details" });
  const response = await auth.api.signUpEmail({ body: { email, password, name }, asResponse: true });
  const data = await response.json().catch(() => ({}));
  res.status(response.status).json(data);
});

app.listen(port, "127.0.0.1", () => console.log(`AI Helper Better Auth listening on 127.0.0.1:${port}`));
