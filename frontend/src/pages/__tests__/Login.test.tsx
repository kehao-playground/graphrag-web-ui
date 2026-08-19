import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import Login from "../Login";

test("submits credentials and shows error on 401", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "unauthorized" }), { status: 401 }))
  vi.stubGlobal("fetch", fetchMock)
  // Login 內用 useNavigate() → 沒有 Router 會直接 throw
  render(<MemoryRouter><Login /></MemoryRouter>)
  // label 要對上實作的中文文案,不是 /email/i
  await userEvent.type(screen.getByLabelText("電子郵件"), "a@b.c")
  await userEvent.type(screen.getByLabelText("密碼"), "wrong")
  await userEvent.click(screen.getByRole("button", { name: /登入/ }))
  await waitFor(() => expect(screen.getByText(/登入失敗/)).toBeInTheDocument())
})
