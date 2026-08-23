import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import Login from "../Login";

test("submits credentials and shows error on 401", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "unauthorized" }), { status: 401 }))
  vi.stubGlobal("fetch", fetchMock)
  // Login uses useNavigate() → without a Router it throws outright
  render(<MemoryRouter><Login /></MemoryRouter>)
  // Labels must match the implemented zh-TW copy, not /email/i
  await userEvent.type(screen.getByLabelText("電子郵件"), "a@b.c")
  await userEvent.type(screen.getByLabelText("密碼"), "wrong")
  await userEvent.click(screen.getByRole("button", { name: /登入/ }))
  await waitFor(() => expect(screen.getByText(/登入失敗/)).toBeInTheDocument())
})
