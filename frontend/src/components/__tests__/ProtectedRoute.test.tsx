import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { useAuth } from "../../stores/auth";
import ProtectedRoute from "../ProtectedRoute";

afterEach(cleanup);

// The auth gate in front of every page but /login (R2-35).
function mount() {
  render(
    <MemoryRouter initialEntries={["/projects"]}>
      <Routes>
        <Route path="/login" element={<p>login page</p>} />
        <Route path="/projects" element={<ProtectedRoute><p>project list</p></ProtectedRoute>} />
      </Routes>
    </MemoryRouter>,
  );
}

it("shows a spinner and neither page while the session is being restored", () => {
  useAuth.setState({ bootstrapping: true, user: null });
  mount();
  expect(document.querySelector(".ant-spin")).not.toBeNull();
  expect(screen.queryByText("project list")).toBeNull();
  expect(screen.queryByText("login page")).toBeNull();
});

it("sends a signed-out visitor to /login", () => {
  useAuth.setState({ bootstrapping: false, user: null });
  mount();
  expect(screen.getByText("login page")).toBeInTheDocument();
  expect(screen.queryByText("project list")).toBeNull();
});

it("renders the page for a signed-in user", () => {
  useAuth.setState({
    bootstrapping: false,
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [], permissions: [],
            is_active: true, must_change_password: false },
  });
  mount();
  expect(screen.getByText("project list")).toBeInTheDocument();
});
