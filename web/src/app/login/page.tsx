import { LoginForm } from "./LoginForm";

export const metadata = { title: "Sign in | CCPO" };

export default function LoginPage() {
  return (
    <main className="mx-auto w-full max-w-sm flex-1 px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight text-neutral-900">Sign in</h1>
      <p className="mt-2 text-sm text-neutral-600">
        Admin access only, for now (Part F §F.6). Enter the admin email to get a sign-in link.
      </p>
      <div className="mt-6">
        <LoginForm />
      </div>
    </main>
  );
}
