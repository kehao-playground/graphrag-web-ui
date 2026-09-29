import { render, screen } from "@testing-library/react";
import Markdown from "../tests/Markdown";

const noMarker = (raw: string) => raw;

test("headings, bold, italics, code, rules and lists render as elements", () => {
  const { container } = render(
    <Markdown
      text={"## Summary\n\nThe **key** point is *here* and `code`.\n\n---\n\n- one\n- two\n\n1. first\n2. second"}
      renderMarker={noMarker}
    />,
  );
  expect(screen.getByRole("heading", { name: "Summary" })).toBeInTheDocument();
  expect(screen.getByText("key").tagName).toBe("STRONG");
  expect(screen.getByText("here").tagName).toBe("EM");
  expect(screen.getByText("code").tagName).toBe("CODE");
  expect(container.querySelector("hr")).not.toBeNull();
  expect(container.querySelectorAll("ul > li")).toHaveLength(2);
  expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  expect(container.textContent).not.toContain("##");
  expect(container.textContent).not.toContain("**");
});

test("HTML in the answer stays text", () => {
  const { container } = render(<Markdown text={"<b>not bold</b> <script>x()</script>"} renderMarker={noMarker} />);
  expect(container.querySelector("b")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<b>not bold</b>");
});

test("[Data: …] markers go through renderMarker, also inside bold", () => {
  render(
    <Markdown
      text={"Fact [Data: Sources (3)]. **Bold [Data: Entities (1)]**"}
      renderMarker={(raw, key) => <span key={key} data-testid="marker">{raw}</span>}
    />,
  );
  expect(screen.getAllByTestId("marker").map((m) => m.textContent)).toEqual([
    "[Data: Sources (3)]", "[Data: Entities (1)]",
  ]);
});

test("an unclosed ** while streaming stays literal", () => {
  const { container } = render(<Markdown text={"partial **bo"} renderMarker={noMarker} />);
  expect(container.textContent).toBe("partial **bo");
});
