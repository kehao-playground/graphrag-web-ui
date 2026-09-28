import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ErrorBoundary from "../ErrorBoundary";
import Layout from "../Layout";
import { useAuth } from "../../stores/auth";

// React logs every caught render error to console.error; silence it so the
// expected throws below do not bury real failures in the output.
beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); });

let broken = true;
function Flaky() {
  if (broken) throw new Error("chunk load failed");
  return <p>recovered</p>;
}

it("shows the error with retry instead of unmounting, and retry re-renders", async () => {
  broken = true;
  const onReset = vi.fn();
  render(<ErrorBoundary onReset={onReset}><Flaky /></ErrorBoundary>);
  expect(screen.getByText("此區塊載入失敗")).toBeInTheDocument();
  expect(screen.getByText("chunk load failed")).toBeInTheDocument();
  broken = false;
  await userEvent.setup().click(screen.getByRole("button", { name: /^重\s?試$/ }));
  expect(screen.getByText("recovered")).toBeInTheDocument();
  expect(onReset).toHaveBeenCalledTimes(1);
});

it("clears the error when resetKey changes", () => {
  broken = true;
  const { rerender } = render(<ErrorBoundary resetKey="a"><Flaky /></ErrorBoundary>);
  expect(screen.getByText("此區塊載入失敗")).toBeInTheDocument();
  broken = false;
  rerender(<ErrorBoundary resetKey="b"><Flaky /></ErrorBoundary>);
  expect(screen.getByText("recovered")).toBeInTheDocument();
});

function Boom(): never { throw new Error("pane crashed"); }
function GoHome() {
  const navigate = useNavigate();
  const [clicked, setClicked] = useState(false);
  return clicked ? null : (
    <button type="button" onClick={() => { setClicked(true); navigate("/ok"); }}>go</button>
  );
}

it("a crashing pane keeps the navigation and recovers on route change", async () => {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [], permissions: [],
            is_active: true, must_change_password: false },
  });
  render(
    <MemoryRouter initialEntries={["/boom"]}>
      <GoHome />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/boom" element={<Boom />} />
          <Route path="/ok" element={<p>fine</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("pane crashed")).toBeInTheDocument();
  expect(screen.getByText("專案")).toBeInTheDocument(); // nav survived
  await userEvent.setup().click(screen.getByRole("button", { name: "go" }));
  expect(screen.getByText("fine")).toBeInTheDocument();
});
