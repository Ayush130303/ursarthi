import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Alert } from "../components/common";
import api, { errorMessage } from "../lib/api";
import { useAuth } from "../lib/AuthContext";

function Field({ label, error, ...props }) {
  return (
    <label className="field">
      {label}
      <input {...props} />
      {error && <span className="field-error">{error.message}</span>}
    </label>
  );
}

export function Login() {
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm();
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState("");
  const onSubmit = async ({ email, password }) => {
    setError("");
    try {
      await login(email, password);
      navigate(location.state?.from?.pathname || "/plan", { replace: true });
    } catch (err) { setError(errorMessage(err, "Login failed.")); }
  };
  return (
    <div className="auth card">
      <h2>Login to urSarthi</h2>
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Email" type="email" autoComplete="email" error={errors.email} {...register("email", { required: "Email is required" })} />
        <Field label="Password" type="password" autoComplete="current-password" error={errors.password} {...register("password", { required: "Password is required" })} />
        <Alert>{error}</Alert>
        <button className="btn btn-primary btn-block" disabled={isSubmitting}>{isSubmitting ? "Logging in…" : "Login"}</button>
      </form>
      <p className="small"><Link to="/forgot-password">Forgot password?</Link></p>
      <p className="small">New here? <Link to="/signup">Create an account</Link></p>
    </div>
  );
}

export function Signup() {
  const { register, handleSubmit, watch, formState: { errors, isSubmitting } } = useForm();
  const { register: createAccount } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const onSubmit = async ({ name, email, password }) => {
    setError("");
    try {
      await createAccount(name, email, password);
      navigate("/preferences", { replace: true });
    } catch (err) { setError(errorMessage(err, "Sign up failed.")); }
  };
  return (
    <div className="auth card">
      <h2>Create your account</h2>
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Full name" autoComplete="name" error={errors.name} {...register("name", { required: "Name is required", minLength: { value: 2, message: "At least 2 characters" } })} />
        <Field label="Email" type="email" autoComplete="email" error={errors.email} {...register("email", { required: "Email is required", pattern: { value: /^\S+@\S+\.\S+$/, message: "Enter a valid email" } })} />
        <Field label="Password" type="password" autoComplete="new-password" error={errors.password} {...register("password", { required: "Password is required", minLength: { value: 8, message: "At least 8 characters" } })} />
        <Field label="Confirm password" type="password" autoComplete="new-password" error={errors.confirm} {...register("confirm", { validate: (v) => v === watch("password") || "Passwords do not match" })} />
        <Alert>{error}</Alert>
        <button className="btn btn-primary btn-block" disabled={isSubmitting}>{isSubmitting ? "Creating…" : "Sign up"}</button>
      </form>
      <p className="small">Already have an account? <Link to="/login">Login</Link></p>
    </div>
  );
}

export function ForgotPassword() {
  const { register, handleSubmit } = useForm();
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const onSubmit = async ({ email }) => {
    try { setMsg({ ok: (await api.post("/auth/forgot-password", { email })).data.message, err: "" }); }
    catch (err) { setMsg({ ok: "", err: errorMessage(err) }); }
  };
  return (
    <div className="auth card">
      <h2>Reset password</h2>
      <form onSubmit={handleSubmit(onSubmit)}>
        <Field label="Account email" type="email" required {...register("email")} />
        <Alert type="success">{msg.ok}</Alert>
        <Alert>{msg.err}</Alert>
        <button className="btn btn-primary btn-block">Send reset link</button>
      </form>
      <p className="small"><Link to="/login">Back to login</Link></p>
    </div>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();
  const { register, handleSubmit, watch, formState: { errors } } = useForm();
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const onSubmit = async ({ password }) => {
    try {
      setMsg({ ok: (await api.post("/auth/reset-password", { token, new_password: password })).data.message, err: "" });
      setTimeout(() => navigate("/login"), 1500);
    } catch (err) { setMsg({ ok: "", err: errorMessage(err) }); }
  };
  return (
    <div className="auth card">
      <h2>Choose a new password</h2>
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="New password" type="password" error={errors.password} {...register("password", { required: "Required", minLength: { value: 8, message: "At least 8 characters" } })} />
        <Field label="Confirm password" type="password" error={errors.confirm} {...register("confirm", { validate: (v) => v === watch("password") || "Passwords do not match" })} />
        {!token && <Alert>This link has no reset token. Request a new link.</Alert>}
        <Alert type="success">{msg.ok}</Alert>
        <Alert>{msg.err}</Alert>
        <button className="btn btn-primary btn-block" disabled={!token}>Update password</button>
      </form>
    </div>
  );
}
