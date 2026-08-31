import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { i18n } from "../../i18n";
import { useAuth } from "../../stores/auth";
import Layout from "../Layout";

function mount() {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [],
            permissions: ["users:manage"],
            is_active: true, must_change_password: false },
  });
  return render(<MemoryRouter><Layout /></MemoryRouter>);
}

afterEach(async () => { await i18n.changeLanguage("zh-TW"); });

it("language dropdown toggles nav copy and documentElement.lang", async () => {
  mount();
  expect(screen.getByText("專案")).toBeInTheDocument();
  // the users:manage atom (not a role name) is what shows the admin entry
  expect(screen.getByText("管理者 — 使用者")).toBeInTheDocument();
  // the selector is a Select dropdown at the Sider bottom: open it, then
  // pick the option — the closed control only shows the current language
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "語言" }));
  await user.click(await screen.findByText("English"));
  expect(screen.getByText("Projects")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("en-US");
  await user.click(screen.getByRole("combobox", { name: "Language" }));
  await user.click(await screen.findByText("中文"));
  expect(screen.getByText("專案")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("zh-TW");
});

it("hides the admin nav entry without the users:manage atom", () => {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [],
            permissions: [], is_active: true, must_change_password: false },
  });
  render(<MemoryRouter><Layout /></MemoryRouter>);
  expect(screen.getByText("專案")).toBeInTheDocument();
  expect(screen.queryByText("管理者 — 使用者")).toBeNull();
});
