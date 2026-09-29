import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { useAuth } from "../../stores/auth";
import RequirePermission from "../RequirePermission";

function mount(permissions: string[]) {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [], permissions,
            is_active: true, must_change_password: false },
  });
  render(
    <MemoryRouter initialEntries={["/admin/users"]}>
      <Routes>
        <Route path="/admin/users" element={
          <RequirePermission atom="users:manage"><p>admin body</p></RequirePermission>
        } />
        <Route path="/projects" element={<p>project list</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

it("renders the page for a holder of the atom", () => {
  mount(["users:manage"]);
  expect(screen.getByText("admin body")).toBeInTheDocument();
});

it("renders a 403 with a way back instead of the page (R4-37)", async () => {
  mount([]);
  expect(screen.queryByText("admin body")).toBeNull();
  expect(screen.getByText("沒有權限檢視此頁面")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "回到專案列表" }));
  expect(screen.getByText("project list")).toBeInTheDocument();
});
