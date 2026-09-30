import { useState, type FormEvent } from "react";
import { registrationError } from "../auth";
import { MAX_USERNAME_LENGTH, MIN_PASSWORD_LENGTH } from "../protocol";
import { ProjectFooter, translateUi, useLanguage } from "../i18n";

type Props = {
  allowRegistration: boolean;
  onSubmit: (mode: "login" | "register", username: string, password: string) => Promise<string | null>;
};

export function AuthScreen({ allowRegistration, onSubmit }: Props) {
  const { language } = useLanguage();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeated, setRepeated] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const register = mode === "register";
  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setError("");
    setRepeated("");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const name = username.trim();
    if (register) {
      // The repeat field never leaves the browser: validate before requesting.
      const message = registrationError(name, password, repeated);
      if (message) {
        setError(message);
        return;
      }
    } else if (!name || !password) {
      setError(translateUi("请填写用户名和密码", language));
      return;
    }
    setBusy(true);
    setError("");
    const message = await onSubmit(mode, name, password);
    if (message) setError(message);
    setBusy(false);
  };

  const ready = register
    ? Boolean(username.trim() && password && repeated)
    : Boolean(username.trim() && password);

  return <main className="join-shell">
    <form className="join-card" onSubmit={submit}>
      <h1>AI RP Engine</h1>
      <p className="muted">{register ? "创建一个新账号" : "使用账号登录"}</p>
      <label htmlFor="username">用户名</label>
      <input id="username" value={username} maxLength={MAX_USERNAME_LENGTH}
        onChange={(event) => setUsername(event.target.value)}
        autoComplete="username" autoFocus />
      <label htmlFor="password">密码</label>
      <input id="password" value={password} type="password"
        minLength={register ? MIN_PASSWORD_LENGTH : undefined}
        onChange={(event) => setPassword(event.target.value)}
        autoComplete={register ? "new-password" : "current-password"} />
      {register && <>
        <label htmlFor="repeat-password">重复密码</label>
        <input id="repeat-password" value={repeated} type="password"
          minLength={MIN_PASSWORD_LENGTH}
          onChange={(event) => setRepeated(event.target.value)}
          autoComplete="new-password" />
      </>}
      <button type="submit" disabled={busy || !ready}>
        {busy ? "处理中…" : register ? "注册" : "登录"}
      </button>
      {allowRegistration && <button type="button" className="link-button"
        onClick={() => switchMode(register ? "login" : "register")}>
        {register ? "已有账号？登录" : "没有账号？注册"}
      </button>}
      {!allowRegistration && <p className="muted">注册当前已关闭，请使用已有账号登录。</p>}
      {error && <p className="error-text">{error}</p>}
    </form>
    <ProjectFooter />
  </main>;
}
