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
    user: { id: "u1", email: "a@b.c", display_name: "A", role: "admin",
            is_active: true, must_change_password: false },
  });
  return render(<MemoryRouter><Layout /></MemoryRouter>);
}

afterEach(async () => { await i18n.changeLanguage("zh-TW"); });

it("switcher toggles nav copy and documentElement.lang", async () => {
  mount();
  expect(screen.getByText("專案")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByText("English"));
  expect(screen.getByText("Projects")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("en-US");
  await userEvent.setup().click(screen.getByText("中文"));
  expect(screen.getByText("專案")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("zh-TW");
});
