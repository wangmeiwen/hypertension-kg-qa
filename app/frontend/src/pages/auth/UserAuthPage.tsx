import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import styles from "./UserAuthPage.module.css";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { useUserAuthStore } from "../../features/user_auth/model/useUserAuthStore";
import { hasUserToken, loginUser, registerUser } from "../../shared/api/userAuthClient";
import { queryClient } from "../../shared/queries/queryClient";
import { Button, Card, InlineNotice, Input } from "../../shared/ui/primitives";

interface UserAuthPageProps {
  mode: "login" | "register";
}

export default function UserAuthPage({ mode }: UserAuthPageProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const isAuthenticated = useUserAuthStore((state) => state.isAuthenticated);
  const setUser = useUserAuthStore((state) => state.setUser);
  const setUserId = useSessionStore((state) => state.setUserId);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [validationError, setValidationError] = useState("");
  const authMutation = useMutation({
    mutationFn: () =>
      mode === "login" ? loginUser(username, password) : registerUser(username, password),
    onSuccess: (user) => {
      queryClient.clear();
      setUser(user);
      setUserId(user.id);
      const requestedPath = (location.state as { from?: string } | null)?.from;
      navigate(requestedPath === "/chat" ? requestedPath : "/chat", { replace: true });
    },
  });

  if (isAuthenticated && hasUserToken()) return <Navigate to="/chat" replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setValidationError("");
    if (mode === "register" && password !== confirmPassword) {
      setValidationError("两次输入的密码不一致");
      return;
    }
    authMutation.mutate();
  };

  const requestError = authMutation.error as { message?: unknown } | null;
  const errorMessage =
    validationError ||
    (typeof requestError?.message === "string" ? requestError.message : "账号操作失败，请重试");

  return (
    <main className={styles.page}>
      <Card className={styles.card} title={mode === "login" ? "用户登录" : "创建账号"}>
        <p className={styles.description}>
          {mode === "login" ? "登录后访问自己的问答会话。" : "注册后会自动登录并创建独立会话空间。"}
        </p>
        <form className={styles.form} onSubmit={submit}>
          <label>
            用户名
            <Input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9_]+"
              required
            />
          </label>
          <label>
            密码
            <Input
              name="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={8}
              maxLength={128}
              required
            />
          </label>
          {mode === "register" ? (
            <label>
              确认密码
              <Input
                name="confirmPassword"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                required
              />
            </label>
          ) : null}
          {validationError || authMutation.isError ? (
            <InlineNotice tone="error">{errorMessage}</InlineNotice>
          ) : null}
          <Button type="submit" disabled={authMutation.isPending}>
            {authMutation.isPending ? "提交中..." : mode === "login" ? "登录" : "注册并登录"}
          </Button>
        </form>
        <div className={styles.switchLink}>
          {mode === "login" ? "还没有账号？" : "已经有账号？"}
          <Link to={mode === "login" ? "/register" : "/login"}>
            {mode === "login" ? "去注册" : "去登录"}
          </Link>
        </div>
      </Card>
    </main>
  );
}
